"""
Plain dataclasses that mirror database rows.  No ORM, no magic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class Account:
    id: int
    email: str
    password: str
    submail: str                    # recovery/backup address
    two_fa_key: Optional[str]       # TOTP secret or None
    status: str                     # active | blocked | exhausted | deleted
    daily_sent: int
    total_sent: int
    last_sent_date: Optional[date]
    date_added: date
    date_exhausted: Optional[date]
    first_login_completed: bool
    cookies_path: Optional[str]     # path to saved session JSON
    profile_dir: Optional[str]      # Playwright persistent profile dir
    notes: str


@dataclass
class Recipient:
    id: int
    email: str
    subject: str                    # original subject from Excel
    language: str                   # nl | fr | de | en
    assigned_account: Optional[str] # account email that sent first mail
    first_sent_at: Optional[datetime]
    replied_at: Optional[datetime]
    unique_link: Optional[str]
    second_sent_at: Optional[datetime]
    status: str                     # pending | first_sent | replied | second_sent | bad
    notes: str


@dataclass
class LogEntry:
    id: int
    ts: datetime
    level: str            # INFO | WARNING | ERROR
    account: Optional[str]
    recipient: Optional[str]
    message: str
