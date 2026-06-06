"""
Reply checker — orchestrates checking replies across all accounts
and dispatching second emails.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from .config import AppConfig
from .database import Database
from .gmail_automation import GmailAutomation
from .message_loader import MessageLoader
from .telegram_notifier import LinkGenerator, TelegramNotifier

logger = logging.getLogger(__name__)


class ReplyChecker:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        message_loader: "MessageLoader",
        notifier: Optional[TelegramNotifier] = None,
        link_generator: Optional[LinkGenerator] = None,
    ):
        self.db = db
        self.config = config
        self.msg = message_loader
        self.notifier = notifier
        self.link_gen = link_generator

    async def run(self) -> dict:
        """
        For every active account, check replies and send second emails.
        Returns summary stats.
        """
        accounts = await self.db.get_active_accounts()
        if not accounts:
            logger.info("No active accounts to check for replies")
            return {}

        total_replies = 0
        total_second_sent = 0
        sem = asyncio.Semaphore(self.config.limits.concurrent_accounts)

        async def _check_account(acc):
            nonlocal total_replies, total_second_sent
            async with sem:
                automation = GmailAutomation(
                    account=dict(acc),
                    config=self.config,
                    db=self.db,
                    telegram_notifier=self.notifier,
                )
                try:
                    await automation.start()
                    ok = await automation.login()
                    if not ok:
                        return

                    # Get recipients assigned to this account that are in first_sent status
                    recipients = await self.db.get_first_sent_recipients()
                    acct_email = acc["email"]
                    my_recipients = [
                        r for r in recipients if r["assigned_account"] == acct_email
                    ]

                    if not my_recipients:
                        return

                    expected_senders = [r["email"] for r in my_recipients]
                    replies = await automation.check_replies(expected_senders)
                    total_replies += len(replies)

                    for sender_email in replies:
                        # Find the recipient record to know language
                        rec = next(
                            (r for r in my_recipients if r["email"] == sender_email), None
                        )
                        if not rec:
                            continue

                        await self.db.mark_replied(sender_email)
                        await self._send_second_email(automation, acc, rec)
                        total_second_sent += 1

                except Exception as exc:
                    logger.error("[%s] Reply check failed: %s", acc["email"], exc)
                finally:
                    await automation.stop()

        tasks = [_check_account(acc) for acc in accounts]
        await asyncio.gather(*tasks)

        return {"replies_found": total_replies, "second_sent": total_second_sent}

    async def _send_second_email(
        self,
        automation: GmailAutomation,
        account: dict,
        recipient: dict,
    ) -> None:
        sender_email = recipient["email"]
        lang = recipient.get("language", "en")
        subject_original = recipient.get("subject", "")

        # Generate unique link via Telegram bot
        link = None
        if self.link_gen:
            link = await self.link_gen.generate_link(sender_email)
        if not link:
            link = ""
            logger.warning("No link generated for %s — sending without link", sender_email)

        # Build second-email body
        body_html = self.msg.get_second_message(lang, link)
        subject = self.msg.get_second_subject(lang, subject_original)

        ok = await automation.send_email(
            to=sender_email,
            subject=subject,
            body_html=body_html,
            send_delay=(
                self.config.limits.send_delay_min,
                self.config.limits.send_delay_max,
            ),
        )

        if ok:
            await self.db.mark_second_sent(sender_email, link)
            await self.db.increment_sent(account["email"])
            logger.info(
                "[%s] Second email sent to %s (link=%s…)",
                account["email"], sender_email, link[:30] if link else ""
            )
        else:
            logger.warning("[%s] Failed to send second email to %s", account["email"], sender_email)
