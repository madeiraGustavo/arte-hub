"""
Account manager — loads accounts from text files, syncs to DB,
and provides a round-robin dispatcher that respects daily/total limits.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator, List, Optional

from .database import Database

logger = logging.getLogger(__name__)


def parse_account_line(line: str) -> Optional[dict]:
    """
    Supports two formats:
      email:password:submail:2fa_key
      email:password:submail
    Returns None on parse error.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(":", 3)
    if len(parts) < 3:
        logger.warning("Skipping malformed account line: %s", line[:40])
        return None
    account = {
        "email":     parts[0].strip(),
        "password":  parts[1].strip(),
        "submail":   parts[2].strip(),
        "two_fa_key": parts[3].strip() if len(parts) == 4 else None,
    }
    return account


class AccountManager:
    def __init__(self, db: Database, accounts_file: str):
        self.db = db
        self.accounts_file = accounts_file
        self._lock = asyncio.Lock()

    async def sync_accounts_from_file(self) -> int:
        """
        Read accounts file, upsert all to DB.
        Returns number of accounts loaded.
        """
        path = Path(self.accounts_file)
        if not path.exists():
            logger.error("Accounts file not found: %s", path)
            return 0

        loaded = 0
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                parsed = parse_account_line(line)
                if parsed is None:
                    continue
                await self.db.upsert_account(
                    email=parsed["email"],
                    password=parsed["password"],
                    submail=parsed["submail"],
                    two_fa_key=parsed["two_fa_key"],
                )
                loaded += 1

        logger.info("Loaded %d accounts from %s", loaded, path)
        return loaded

    async def get_next_available_account(self) -> Optional[dict]:
        """
        Return the active account with the lowest daily_sent count.
        Thread-safe via asyncio lock.
        """
        async with self._lock:
            rows = await self.db.get_active_accounts()
            if not rows:
                return None
            # rows are sorted by daily_sent ASC; take first
            row = rows[0]
            return dict(row)

    async def get_all_active_accounts(self) -> List[dict]:
        rows = await self.db.get_active_accounts()
        return [dict(r) for r in rows]

    async def account_slot_generator(
        self, batch_size: int = 100
    ) -> AsyncIterator[tuple[dict, List]]:
        """
        Yields (account, [recipient_emails]) batches,
        respecting the per-account daily limit.
        """
        accounts = await self.get_all_active_accounts()
        recipients = await self.db.get_pending_recipients(limit=10_000)

        if not accounts:
            logger.warning("No active accounts available for sending")
            return
        if not recipients:
            logger.info("No pending recipients to send to")
            return

        from datetime import date
        today = date.today().isoformat()

        rec_index = 0
        for acc in accounts:
            if rec_index >= len(recipients):
                break

            daily_sent = acc["daily_sent"] if acc["last_sent_date"] == today else 0
            remaining_today = 100 - daily_sent
            remaining_total = 300 - acc["total_sent"]
            slots = min(remaining_today, remaining_total, batch_size)

            if slots <= 0:
                continue

            batch = recipients[rec_index: rec_index + slots]
            rec_index += len(batch)

            if batch:
                yield acc, [dict(r) for r in batch]

    async def mark_account_blocked(
        self, email: str, reason: str = "blocked"
    ) -> None:
        await self.db.set_account_status(email, "blocked", notes=reason)
        logger.warning("Account blocked: %s — %s", email, reason)

    async def cleanup_expired_accounts(self, days: int = 3) -> List[str]:
        purged = await self.db.purge_old_exhausted_accounts(days=days)
        return purged
