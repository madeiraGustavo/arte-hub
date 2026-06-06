"""
Daily send wave — loads recipients from Excel and sends first emails
via Gmail Compose (browser automation, NOT SMTP).

Uses sessions from local_setup.py (cookies saved on user's local machine).
If no session file found — falls back to browser login.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

from .account_manager import AccountManager
from .config import AppConfig
from .database import Database
from .excel_reader import load_recipients
from .gmail_automation import GmailAutomation
from .message_loader import MessageLoader
from .telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)

SESSIONS_DIR = Path("sessions")


def _session_file(email: str) -> Path:
    return SESSIONS_DIR / f"{re.sub(r'[^a-z0-9._-]', '_', email)}.json"


class SendWave:
    def __init__(self, db: Database, config: AppConfig,
                 account_manager: AccountManager, message_loader: MessageLoader,
                 notifier: Optional[TelegramNotifier] = None):
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
                automation = GmailAutomation(
                    account=account, config=self.config,
                    db=self.db, telegram_notifier=self.notifier,
                )
                try:
                    await automation.start()

                    # Try session file first (from local_setup.py)
                    session_ok = await automation.load_session()

                    if not session_ok:
                        # No session — try browser login
                        if not account.get("first_login_completed"):
                            logged_in = await automation.login()
                            if not logged_in:
                                logger.warning("[%s] Login failed — skipping", acct_email)
                                async with lock:
                                    errors_total += len(recipients)
                                return
                        else:
                            # Has first_login but no session file — try anyway
                            logged_in = await automation.login()
                            if not logged_in:
                                async with lock:
                                    errors_total += len(recipients)
                                return

                    # Send via Compose (browser, not SMTP)
                    sent = 0
                    failed = 0
                    for rec in recipients:
                        lang = rec.get("language", "en")
                        product = rec.get("subject", "")
                        body_html = self.msg.get_first_message(lang, product=product)
                        subject = self.msg.get_first_subject(lang, product)

                        ok = await automation.send_email(
                            to=rec["email"],
                            subject=subject,
                            body_html=body_html,
                            send_delay=(
                                self.config.limits.send_delay_min,
                                self.config.limits.send_delay_max,
                            ),
                        )

                        if ok:
                            await self.db.mark_first_sent(rec["email"], acct_email)
                            await self.db.increment_sent(acct_email)
                            sent += 1
                        else:
                            await self.db.mark_bad(rec["email"])
                            failed += 1

                    async with lock:
                        first_sent_total += sent
                        errors_total += failed

                    logger.info("[%s] Compose wave: sent=%d failed=%d", acct_email, sent, failed)

                except Exception as exc:
                    logger.error("[%s] Send wave error: %s", acct_email, exc, exc_info=True)
                    if self.notifier:
                        await self.notifier.send(f"❌ Error: <code>{acct_email}</code>: {exc}")
                finally:
                    await automation.stop()

        tasks = []
        async for account, batch in self.acct_mgr.account_slot_generator():
            tasks.append(_send_account_batch(account, batch))

        if not tasks:
            logger.info("No tasks (no active accounts or no pending recipients)")
            return {"first_sent": 0, "errors": 0}

        logger.info("Starting send wave: %d batches, concurrency=%d",
                    len(tasks), self.config.limits.concurrent_accounts)
        await asyncio.gather(*tasks)

        active_rows = await self.db.get_active_accounts()
        if self.notifier and len(active_rows) <= 5:
            await self.notifier.alert_accounts_low(len(active_rows))

        return {"first_sent": first_sent_total, "errors": errors_total}
