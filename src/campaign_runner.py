"""
Campaign orchestration — the heart of the system.

Three main tasks that compose the full campaign loop:

  1. run_daily_blast()
     • Reset daily counters
     • Load pending recipients from DB
     • Distribute among available accounts (round-robin, respect limits)
     • Login each account, send first emails, record progress
     • Detect & handle errors (bad address, account blocked)

  2. run_reply_check()
     • For each account that has sent first emails
     • Login and scan inbox for replies from our recipient set
     • Filter out auto-replies and bounces
     • For genuine replies: generate unique link → send second email

  3. run_maintenance()
     • Purge expired exhausted accounts
     • Reset daily counts
     • Send daily stats to Telegram

Tasks are intentionally sequential within a single account to avoid
Gmail detecting concurrent sessions; across accounts they run in a
limited asyncio semaphore pool.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import date
from typing import List, Optional, Set

from playwright.async_api import async_playwright

from .config import Config
from .database import (
    db_log,
    get_account,
    get_active_accounts,
    get_daily_stats,
    get_first_sent_recipients,
    get_pending_recipients,
    get_recipient_by_email,
    increment_sent,
    mark_account_status,
    mark_bad,
    mark_first_sent,
    mark_replied,
    mark_second_sent,
    purge_expired_accounts,
    reset_daily_counts,
    update_account_field,
)
from .gmail_automation import (
    AccountSendBlockedError,
    BadAddressError,
    GmailBlockedError,
    GmailCaptchaError,
    GmailLoginError,
    compose_and_send,
    create_browser_context,
    delete_all_inbox,
    check_replies,
    is_autoreply,
    login_gmail,
)
from .telegram_notifier import (
    generate_unique_link,
    notify_blocked,
    notify_daily_stats,
    notify_exhausted,
    send_telegram_message,
)
from .template_engine import render_first_email, render_second_email

log = logging.getLogger(__name__)


# ─────────────────────────── helpers ────────────────────────────────────────

def _pick_accounts_for_sending(config: Config, db_path: str, needed: int):
    """
    Return active accounts that still have daily capacity, sorted by
    total_sent ascending (prefer least-used accounts).
    """
    accounts = get_active_accounts(db_path)
    today = date.today()
    eligible = []
    for acc in accounts:
        if acc.last_sent_date and acc.last_sent_date == today:
            remaining = config.campaign.daily_limit - acc.daily_sent
        else:
            remaining = config.campaign.daily_limit
        if remaining > 0:
            eligible.append((acc, remaining))
    return eligible


def _distribute_recipients(eligible_accounts, recipients):
    """
    Assign recipients to accounts in round-robin respecting remaining daily capacity.
    Returns dict: {account_email: [recipient, ...]}
    """
    assignments: dict[str, list] = {}
    queues = list(eligible_accounts)  # [(account, remaining), ...]
    random.shuffle(queues)             # randomise starting account

    rec_iter = iter(recipients)
    idx = 0
    exhausted_flags: Set[int] = set()

    for rec in rec_iter:
        if len(exhausted_flags) == len(queues):
            log.warning("All accounts at daily limit — %d recipients unassigned", 1)
            break
        # Find the next account with capacity
        start = idx
        while True:
            if idx not in exhausted_flags:
                acc, rem = queues[idx]
                if rec not in assignments:
                    assignments.setdefault(acc.email, [])
                assignments[acc.email].append(rec)
                queues[idx] = (acc, rem - 1)
                if rem - 1 <= 0:
                    exhausted_flags.add(idx)
                idx = (idx + 1) % len(queues)
                break
            idx = (idx + 1) % len(queues)
            if idx == start:
                break

    return assignments


# ─────────────────────────── per-account send task ──────────────────────────

async def _send_batch(account_email: str, recipients, config: Config, db_path: str, semaphore: asyncio.Semaphore) -> None:
    """Login once and send all assigned first emails for one account."""
    async with semaphore:
        acc = get_account(db_path, account_email)
        if not acc:
            return

        try:
            async with async_playwright() as pw:
                context = await create_browser_context(pw, acc.email, config)
                try:
                    page = await login_gmail(context, acc.email, acc.password, acc.two_fa_key, config)
                except GmailCaptchaError as exc:
                    log.error("CAPTCHA for %s: %s", acc.email, exc)
                    mark_account_status(db_path, acc.email, "blocked")
                    await notify_blocked(config.telegram.bot_token, config.telegram.chat_id, acc.email, "captcha")
                    db_log(db_path, "ERROR", str(exc), account=acc.email)
                    return
                except GmailBlockedError as exc:
                    log.error("BLOCKED: %s", exc)
                    mark_account_status(db_path, acc.email, "blocked")
                    await notify_blocked(config.telegram.bot_token, config.telegram.chat_id, acc.email, "blocked by Google")
                    db_log(db_path, "ERROR", str(exc), account=acc.email)
                    return
                except GmailLoginError as exc:
                    log.error("Login failed for %s: %s", acc.email, exc)
                    mark_account_status(db_path, acc.email, "blocked")
                    db_log(db_path, "ERROR", str(exc), account=acc.email)
                    return

                # First-time inbox cleanup
                if not acc.first_login_completed:
                    try:
                        await delete_all_inbox(page, acc.email)
                        update_account_field(db_path, acc.email, first_login_completed=1)
                        log.info("%s: first-login inbox cleanup completed", acc.email)
                        db_log(db_path, "INFO", "First-login inbox cleanup completed", account=acc.email)
                    except Exception as exc:
                        log.warning("%s: cleanup failed (non-fatal): %s", acc.email, exc)

                # Send emails
                for rec in recipients:
                    await _send_one(page, acc, rec, config, db_path)
                    delay = random.uniform(config.campaign.min_send_delay, config.campaign.max_send_delay)
                    await asyncio.sleep(delay)

                    # Refresh page occasionally to avoid session timeout
                    if random.random() < 0.05:
                        await page.reload(wait_until="domcontentloaded", timeout=20000)
                        await asyncio.sleep(1)

                await context.close()

        except Exception as exc:
            log.error("Unexpected error in _send_batch for %s: %s", account_email, exc)
            db_log(db_path, "ERROR", f"Batch send error: {exc}", account=account_email)


async def _send_one(page, acc, rec, config: Config, db_path: str) -> None:
    """Send a single first email. Updates DB on success or failure."""
    try:
        subject, body = render_first_email(
            config.paths.templates_dir,
            rec.language,
            rec.email,
            rec.subject,
        )
        await compose_and_send(page, rec.email, subject, body, acc.email)

        mark_first_sent(db_path, rec.email, acc.email)
        increment_sent(db_path, acc.email)
        db_log(db_path, "INFO", f"First email sent to {rec.email}", account=acc.email, recipient=rec.email)

        # Check if account reached total limit
        updated_acc = get_account(db_path, acc.email)
        if updated_acc and updated_acc.total_sent >= config.campaign.total_limit:
            mark_account_status(db_path, acc.email, "exhausted")
            await notify_exhausted(config.telegram.bot_token, config.telegram.chat_id, acc.email)
            log.info("%s reached total send limit — marked exhausted", acc.email)

    except BadAddressError as exc:
        log.warning(str(exc))
        mark_bad(db_path, rec.email, str(exc))
        db_log(db_path, "WARNING", str(exc), account=acc.email, recipient=rec.email)

    except AccountSendBlockedError as exc:
        log.error(str(exc))
        mark_account_status(db_path, acc.email, "blocked")
        await notify_blocked(config.telegram.bot_token, config.telegram.chat_id, acc.email, str(exc))
        db_log(db_path, "ERROR", str(exc), account=acc.email, recipient=rec.email)
        raise  # abort batch for this account

    except Exception as exc:
        log.error("Send error %s → %s: %s", acc.email, rec.email, exc)
        db_log(db_path, "ERROR", f"Send failed: {exc}", account=acc.email, recipient=rec.email)


# ─────────────────────────── reply check task ────────────────────────────────

async def _check_replies_for_account(acc_email: str, config: Config, db_path: str, semaphore: asyncio.Semaphore) -> None:
    """Login, check inbox, process genuine replies."""
    async with semaphore:
        acc = get_account(db_path, acc_email)
        if not acc or acc.status != "active":
            return

        # Which recipients were sent by this account and are awaiting reply?
        all_waiting = get_first_sent_recipients(db_path)
        mine = [r for r in all_waiting if r.assigned_account == acc_email]
        if not mine:
            return

        mine_emails = {r.email.lower() for r in mine}

        try:
            async with async_playwright() as pw:
                context = await create_browser_context(pw, acc.email, config)
                try:
                    page = await login_gmail(context, acc.email, acc.password, acc.two_fa_key, config)
                except (GmailLoginError, GmailBlockedError, GmailCaptchaError) as exc:
                    log.error("Reply check login failed %s: %s", acc.email, exc)
                    db_log(db_path, "ERROR", str(exc), account=acc.email)
                    return

                repliers = await check_replies(page, acc.email, mine_emails)
                log.info("%s: found %d replies from our recipients", acc.email, len(repliers))

                for replier_email in repliers:
                    await _process_reply(page, acc, replier_email, config, db_path)
                    await asyncio.sleep(random.uniform(2, 4))

                await context.close()

        except Exception as exc:
            log.error("Reply check error for %s: %s", acc_email, exc)
            db_log(db_path, "ERROR", f"Reply check error: {exc}", account=acc_email)


async def _process_reply(page, acc, replier_email: str, config: Config, db_path: str) -> None:
    """For a genuine reply: generate link, send second email, mark done."""
    rec = get_recipient_by_email(db_path, replier_email)
    if not rec:
        log.warning("Reply from %s but not in recipients table", replier_email)
        return

    if rec.status in ("second_sent", "bad"):
        log.debug("Skipping already-processed reply from %s", replier_email)
        return

    # Generate unique link
    unique_link = await generate_unique_link(
        config.telegram.link_bot_token,
        config.telegram.link_bot_api_url,
        replier_email,
    )
    if not unique_link:
        log.warning("Could not generate link for %s — skipping second email", replier_email)
        return

    # Render and send second email
    try:
        subject, body = render_second_email(
            config.paths.templates_dir,
            rec.language,
            rec.email,
            rec.subject,
            unique_link,
        )
        await compose_and_send(page, rec.email, subject, body, acc.email)
        mark_replied(db_path, rec.email)
        mark_second_sent(db_path, rec.email, unique_link)
        increment_sent(db_path, acc.email)
        db_log(db_path, "INFO", f"Second email sent to {rec.email} with link {unique_link}", account=acc.email, recipient=rec.email)
        log.info("Second email sent to %s", rec.email)
    except Exception as exc:
        log.error("Failed sending second email to %s: %s", rec.email, exc)
        db_log(db_path, "ERROR", f"Second email failed: {exc}", account=acc.email, recipient=rec.email)


# ─────────────────────────── public API ──────────────────────────────────────

async def run_daily_blast(config: Config) -> None:
    """Main daily send wave."""
    db_path = config.paths.db_path
    reset_daily_counts(db_path)

    pending = get_pending_recipients(db_path, limit=10000)
    if not pending:
        log.info("No pending recipients — daily blast skipped")
        return

    eligible = _pick_accounts_for_sending(config, db_path, len(pending))
    if not eligible:
        await send_telegram_message(
            config.telegram.bot_token,
            config.telegram.chat_id,
            "⚠️ <b>No active accounts with daily capacity available!</b>\nPlease add new accounts.",
        )
        log.error("No eligible accounts for daily blast")
        return

    assignments = _distribute_recipients(eligible, pending)
    log.info(
        "Daily blast: %d recipients across %d accounts",
        sum(len(v) for v in assignments.values()),
        len(assignments),
    )

    semaphore = asyncio.Semaphore(config.campaign.max_concurrent_senders)
    tasks = [
        asyncio.create_task(_send_batch(acc_email, recs, config, db_path, semaphore))
        for acc_email, recs in assignments.items()
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("Daily blast complete")


async def run_reply_check(config: Config) -> None:
    """Check inboxes for replies and send second emails."""
    db_path = config.paths.db_path
    accounts = get_active_accounts(db_path)
    if not accounts:
        log.info("No active accounts for reply check")
        return

    semaphore = asyncio.Semaphore(config.campaign.max_concurrent_senders)
    tasks = [
        asyncio.create_task(_check_replies_for_account(acc.email, config, db_path, semaphore))
        for acc in accounts
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("Reply check complete")


async def run_maintenance(config: Config) -> None:
    """Purge stale accounts and send daily stats."""
    db_path = config.paths.db_path

    removed = purge_expired_accounts(db_path, config.campaign.account_expiry_days)
    if removed:
        log.info("Purged %d expired accounts: %s", len(removed), ", ".join(removed))
        db_log(db_path, "INFO", f"Purged {len(removed)} expired accounts: {', '.join(removed)}")

    stats = get_daily_stats(db_path)
    await notify_daily_stats(config.telegram.bot_token, config.telegram.chat_id, stats)
    log.info("Maintenance complete. Stats: %s", stats)
