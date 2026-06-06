"""
Reply checker — orchestrates checking replies across all accounts
and dispatching second emails.

Uses IMAP (fast) as primary method, falls back to browser if IMAP auth fails.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from .config import AppConfig
from .database import Database
from .gmail_automation import GmailAutomation
from .imap_checker import check_replies_imap
from .message_loader import MessageLoader
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
        """
        For every active account: check replies via IMAP (fast),
        fall back to browser if IMAP fails.
        Then send second emails to all who replied.
        """
        accounts = await self.db.get_active_accounts()
        if not accounts:
            return {}

        total_replies = 0
        total_second_sent = 0
        sem = asyncio.Semaphore(self.config.limits.concurrent_accounts)

        async def _process_account(acc):
            nonlocal total_replies, total_second_sent
            async with sem:
                acct_email = acc["email"]

                # Get recipients assigned to this account still waiting for reply
                all_first_sent = await self.db.get_first_sent_recipients()
                my_recipients = [
                    r for r in all_first_sent if r["assigned_account"] == acct_email
                ]
                if not my_recipients:
                    return

                expected = [r["email"] for r in my_recipients]

                # --- Try IMAP first (much faster than browser) ---
                replied = await check_replies_imap(
                    account_email=acct_email,
                    password=acc["password"],
                    expected_senders=expected,
                    since_days=7,
                )

                # --- If IMAP returned nothing AND we have senders to check,
                #     the login may have failed — try browser fallback ---
                if not replied:
                    logger.debug(
                        "[%s] IMAP returned no replies, trying browser fallback",
                        acct_email,
                    )
                    replied = await self._check_via_browser(acc, expected)

                total_replies += len(replied)

                if not replied:
                    return

                # Send second emails to all who replied
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

                    for sender_email in replied:
                        rec = next(
                            (r for r in my_recipients if r["email"] == sender_email),
                            None,
                        )
                        if not rec:
                            continue
                        await self.db.mark_replied(sender_email)
                        sent = await self._send_second_email(automation, acc, rec)
                        if sent:
                            total_second_sent += 1
                finally:
                    await automation.stop()

        tasks = [_process_account(acc) for acc in accounts]
        await asyncio.gather(*tasks)

        return {"replies_found": total_replies, "second_sent": total_second_sent}

    async def _check_via_browser(
        self, acc: dict, expected: List[str]
    ) -> List[str]:
        automation = GmailAutomation(
            account=dict(acc),
            config=self.config,
            db=self.db,
            telegram_notifier=self.notifier,
        )
        replied: List[str] = []
        try:
            await automation.start()
            ok = await automation.login()
            if ok:
                replied = await automation.check_replies_browser(expected)
        except Exception as exc:
            logger.error("[%s] Browser reply check failed: %s", acc["email"], exc)
        finally:
            await automation.stop()
        return replied

    async def _send_second_email(
        self,
        automation: GmailAutomation,
        account: dict,
        recipient: dict,
    ) -> bool:
        sender_email = recipient["email"]
        lang = recipient.get("language", "en")
        subject_original = recipient.get("subject", "")

        # Generate unique link via Telegram bot
        link = ""
        if self.link_gen:
            link = await self.link_gen.generate_link(sender_email) or ""
        if not link:
            logger.warning("No link generated for %s — sending without link", sender_email)

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
            logger.info("[%s] Second email → %s (link=%s…)", account["email"],
                        sender_email, link[:30] if link else "none")
        else:
            logger.warning("[%s] Failed to send second email to %s", account["email"], sender_email)

        return ok
