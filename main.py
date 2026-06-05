#!/usr/bin/env python3
"""
Gmail Campaign Automation — CLI entry point.

Usage:
  python main.py wizard                   # Interactive setup (proxy, Telegram, templates)
  python main.py setup                    # Initialize DB and dirs
  python main.py import-accounts          # Import accounts from data/accounts/*.txt
  python main.py import-recipients FILE   # Import recipients from Excel
  python main.py blast                    # Run daily send blast now
  python main.py check-replies            # Check inboxes for replies now
  python main.py maintenance              # Run maintenance + stats
  python main.py scheduler               # Start the scheduler daemon
  python main.py dashboard [PORT]        # Start web dashboard (default: port 8080)
  python main.py stats                    # Print current DB stats
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Ensure src/ is importable when running from project root
sys.path.insert(0, str(Path(__file__).parent))

from src.config import Config
from src.database import (
    get_daily_stats,
    init_db,
    upsert_account,
    upsert_recipients,
)
from src.account_parser import parse_all_account_files
from src.excel_reader import read_recipients
from src.language_detector import detect_languages_bulk
from src.campaign_runner import run_daily_blast, run_maintenance, run_reply_check


def setup_logging(config: Config) -> None:
    Path(config.paths.logs_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f"{config.paths.logs_dir}/campaign.log"),
        ],
    )


def cmd_setup(config: Config) -> None:
    """Create all directories and initialise the database."""
    for d in [
        config.paths.logs_dir,
        config.paths.profiles_dir,
        config.paths.accounts_dir,
        config.paths.recipients_dir,
        "templates",
    ]:
        Path(d).mkdir(parents=True, exist_ok=True)
    init_db(config.paths.db_path)
    print(f"[OK] Database initialised at {config.paths.db_path}")
    print("[OK] Directory structure ready.")


def cmd_import_accounts(config: Config) -> None:
    """Parse all account files and upsert into DB."""
    accounts = parse_all_account_files(config.paths.accounts_dir)
    if not accounts:
        print("[WARN] No accounts found. Place .txt files in data/accounts/")
        return
    for acc in accounts:
        upsert_account(config.paths.db_path, acc)
    print(f"[OK] Imported/updated {len(accounts)} accounts.")


def cmd_import_recipients(config: Config, excel_path: str) -> None:
    """Read Excel file, detect languages, upsert into DB."""
    rows = read_recipients(excel_path)
    rows_with_lang = detect_languages_bulk([dict(r) for r in rows])
    added = upsert_recipients(config.paths.db_path, rows_with_lang)
    print(f"[OK] {added} new recipients imported ({len(rows)} total rows in file).")


def cmd_blast(config: Config) -> None:
    asyncio.run(run_daily_blast(config))


def cmd_check_replies(config: Config) -> None:
    asyncio.run(run_reply_check(config))


def cmd_maintenance(config: Config) -> None:
    asyncio.run(run_maintenance(config))


def cmd_scheduler(config: Config) -> None:
    from src.scheduler import run_scheduler
    asyncio.run(run_scheduler(config))


def cmd_dashboard(config: Config, port: int = 8080) -> None:
    try:
        import uvicorn  # type: ignore
    except ImportError:
        print("uvicorn is required: pip install uvicorn")
        sys.exit(1)
    # Import the actual app object so uvicorn uses the correct port
    from src.dashboard import app as dashboard_app
    print(f"\n  Dashboard running at  http://0.0.0.0:{port}")
    print(f"  Open in browser:      http://YOUR_SERVER_IP:{port}\n")
    uvicorn.run(
        dashboard_app,
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="warning",
    )


def cmd_stats(config: Config) -> None:
    stats = get_daily_stats(config.paths.db_path)
    print("\n=== Campaign Stats ===")
    for k, v in stats.items():
        print(f"  {k:<25} {v}")
    print()


def main() -> None:
    config = Config.load("config.json")
    setup_logging(config)

    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "wizard":
        from src.wizard import run_wizard
        run_wizard()
    elif cmd == "setup":
        cmd_setup(config)
    elif cmd == "import-accounts":
        cmd_import_accounts(config)
    elif cmd == "import-recipients":
        if len(args) < 2:
            print("Usage: python main.py import-recipients <path_to_excel.xlsx>")
            sys.exit(1)
        cmd_import_recipients(config, args[1])
    elif cmd == "blast":
        cmd_blast(config)
    elif cmd == "check-replies":
        cmd_check_replies(config)
    elif cmd == "maintenance":
        cmd_maintenance(config)
    elif cmd == "scheduler":
        cmd_scheduler(config)
    elif cmd == "dashboard":
        port = int(args[1]) if len(args) > 1 else 8080
        cmd_dashboard(config, port)
    elif cmd == "stats":
        cmd_stats(config)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
