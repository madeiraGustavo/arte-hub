"""
Daily send wave — loads recipients from Excel and sends first emails
via SMTP (fast, same mechanism as Google Apps Script).

Architecture:
  - Each account gets its own SMTPSender instance
  - Up to `concurrent_accounts` accounts send in parallel (default 10)
  - Each account sends its assigned batch sequentially with 0.5–1.5s delay
  - Total time: ~1-2 min per account; 100 accounts at 10 parallel = ~10-20 min

Playwright is used ONLY for:
  - First-ever login (to establish session, handle 2FA/captcha)
  - First-login inbox cleanup
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Optional

from .account_manager import AccountManager
from .config import AppConfig
from .database import Database
from .excel_reader import load_recipients
from .gmail_automation import GmailAutomation
from .message_loader import MessageLoader
from .smtp_sender import SMTPSender
from .telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class SendWave:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        account_manager: AccountManager,
        message_loader: MessageLoader,
        notifier: Optional[TelegramNotifier] = None,
    ):
        self.db = db
        self.config = config
        self.acct_mgr = account_manager
        self.msg = message_loader
        self.notifier = notifier

    async def load_today_recipients(self) -> int:
        records = load_recipients(self.config.paths.recipients_excel)
        for email, subject, lang in records:
            await self.db.upsert_recipient(email, subject, lang)
        return len(records)

    async def run(self) -> dict:
        """
        Full sending wave.
        Returns stats dict.
        """
        await self.db.reset_daily_counters()

        loaded = await self.load_today_recipients()
        logger.info("Recipients loaded: %d", loaded)

        sem = asyncio.Semaphore(self.config.limits.concurrent_accounts)
        first_sent_total = 0
        errors_total = 0
        lock = asyncio.Lock()

        async def _send_account_batch(account: dict, recipients: list) -> None:
            nonlocal first_sent_total, errors_total
            async with sem:
                acct_email = account["email"]

                # ── Step 1: First-login via browser (always required) ────────
                # Google blocks SMTP with 534 until the account logs in via
                # web browser at least once from this IP. We must do this
                # even if an app_password was pre-supplied in accounts.txt —
                # that 3rd field is NOT a Google App Password, it needs to be
                # generated via the browser after login.
                if not account.get("first_login_completed"):
                    await self._do_first_login(account)
                    row = await self.db.get_account(acct_email)
                    if row:
                        account = dict(row)
                    if not account.get("first_login_completed"):
                        logger.warning("[%s] First login failed — skipping", acct_email)
                        async with lock:
                            errors_total += len(recipients)
                        return

                # ── Step 2: Send via SMTP ─────────────────────────────────────
                # Use App Password if available (required when 2FA is on),
                # fall back to main password if App Password was not generated
                smtp_pwd = await self.db.get_smtp_password(acct_email)
                smtp = SMTPSender(
                    account_email=acct_email,
                    password=smtp_pwd or account["password"],
                )

                sent_emails: list[str] = []
                failed_emails: list[str] = []

                async def _on_progress(acc_email: str, to: str, ok: bool) -> None:
                    if ok:
                        await self.db.mark_first_sent(to, acc_email)
                        await self.db.increment_sent(acc_email)
                        sent_emails.append(to)
                    else:
                        await self.db.mark_bad(to)
                        failed_emails.append(to)

                # Build per-recipient payloads
                payloads = []
                for rec in recipients:
                    lang = rec.get("language", "en")
                    product = rec.get("subject", "")   # Excel col B = product name
                    body_html = self.msg.get_first_message(lang, product=product)
                    subject = self.msg.get_first_subject(lang, product)
                    payloads.append({
                        "email": rec["email"],
                        "subject": subject,
                        "body_html": body_html,
                    })

                sent_count, failed_count = await smtp.send_batch(
                    recipients=payloads,
                    delay_range=(
                        self.config.limits.send_delay_min,
                        self.config.limits.send_delay_max,
                    ),
                    progress_callback=_on_progress,
                )

                async with lock:
                    first_sent_total += sent_count
                    errors_total += failed_count

                logger.info(
                    "[%s] Wave: sent=%d failed=%d",
                    acct_email, sent_count, failed_count,
                )

        # Build task list from account slots
        tasks = []
        async for account, batch in self.acct_mgr.account_slot_generator():
            tasks.append(_send_account_batch(account, batch))

        if not tasks:
            logger.info("No tasks to run (no active accounts or no pending recipients)")
            return {"first_sent": 0, "errors": 0}

        logger.info("Starting send wave: %d account batches, concurrency=%d",
                    len(tasks), self.config.limits.concurrent_accounts)

        await asyncio.gather(*tasks)

        # Notify if running low on accounts
        active_rows = await self.db.get_active_accounts()
        if self.notifier and len(active_rows) <= 5:
            await self.notifier.alert_accounts_low(len(active_rows))

        return {"first_sent": first_sent_total, "errors": errors_total}

    async def _do_first_login(self, account: dict) -> None:
        """
        Use Playwright for the very first login only:
        - Handles 2FA / captcha
        - Clears inbox
        - Marks first_login_completed = 1 in DB
        After this, SMTP is used for all sends.
        """
        automation = GmailAutomation(
            account=account,
            config=self.config,
            db=self.db,
            telegram_notifier=self.notifier,
        )
        try:
            await automation.start()
            ok = await automation.login()
            if not ok:
                logger.error("[%s] First login via browser failed", account["email"])
        except Exception as exc:
            logger.error("[%s] First login error: %s", account["email"], exc)
        finally:
            await automation.stop()
