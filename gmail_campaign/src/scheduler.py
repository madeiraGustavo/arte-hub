"""
Cron-style scheduler built on APScheduler.

Schedule (all times are local server time, configurable via config.json):

  07:00  — run_maintenance (reset daily counters, purge old accounts)
  08:00  — run_daily_blast (send first emails from today's Excel)
  09:00  — run_reply_check (1st check, ~60 min after blast)
  11:00  — run_reply_check (2nd check)
  15:00  — run_reply_check (3rd check)
  19:00  — run_reply_check (4th check + send daily stats)

The schedule can be adjusted by editing SCHEDULE_TIMES below or via
environment variables BLAST_HOUR, CHECK_HOURS (comma-separated).

Run:  python -m gmail_campaign.src.scheduler
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

from .campaign_runner import run_daily_blast, run_maintenance, run_reply_check
from .config import Config

log = logging.getLogger(__name__)

BLAST_HOUR: int = int(os.environ.get("BLAST_HOUR", "8"))
CHECK_HOURS: list[int] = [
    int(h) for h in os.environ.get("CHECK_HOURS", "9,11,15,19").split(",")
]
MAINTENANCE_HOUR: int = int(os.environ.get("MAINTENANCE_HOUR", "7"))


def _setup_apscheduler(config: Config):
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
        from apscheduler.triggers.cron import CronTrigger  # type: ignore
    except ImportError:
        raise RuntimeError("apscheduler required: pip install apscheduler")

    scheduler = AsyncIOScheduler(timezone="Europe/Amsterdam")

    scheduler.add_job(
        lambda: asyncio.ensure_future(run_maintenance(config)),
        CronTrigger(hour=MAINTENANCE_HOUR, minute=0),
        id="maintenance",
        name="Daily maintenance",
        replace_existing=True,
    )

    scheduler.add_job(
        lambda: asyncio.ensure_future(run_daily_blast(config)),
        CronTrigger(hour=BLAST_HOUR, minute=0),
        id="daily_blast",
        name="Daily email blast",
        replace_existing=True,
    )

    for hour in CHECK_HOURS:
        scheduler.add_job(
            lambda: asyncio.ensure_future(run_reply_check(config)),
            CronTrigger(hour=hour, minute=0),
            id=f"reply_check_{hour}",
            name=f"Reply check {hour:02d}:00",
            replace_existing=True,
        )

    return scheduler


async def run_scheduler(config: Config) -> None:
    scheduler = _setup_apscheduler(config)
    scheduler.start()

    log.info("Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        log.info("  %s — %s", job.name, job.next_run_time)

    # Keep running until SIGTERM / SIGINT
    stop_event = asyncio.Event()

    def _handle_signal(*_):
        log.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    await stop_event.wait()
    scheduler.shutdown(wait=False)
    log.info("Scheduler stopped")


def main() -> None:
    """Entry point for running the scheduler as a long-running process."""
    from .config import Config
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/scheduler.log"),
        ],
    )

    config = Config.load("config.json")
    asyncio.run(run_scheduler(config))


if __name__ == "__main__":
    main()
