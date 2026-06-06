"""
Scheduler — uses APScheduler to orchestrate the daily send wave and reply checks.
Also starts the Telegram bot for remote control.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .account_manager import AccountManager
from .config import AppConfig
from .database import Database
from .message_loader import MessageLoader
from .reply_checker import ReplyChecker
from .send_wave import SendWave
from .telegram_notifier import LinkGenerator, TelegramNotifier

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self._scheduler = AsyncIOScheduler()

        self.notifier = TelegramNotifier(
            bot_token=config.telegram.bot_token,
            admin_chat_id=config.telegram.admin_chat_id,
        )
        self.link_gen = LinkGenerator(
            bot_token=config.telegram.link_bot_token,
            chat_id=config.telegram.link_bot_chat_id,
        )
        self.acct_mgr = AccountManager(db, config.paths.accounts_file)

        _msg_dir = str(Path(config.paths.db_file).parent.parent / "data" / "messages")
        self.msg_loader = MessageLoader(messages_dir=_msg_dir)

        self.send_wave = SendWave(
            db=db, config=config,
            account_manager=self.acct_mgr,
            message_loader=self.msg_loader,
            notifier=self.notifier,
        )
        self.reply_checker = ReplyChecker(
            db=db, config=config,
            message_loader=self.msg_loader,
            notifier=self.notifier,
            link_generator=self.link_gen,
        )

        # Telegram bot (optional — only if token is configured)
        self._bot = None
        self._bot_task = None
        if config.telegram.bot_token:
            from .telegram_bot import MailerBot
            self._bot = MailerBot(config, db)
            self._bot.set_handlers(
                send_wave_fn=self.send_wave.run,
                reply_check_fn=self.reply_checker.run,
            )

    # -----------------------------------------------------------------------
    # Jobs
    # -----------------------------------------------------------------------

    async def _job_send_wave(self) -> None:
        logger.info("=== Daily send wave starting ===")
        try:
            await self.acct_mgr.sync_accounts_from_file()
            result = await self.send_wave.run()
            logger.info("Send wave done: %s", result)
        except Exception as exc:
            logger.error("Send wave failed: %s", exc, exc_info=True)
            await self.notifier.send(f"❌ Send wave FAILED: {exc}")

    async def _job_check_replies(self) -> None:
        logger.info("=== Reply check starting ===")
        try:
            result = await self.reply_checker.run()
            logger.info("Reply check done: %s", result)
        except Exception as exc:
            logger.error("Reply check failed: %s", exc, exc_info=True)

    async def _job_daily_stats(self) -> None:
        stats = await self.db.get_daily_stats()
        await self.notifier.send_daily_stats(stats)

    async def _job_cleanup(self) -> None:
        purged = await self.acct_mgr.cleanup_expired_accounts(
            days=self.config.limits.days_before_account_removal
        )
        if purged:
            await self.notifier.send(
                f"🗑 Purged {len(purged)} exhausted account(s): "
                + ", ".join(f"<code>{e}</code>" for e in purged)
            )

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def setup_jobs(self) -> None:
        cfg = self.config
        self._scheduler.add_job(
            self._job_send_wave,
            CronTrigger(hour=cfg.send_hour, minute=cfg.send_minute),
            id="send_wave", max_instances=1, coalesce=True,
        )
        for h in cfg.reply_check_hours:
            self._scheduler.add_job(
                self._job_check_replies,
                CronTrigger(hour=h, minute=0),
                id=f"reply_{h}", max_instances=1, coalesce=True,
            )
        self._scheduler.add_job(
            self._job_daily_stats, CronTrigger(hour=23, minute=0), id="stats",
        )
        self._scheduler.add_job(
            self._job_cleanup, CronTrigger(hour=0, minute=30), id="cleanup",
        )
        logger.info("Scheduler jobs registered")

    async def start(self) -> None:
        await self.db.connect()
        await self.acct_mgr.sync_accounts_from_file()
        self.setup_jobs()
        self._scheduler.start()
        logger.info("Scheduler started")

        if self._bot and self.config.telegram.bot_token:
            app = self._bot.build_app()
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot started")
            await self.notifier.send(
                "🟢 Gmail Mailer запущен\\! Команды: /stats /send /check /accounts /logs"
            )
        else:
            await self.notifier.send("🟢 Gmail Mailer started — scheduler running")

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        if self._bot and self._bot._app:
            try:
                await self._bot._app.updater.stop()
                await self._bot._app.stop()
                await self._bot._app.shutdown()
            except Exception:
                pass
        await self.db.close()
        logger.info("Scheduler stopped")

    async def run_forever(self) -> None:
        await self.start()
        try:
            while True:
                await asyncio.sleep(60)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await self.stop()
