"""
SQLite database layer.  All raw SQL lives here — no ORM dependency.
Thread-safe: uses check_same_thread=False + a module-level lock so async
code can call these helpers from any coroutine.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Generator, List, Optional

from .models import Account, LogEntry, Recipient

_lock = threading.Lock()


# ─────────────────────────── connection helper ──────────────────────────────

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _cursor(db_path: str) -> Generator[sqlite3.Cursor, None, None]:
    with _lock:
        conn = _connect(db_path)
        try:
            cur = conn.cursor()
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ─────────────────────────── schema ─────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    email                   TEXT    UNIQUE NOT NULL,
    password                TEXT    NOT NULL,
    submail                 TEXT    NOT NULL DEFAULT '',
    two_fa_key              TEXT,
    status                  TEXT    NOT NULL DEFAULT 'active',
    daily_sent              INTEGER NOT NULL DEFAULT 0,
    total_sent              INTEGER NOT NULL DEFAULT 0,
    last_sent_date          TEXT,
    date_added              TEXT    NOT NULL,
    date_exhausted          TEXT,
    first_login_completed   INTEGER NOT NULL DEFAULT 0,
    cookies_path            TEXT,
    profile_dir             TEXT,
    notes                   TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS recipients (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT    UNIQUE NOT NULL,
    subject             TEXT    NOT NULL DEFAULT '',
    language            TEXT    NOT NULL DEFAULT 'en',
    assigned_account    TEXT,
    first_sent_at       TEXT,
    replied_at          TEXT,
    unique_link         TEXT,
    second_sent_at      TEXT,
    status              TEXT    NOT NULL DEFAULT 'pending',
    notes               TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    level       TEXT    NOT NULL DEFAULT 'INFO',
    account     TEXT,
    recipient   TEXT,
    message     TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_acc_status  ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_rec_status  ON recipients(status);
CREATE INDEX IF NOT EXISTS idx_log_ts      ON logs(ts);
"""


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = _connect(db_path)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()


# ─────────────────────────── accounts ───────────────────────────────────────

def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        email=row["email"],
        password=row["password"],
        submail=row["submail"],
        two_fa_key=row["two_fa_key"],
        status=row["status"],
        daily_sent=row["daily_sent"],
        total_sent=row["total_sent"],
        last_sent_date=date.fromisoformat(row["last_sent_date"]) if row["last_sent_date"] else None,
        date_added=date.fromisoformat(row["date_added"]),
        date_exhausted=date.fromisoformat(row["date_exhausted"]) if row["date_exhausted"] else None,
        first_login_completed=bool(row["first_login_completed"]),
        cookies_path=row["cookies_path"],
        profile_dir=row["profile_dir"],
        notes=row["notes"],
    )


def upsert_account(db_path: str, acc: "AccountInput") -> None:
    """Insert or update account from parsed data dict."""
    today = date.today().isoformat()
    with _cursor(db_path) as cur:
        cur.execute(
            """
            INSERT INTO accounts (email, password, submail, two_fa_key, date_added)
            VALUES (:email, :password, :submail, :two_fa_key, :date_added)
            ON CONFLICT(email) DO UPDATE SET
                password  = excluded.password,
                submail   = excluded.submail,
                two_fa_key = excluded.two_fa_key
            """,
            {
                "email": acc["email"],
                "password": acc["password"],
                "submail": acc["submail"],
                "two_fa_key": acc.get("two_fa_key"),
                "date_added": today,
            },
        )


def get_active_accounts(db_path: str) -> List[Account]:
    with _cursor(db_path) as cur:
        cur.execute("SELECT * FROM accounts WHERE status='active' ORDER BY total_sent ASC")
        return [_row_to_account(r) for r in cur.fetchall()]


def get_account(db_path: str, email: str) -> Optional[Account]:
    with _cursor(db_path) as cur:
        cur.execute("SELECT * FROM accounts WHERE email=?", (email,))
        row = cur.fetchone()
        return _row_to_account(row) if row else None


def update_account_field(db_path: str, email: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=:{k}" for k in fields)
    fields["email"] = email
    with _cursor(db_path) as cur:
        cur.execute(f"UPDATE accounts SET {sets} WHERE email=:email", fields)


def mark_account_status(db_path: str, email: str, status: str) -> None:
    extra: dict = {}
    if status == "exhausted":
        extra["date_exhausted"] = date.today().isoformat()
    update_account_field(db_path, email, status=status, **extra)


def increment_sent(db_path: str, email: str) -> None:
    today = date.today().isoformat()
    with _cursor(db_path) as cur:
        cur.execute(
            """
            UPDATE accounts
            SET daily_sent = CASE
                    WHEN last_sent_date = :today THEN daily_sent + 1
                    ELSE 1
                END,
                total_sent      = total_sent + 1,
                last_sent_date  = :today
            WHERE email = :email
            """,
            {"today": today, "email": email},
        )


def reset_daily_counts(db_path: str) -> None:
    """Call at the start of each day for accounts whose last_sent_date < today."""
    today = date.today().isoformat()
    with _cursor(db_path) as cur:
        cur.execute(
            "UPDATE accounts SET daily_sent=0 WHERE last_sent_date IS NULL OR last_sent_date < ?",
            (today,),
        )


def purge_expired_accounts(db_path: str, expiry_days: int) -> List[str]:
    """Delete accounts exhausted more than expiry_days ago; return their emails."""
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=expiry_days)).isoformat()
    with _cursor(db_path) as cur:
        cur.execute(
            "SELECT email FROM accounts WHERE status='exhausted' AND date_exhausted <= ?",
            (cutoff,),
        )
        emails = [r["email"] for r in cur.fetchall()]
        if emails:
            placeholders = ",".join("?" * len(emails))
            cur.execute(f"DELETE FROM accounts WHERE email IN ({placeholders})", emails)
    return emails


def save_cookies(db_path: str, email: str, cookies: list, cookies_dir: str) -> str:
    Path(cookies_dir).mkdir(parents=True, exist_ok=True)
    safe = email.replace("@", "_at_").replace(".", "_")
    path = str(Path(cookies_dir) / f"{safe}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cookies, fh)
    update_account_field(db_path, email, cookies_path=path)
    return path


def load_cookies(db_path: str, email: str) -> Optional[list]:
    acc = get_account(db_path, email)
    if not acc or not acc.cookies_path:
        return None
    path = Path(acc.cookies_path)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ─────────────────────────── recipients ─────────────────────────────────────

def _row_to_recipient(row: sqlite3.Row) -> Recipient:
    def _dt(v): return datetime.fromisoformat(v) if v else None
    return Recipient(
        id=row["id"],
        email=row["email"],
        subject=row["subject"],
        language=row["language"],
        assigned_account=row["assigned_account"],
        first_sent_at=_dt(row["first_sent_at"]),
        replied_at=_dt(row["replied_at"]),
        unique_link=row["unique_link"],
        second_sent_at=_dt(row["second_sent_at"]),
        status=row["status"],
        notes=row["notes"],
    )


def get_recipient_by_email(db_path: str, email: str) -> Optional[Recipient]:
    with _cursor(db_path) as cur:
        cur.execute("SELECT * FROM recipients WHERE email=?", (email,))
        row = cur.fetchone()
        return _row_to_recipient(row) if row else None


def upsert_recipients(db_path: str, rows: list[dict]) -> int:
    """Bulk-insert new recipients; skip existing (by email). Returns count added."""
    added = 0
    with _cursor(db_path) as cur:
        for r in rows:
            cur.execute(
                """
                INSERT OR IGNORE INTO recipients (email, subject, language)
                VALUES (:email, :subject, :language)
                """,
                r,
            )
            added += cur.rowcount
    return added


def get_pending_recipients(db_path: str, limit: int = 200) -> List[Recipient]:
    with _cursor(db_path) as cur:
        cur.execute(
            "SELECT * FROM recipients WHERE status='pending' ORDER BY id ASC LIMIT ?",
            (limit,),
        )
        return [_row_to_recipient(r) for r in cur.fetchall()]


def get_first_sent_recipients(db_path: str) -> List[Recipient]:
    """Recipients waiting for a reply check."""
    with _cursor(db_path) as cur:
        cur.execute(
            "SELECT * FROM recipients WHERE status='first_sent' ORDER BY first_sent_at ASC"
        )
        return [_row_to_recipient(r) for r in cur.fetchall()]


def mark_first_sent(db_path: str, email: str, account_email: str) -> None:
    update_recipient(db_path, email,
                     status="first_sent",
                     assigned_account=account_email,
                     first_sent_at=datetime.utcnow().isoformat())


def mark_replied(db_path: str, email: str) -> None:
    update_recipient(db_path, email,
                     status="replied",
                     replied_at=datetime.utcnow().isoformat())


def mark_second_sent(db_path: str, email: str, unique_link: str) -> None:
    update_recipient(db_path, email,
                     status="second_sent",
                     unique_link=unique_link,
                     second_sent_at=datetime.utcnow().isoformat())


def mark_bad(db_path: str, email: str, reason: str = "") -> None:
    update_recipient(db_path, email, status="bad", notes=reason)


def update_recipient(db_path: str, email: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=:{k}" for k in fields)
    fields["email"] = email
    with _cursor(db_path) as cur:
        cur.execute(f"UPDATE recipients SET {sets} WHERE email=:email", fields)


# ─────────────────────────── logs ───────────────────────────────────────────

def db_log(db_path: str, level: str, message: str,
           account: Optional[str] = None, recipient: Optional[str] = None) -> None:
    with _cursor(db_path) as cur:
        cur.execute(
            "INSERT INTO logs (ts, level, account, recipient, message) VALUES (?,?,?,?,?)",
            (datetime.utcnow().isoformat(), level, account, recipient, message),
        )


def get_daily_stats(db_path: str) -> dict:
    today = date.today().isoformat()
    with _cursor(db_path) as cur:
        cur.execute(
            "SELECT COUNT(*) FROM recipients WHERE DATE(first_sent_at)=?", (today,)
        )
        first_sent = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM recipients WHERE DATE(replied_at)=?", (today,)
        )
        replied = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM recipients WHERE DATE(second_sent_at)=?", (today,)
        )
        second_sent = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM accounts WHERE status='active'")
        active_accounts = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM accounts WHERE status='exhausted'")
        exhausted_accounts = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM accounts WHERE status='blocked'")
        blocked_accounts = cur.fetchone()[0]

    return {
        "date": today,
        "first_sent": first_sent,
        "replied": replied,
        "second_sent": second_sent,
        "active_accounts": active_accounts,
        "exhausted_accounts": exhausted_accounts,
        "blocked_accounts": blocked_accounts,
    }
