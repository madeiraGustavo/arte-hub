"""
Database module — async SQLite via aiosqlite.
Handles all persistence: accounts, recipients, logs.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    email                   TEXT NOT NULL UNIQUE,
    password                TEXT NOT NULL,
    submail                 TEXT,
    two_fa_key              TEXT,               -- TOTP seed from 2fa.fb.tools
    status                  TEXT NOT NULL DEFAULT 'active',
    -- active | blocked | exhausted | error
    first_login_completed   INTEGER NOT NULL DEFAULT 0,  -- 0=no, 1=yes
    daily_sent              INTEGER NOT NULL DEFAULT 0,
    total_sent              INTEGER NOT NULL DEFAULT 0,
    last_sent_date          TEXT,               -- ISO date YYYY-MM-DD
    date_added              TEXT NOT NULL,
    exhausted_date          TEXT,               -- when it hit total limit
    cookies_json            TEXT,               -- serialised browser cookies
    profile_dir             TEXT,               -- persistent browser profile path
    notes                   TEXT                -- free-form error/block notes
);

CREATE TABLE IF NOT EXISTS recipients (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    email                   TEXT NOT NULL UNIQUE,
    subject                 TEXT,
    language                TEXT,               -- nl | fr | de | en
    status                  TEXT NOT NULL DEFAULT 'pending',
    -- pending | first_sent | replied | second_sent | bad | skipped
    assigned_account        TEXT,               -- FK → accounts.email
    first_sent_at           TEXT,
    replied_at              TEXT,
    second_sent_at          TEXT,
    unique_link             TEXT,
    is_bad                  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    level       TEXT NOT NULL,
    account     TEXT,
    recipient   TEXT,
    message     TEXT NOT NULL
);

-- indexes for hot queries
CREATE INDEX IF NOT EXISTS idx_accounts_status   ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_recipients_status ON recipients(status);
CREATE INDEX IF NOT EXISTS idx_recipients_acct   ON recipients(assigned_account);
"""


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info("Database connected: %s", self.db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # -----------------------------------------------------------------------
    # Accounts
    # -----------------------------------------------------------------------

    async def upsert_account(
        self,
        email: str,
        password: str,
        submail: Optional[str] = None,
        two_fa_key: Optional[str] = None,
    ) -> None:
        today = date.today().isoformat()
        await self._conn.execute(
            """
            INSERT INTO accounts (email, password, submail, two_fa_key, date_added)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                password  = excluded.password,
                submail   = excluded.submail,
                two_fa_key= excluded.two_fa_key
            """,
            (email, password, submail, two_fa_key, today),
        )
        await self._conn.commit()

    async def get_active_accounts(self) -> List[aiosqlite.Row]:
        today = date.today().isoformat()
        async with self._conn.execute(
            """
            SELECT * FROM accounts
            WHERE status = 'active'
              AND total_sent < 300
              AND (last_sent_date != ? OR daily_sent < 100 OR last_sent_date IS NULL)
            ORDER BY daily_sent ASC
            """,
            (today,),
        ) as cur:
            return await cur.fetchall()

    async def get_account(self, email: str) -> Optional[aiosqlite.Row]:
        async with self._conn.execute(
            "SELECT * FROM accounts WHERE email = ?", (email,)
        ) as cur:
            return await cur.fetchone()

    async def set_account_status(self, email: str, status: str, notes: str = "") -> None:
        await self._conn.execute(
            "UPDATE accounts SET status = ?, notes = ? WHERE email = ?",
            (status, notes, email),
        )
        await self._conn.commit()

    async def mark_first_login_done(self, email: str) -> None:
        await self._conn.execute(
            "UPDATE accounts SET first_login_completed = 1 WHERE email = ?", (email,)
        )
        await self._conn.commit()

    async def save_cookies(self, email: str, cookies: list) -> None:
        await self._conn.execute(
            "UPDATE accounts SET cookies_json = ? WHERE email = ?",
            (json.dumps(cookies), email),
        )
        await self._conn.commit()

    async def get_cookies(self, email: str) -> Optional[list]:
        async with self._conn.execute(
            "SELECT cookies_json FROM accounts WHERE email = ?", (email,)
        ) as cur:
            row = await cur.fetchone()
            if row and row["cookies_json"]:
                return json.loads(row["cookies_json"])
            return None

    async def increment_sent(self, account_email: str) -> None:
        today = date.today().isoformat()
        async with self._conn.execute(
            "SELECT last_sent_date, daily_sent, total_sent FROM accounts WHERE email = ?",
            (account_email,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return

        daily = (row["daily_sent"] + 1) if row["last_sent_date"] == today else 1
        total = row["total_sent"] + 1
        exhausted_date = None
        status = "active"
        if total >= 300:
            status = "exhausted"
            exhausted_date = today

        await self._conn.execute(
            """
            UPDATE accounts
            SET daily_sent = ?, total_sent = ?, last_sent_date = ?,
                status = ?, exhausted_date = ?
            WHERE email = ?
            """,
            (daily, total, today, status, exhausted_date, account_email),
        )
        await self._conn.commit()

    async def reset_daily_counters(self) -> None:
        """Called at midnight / before the daily send wave starts."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        await self._conn.execute(
            """
            UPDATE accounts
            SET daily_sent = 0
            WHERE last_sent_date <= ? OR last_sent_date IS NULL
            """,
            (yesterday,),
        )
        await self._conn.commit()

    async def purge_old_exhausted_accounts(self, days: int = 3) -> List[str]:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        async with self._conn.execute(
            "SELECT email FROM accounts WHERE status = 'exhausted' AND exhausted_date <= ?",
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
        emails = [r["email"] for r in rows]
        if emails:
            placeholders = ",".join("?" * len(emails))
            await self._conn.execute(
                f"DELETE FROM accounts WHERE email IN ({placeholders})", emails
            )
            await self._conn.commit()
            logger.info("Purged %d exhausted accounts", len(emails))
        return emails

    # -----------------------------------------------------------------------
    # Recipients
    # -----------------------------------------------------------------------

    async def upsert_recipient(
        self, email: str, subject: str, language: str
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO recipients (email, subject, language)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO NOTHING
            """,
            (email, subject, language),
        )
        await self._conn.commit()

    async def get_pending_recipients(self, limit: int = 500) -> List[aiosqlite.Row]:
        async with self._conn.execute(
            """
            SELECT * FROM recipients
            WHERE status = 'pending' AND is_bad = 0
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            return await cur.fetchall()

    async def get_first_sent_recipients(self) -> List[aiosqlite.Row]:
        """Recipients that received first email and haven't replied yet."""
        async with self._conn.execute(
            """
            SELECT * FROM recipients
            WHERE status = 'first_sent' AND is_bad = 0
            """
        ) as cur:
            return await cur.fetchall()

    async def mark_first_sent(self, email: str, account: str) -> None:
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            """
            UPDATE recipients
            SET status = 'first_sent', assigned_account = ?, first_sent_at = ?
            WHERE email = ?
            """,
            (account, now, email),
        )
        await self._conn.commit()

    async def mark_replied(self, email: str) -> None:
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            "UPDATE recipients SET status = 'replied', replied_at = ? WHERE email = ?",
            (now, email),
        )
        await self._conn.commit()

    async def mark_second_sent(self, email: str, link: str) -> None:
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            """
            UPDATE recipients
            SET status = 'second_sent', second_sent_at = ?, unique_link = ?
            WHERE email = ?
            """,
            (now, link, email),
        )
        await self._conn.commit()

    async def mark_bad(self, email: str) -> None:
        await self._conn.execute(
            "UPDATE recipients SET is_bad = 1, status = 'bad' WHERE email = ?",
            (email,),
        )
        await self._conn.commit()

    async def get_replied_recipients(self) -> List[aiosqlite.Row]:
        async with self._conn.execute(
            "SELECT * FROM recipients WHERE status = 'replied' AND is_bad = 0"
        ) as cur:
            return await cur.fetchall()

    # -----------------------------------------------------------------------
    # Logs
    # -----------------------------------------------------------------------

    async def log(
        self,
        level: str,
        message: str,
        account: Optional[str] = None,
        recipient: Optional[str] = None,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO logs (level, account, recipient, message) VALUES (?, ?, ?, ?)",
            (level, account, recipient, message),
        )
        await self._conn.commit()

    async def get_daily_stats(self) -> dict:
        today = date.today().isoformat()
        async with self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM recipients WHERE DATE(first_sent_at) = ?",
            (today,),
        ) as cur:
            first_sent = (await cur.fetchone())["cnt"]
        async with self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM recipients WHERE DATE(replied_at) = ?",
            (today,),
        ) as cur:
            replies = (await cur.fetchone())["cnt"]
        async with self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM recipients WHERE DATE(second_sent_at) = ?",
            (today,),
        ) as cur:
            second_sent = (await cur.fetchone())["cnt"]
        async with self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM accounts WHERE status = 'active'"
        ) as cur:
            active_accounts = (await cur.fetchone())["cnt"]
        return {
            "first_sent": first_sent,
            "replies": replies,
            "second_sent": second_sent,
            "active_accounts": active_accounts,
            "date": today,
        }
