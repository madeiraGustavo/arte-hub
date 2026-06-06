#!/usr/bin/env python3
"""
Gmail Mass Mailer — main entry point.

Usage:
  python main.py                   # Start scheduler (runs forever)
  python main.py --send-now        # Trigger send wave immediately
  python main.py --check-replies   # Trigger reply check immediately
  python main.py --import-accounts # Reload accounts from file to DB
  python main.py --stats           # Print today's stats and exit
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Make sure src/ is importable when running from project root
sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.database import Database
from src.account_manager import AccountManager
from src.message_loader import MessageLoader
from src.send_wave import SendWave
from src.reply_checker import ReplyChecker
from src.scheduler import Scheduler
from src.telegram_notifier import TelegramNotifier, LinkGenerator

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(log_dir: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    import colorlog

    fmt = "%(asctime)s %(log_color)s%(levelname)-8s%(reset)s [%(name)s] %(message)s"
    file_fmt = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

    file_handler = logging.FileHandler(
        Path(log_dir) / "mailer.log", encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(file_fmt))
    file_handler.setLevel(logging.DEBUG)

    console_handler = colorlog.StreamHandler()
    console_handler.setFormatter(colorlog.ColoredFormatter(fmt))
    console_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Silence noisy third-party loggers
    for lib in ("playwright", "asyncio", "aiosqlite", "urllib3", "httpx"):
        logging.getLogger(lib).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gmail Mass Mailer")
    p.add_argument("--config", default=None, help="Path to config.yaml")
    p.add_argument("--send-now", action="store_true", help="Run send wave immediately")
    p.add_argument("--check-replies", action="store_true", help="Run reply check immediately")
    p.add_argument("--import-accounts", action="store_true", help="Sync accounts from file to DB")
    p.add_argument("--stats", action="store_true", help="Print daily stats and exit")
    return p.parse_args()


# ---------------------------------------------------------------------------
# One-shot command runners
# ---------------------------------------------------------------------------

async def run_send_now(config, db: Database) -> None:
    await db.connect()
    notifier = TelegramNotifier(config.telegram.bot_token, config.telegram.admin_chat_id)
    acct_mgr = AccountManager(db, config.paths.accounts_file)
    await acct_mgr.sync_accounts_from_file()
    msg_loader = MessageLoader(
        messages_dir=str(Path(config.paths.db_file).parent.parent / "data" / "messages")
    )
    wave = SendWave(
        db=db,
        config=config,
        account_manager=acct_mgr,
        message_loader=msg_loader,
        notifier=notifier,
    )
    result = await wave.run()
    print(f"Send wave result: {result}")
    await db.close()


async def run_check_replies(config, db: Database) -> None:
    await db.connect()
    notifier = TelegramNotifier(config.telegram.bot_token, config.telegram.admin_chat_id)
    link_gen = LinkGenerator(config.telegram.link_bot_token, config.telegram.link_bot_chat_id)
    msg_loader = MessageLoader(
        messages_dir=str(Path(config.paths.db_file).parent.parent / "data" / "messages")
    )
    checker = ReplyChecker(
        db=db,
        config=config,
        message_loader=msg_loader,
        notifier=notifier,
        link_generator=link_gen,
    )
    result = await checker.run()
    print(f"Reply check result: {result}")
    await db.close()


async def run_import_accounts(config, db: Database) -> None:
    await db.connect()
    acct_mgr = AccountManager(db, config.paths.accounts_file)
    count = await acct_mgr.sync_accounts_from_file()
    print(f"Imported {count} accounts")
    await db.close()


async def run_stats(config, db: Database) -> None:
    await db.connect()
    stats = await db.get_daily_stats()
    print(
        f"\n=== Stats for {stats['date']} ===\n"
        f"First emails sent:  {stats['first_sent']}\n"
        f"Replies received:   {stats['replies']}\n"
        f"Second emails sent: {stats['second_sent']}\n"
        f"Active accounts:    {stats['active_accounts']}\n"
    )
    await db.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    setup_logging(config.paths.logs_dir)
    db = Database(config.paths.db_file)

    if args.send_now:
        await run_send_now(config, db)
    elif args.check_replies:
        await run_check_replies(config, db)
    elif args.import_accounts:
        await run_import_accounts(config, db)
    elif args.stats:
        await run_stats(config, db)
    else:
        # Default: start the scheduler and run forever
        scheduler = Scheduler(config, db)
        await scheduler.run_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
