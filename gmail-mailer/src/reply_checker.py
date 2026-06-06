"""
Reply checker — IMAP-first, SMTP for second emails.

Flow:
  1. For each active account: check replies via IMAP (fast, no browser)
  2. For each real reply: generate link, send second email via SMTP
  3. Browser (Playwright) is NOT used here at all — only IMAP + SMTP
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from .config import AppConfig
from .database import Database
from .imap_checker import check_replies_imap
from .message_loader import MessageLoader
from .smtp_sender import SMTPSender
from .telegram_notifier import LinkGenerator, TelegramNotifier

logger = logging.getLogger(__name__)


class ReplyChecker:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        message_loader: MessageLoader,
        notifier: Optional[TelegramNotifier] = None,
        link_generator: Optional[LinkGenerator] = None,
    ):
        self.db = db
        self.config = config
        self.msg = message_loader
        self.notifier = notifier
        self.link_gen = link_generator

    async def run(self) -> dict:
        """Check replies and send second emails. No browser needed."""
        accounts = await self.db.get_active_accounts()
        if not accounts:
            return {}

        total_replies = 0
        total_second_sent = 0
        sem = asyncio.Semaphore(self.config.limits.concurrent_accounts)
        lock = asyncio.Lock()

        async def _process_account(acc):
            nonlocal total_replies, total_second_sent
            async with sem:
                acct_email = acc["email"]

                # Get this account's first_sent recipients
                all_first_sent = await self.db.get_first_sent_recipients()
                my_recipients = [
                    r for r in all_first_sent if r["assigned_account"] == acct_email
                ]
                if not my_recipients:
                    return

                expected = [r["email"] for r in my_recipients]

                # ── IMAP reply check (fast, no browser) ──────────────────────
                smtp_pwd = await self.db.get_smtp_password(acct_email)
                imap_password = smtp_pwd or acc["password"]

                replied = await check_replies_imap(
                    account_email=acct_email,
                    password=imap_password,
                    expected_senders=expected,
                    since_days=7,
                )

                if not replied:
                    logger.debug("[%s] No replies found via IMAP", acct_email)
                    return

                async with lock:
                    total_replies += len(replied)

                # ── Send second emails via SMTP ───────────────────────────────
                smtp_pwd = await self.db.get_smtp_password(acct_email)
                smtp = SMTPSender(
                    account_email=acct_email,
                    password=smtp_pwd or acc["password"],
                )
                smtp_ok = await smtp.connect()
                if not smtp_ok:
                    logger.warning("[%s] SMTP connect failed for second emails", acct_email)
                    return

                try:
                    for sender_email in replied:
                        rec = next(
                            (r for r in my_recipients if r["email"] == sender_email),
                            None,
                        )
                        if not rec:
                            continue

                        # Mark replied immediately so we don't process twice
                        await self.db.mark_replied(sender_email)

                        # Generate unique link
                        link = ""
                        if self.link_gen:
                            link = await self.link_gen.generate_link(sender_email) or ""
                        if not link:
                            logger.warning(
                                "[%s] No link generated for %s", acct_email, sender_email
                            )

                        lang = rec.get("language", "en")
                        subject_original = rec.get("subject", "")   # product name
                        body_html = self.msg.get_second_message(lang, link, product=subject_original)
                        subject = self.msg.get_second_subject(lang, subject_original)

                        ok = await smtp.send_one(sender_email, subject, body_html)

                        if ok:
                            await self.db.mark_second_sent(sender_email, link)
                            await self.db.increment_sent(acct_email)
                            async with lock:
                                total_second_sent += 1
                            logger.info(
                                "[%s] Second email → %s (link=%s…)",
                                acct_email, sender_email, link[:30],
                            )
                        else:
                            logger.warning(
                                "[%s] Second email failed → %s", acct_email, sender_email
                            )

                        await asyncio.sleep(0.5)

                finally:
                    await smtp.disconnect()

        tasks = [_process_account(acc) for acc in accounts]
        await asyncio.gather(*tasks)

        return {"replies_found": total_replies, "second_sent": total_second_sent}
