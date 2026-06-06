"""
Account manager — loads accounts from text files, syncs to DB,
and provides a round-robin dispatcher that respects daily/total limits.

Supported account formats (auto-detected, separator can be : or |):

  email:password:submail:2fa_key          (colon, 4 fields)
  email:password:submail                  (colon, 3 fields)
  email|password|app_password_or_2fa      (pipe,  3 fields)
  email|password                          (pipe,  2 fields)

Field detection logic for the 3rd field:
  - contains '@'       → submail (recovery email)
  - 16 lowercase only  → App Password (Google format, no spaces)
  - 32 lowercase+digits → App Password (stored without spaces)
  - 16–64 BASE32 chars  → 2FA TOTP key
  - anything else       → treated as App Password
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import AsyncIterator, List, Optional

from .database import Database

logger = logging.getLogger(__name__)

_BASE32_RE = re.compile(r'^[A-Z2-7]{16,64}$')
_APPPASS_RE = re.compile(r'^[a-z0-9]{16,32}$')   # Google App Password (no spaces)


def _classify_third_field(value: str) -> dict:
    """
    Returns dict with keys: submail, two_fa_key, app_password
    exactly one of which is non-None.
    """
    v = value.strip()
    if not v:
        return {"submail": None, "two_fa_key": None, "app_password": None}

    # Recovery email
    if "@" in v:
        return {"submail": v, "two_fa_key": None, "app_password": None}

    # Standard Base32 TOTP seed (uppercase A-Z and 2-7)
    if _BASE32_RE.match(v.upper()) and v.upper() == v:
        return {"submail": None, "two_fa_key": v, "app_password": None}

    # Looks like an App Password (16–32 lowercase letters/digits, no spaces)
    if _APPPASS_RE.match(v):
        return {"submail": None, "two_fa_key": None, "app_password": v}

    # Fallback: mixed case/length → store as App Password and try it for SMTP
    return {"submail": None, "two_fa_key": None, "app_password": v}


def parse_account_line(line: str) -> Optional[dict]:
    """
    Parse one line from accounts.txt.
    Supports both ':' and '|' as separators.
    Returns None on parse error or blank/comment line.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Detect separator: pipe takes precedence if present
    if "|" in line:
        parts = line.split("|")
    else:
        parts = line.split(":", 3)

    if len(parts) < 2:
        logger.warning("Skipping malformed account line: %s", line[:60])
        return None

    email    = parts[0].strip()
    password = parts[1].strip()

    if not email or "@" not in email:
        logger.warning("Invalid email in account line: %s", line[:60])
        return None

    submail     = None
    two_fa_key  = None
    app_password = None

    if len(parts) == 3:
        # 3-field line: classify the third field
        classified = _classify_third_field(parts[2])
        submail      = classified["submail"]
        two_fa_key   = classified["two_fa_key"]
        app_password = classified["app_password"]

    elif len(parts) >= 4:
        # 4-field line: email:password:submail:2fa_key  (classic colon format)
        submail    = parts[2].strip() or None
        two_fa_key = parts[3].strip() or None

    return {
        "email":        email.lower(),
        "password":     password,
        "submail":      submail,
        "two_fa_key":   two_fa_key,
        "app_password": app_password,
    }


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
                # If the line already contained an App Password, save it now
                # so SMTP works immediately without needing browser setup
                if parsed["app_password"]:
                    await self.db.save_app_password(
                        parsed["email"], parsed["app_password"]
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
