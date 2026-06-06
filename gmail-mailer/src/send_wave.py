"""
Main send wave — loads recipients from Excel, distributes them across accounts,
and sends first emails.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .account_manager import AccountManager
from .config import AppConfig
from .database import Database
from .excel_reader import load_recipients
from .gmail_automation import GmailAutomation
from .message_loader import MessageLoader
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
        """Read Excel and upsert new recipients into DB."""
        records = load_recipients(self.config.paths.recipients_excel)
        for email, subject, lang in records:
            await self.db.upsert_recipient(email, subject, lang)
        return len(records)

    async def run(self) -> dict:
        """
        Full sending wave:
          1. Reset daily counters
          2. Load recipients from Excel
          3. Send first emails account-by-account
        """
        await self.db.reset_daily_counters()

        loaded = await self.load_today_recipients()
        logger.info("Recipients loaded: %d", loaded)

        sem = asyncio.Semaphore(self.config.limits.concurrent_accounts)
        first_sent_total = 0
        errors_total = 0

        async def _send_batch(account: dict, recipients: list) -> None:
            nonlocal first_sent_total, errors_total
            async with sem:
                automation = GmailAutomation(
                    account=account,
                    config=self.config,
                    db=self.db,
                    telegram_notifier=self.notifier,
                )
                try:
                    await automation.start()
                    logged_in = await automation.login()
                    if not logged_in:
                        logger.warning("[%s] Login failed — skipping batch", account["email"])
                        errors_total += len(recipients)
                        return

                    for rec in recipients:
                        r_email = rec["email"]
                        lang = rec.get("language", "en")
                        subject = rec.get("subject", "")

                        body_html = self.msg.get_first_message(lang)
                        send_subject = self.msg.get_first_subject(lang, subject)

                        ok = await automation.send_email(
                            to=r_email,
                            subject=send_subject,
                            body_html=body_html,
                            send_delay=(
                                self.config.limits.send_delay_min,
                                self.config.limits.send_delay_max,
                            ),
                        )

                        if ok:
                            await self.db.mark_first_sent(r_email, account["email"])
                            await self.db.increment_sent(account["email"])
                            first_sent_total += 1
                        else:
                            await self.db.mark_bad(r_email)
                            errors_total += 1

                except Exception as exc:
                    logger.error("[%s] Send wave error: %s", account["email"], exc, exc_info=True)
                    await self.notifier.send(
                        f"❌ Error in send wave for <code>{account['email']}</code>: {exc}"
                    ) if self.notifier else None
                finally:
                    await automation.stop()

        tasks = []
        async for account, batch in self.acct_mgr.account_slot_generator():
            tasks.append(_send_batch(account, batch))

        await asyncio.gather(*tasks)

        # Check for newly exhausted accounts and notify
        rows = await self.db.get_active_accounts()
        if self.notifier:
            active = len(rows)
            if active <= 5:
                await self.notifier.alert_accounts_low(active)

        return {
            "first_sent": first_sent_total,
            "errors": errors_total,
        }
