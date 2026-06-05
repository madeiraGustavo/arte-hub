"""
Parse account files.

Supported line formats:
  email:password:submail:2fa_key   (type 1 — with TOTP secret)
  email:password:submail           (type 2 — without 2FA)

Blank lines and lines starting with '#' are ignored.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, TypedDict

log = logging.getLogger(__name__)


class AccountInput(TypedDict, total=False):
    email: str
    password: str
    submail: str
    two_fa_key: str | None


def parse_account_line(line: str) -> AccountInput | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parts = line.split(":")
    if len(parts) < 3:
        log.warning("Skipping malformed account line: %s", line[:40])
        return None

    email, password, submail = parts[0], parts[1], parts[2]
    two_fa_key = parts[3] if len(parts) >= 4 else None

    if "@" not in email:
        log.warning("Skipping line without valid email: %s", line[:40])
        return None

    return AccountInput(
        email=email.strip().lower(),
        password=password.strip(),
        submail=submail.strip(),
        two_fa_key=two_fa_key.strip() if two_fa_key else None,
    )


def parse_accounts_file(path: str | Path) -> List[AccountInput]:
    accounts: List[AccountInput] = []
    path = Path(path)
    if not path.exists():
        log.error("Accounts file not found: %s", path)
        return accounts

    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            acc = parse_account_line(line)
            if acc:
                accounts.append(acc)

    log.info("Parsed %d accounts from %s", len(accounts), path.name)
    return accounts


def parse_all_account_files(accounts_dir: str | Path) -> List[AccountInput]:
    """Parse every *.txt file in the accounts directory."""
    accounts_dir = Path(accounts_dir)
    all_accounts: List[AccountInput] = []
    seen_emails: set[str] = set()

    for txt_file in sorted(accounts_dir.glob("*.txt")):
        for acc in parse_accounts_file(txt_file):
            if acc["email"] not in seen_emails:
                all_accounts.append(acc)
                seen_emails.add(acc["email"])
            else:
                log.debug("Duplicate email skipped: %s", acc["email"])

    log.info("Total unique accounts found: %d", len(all_accounts))
    return all_accounts
