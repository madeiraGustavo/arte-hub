"""
IMAP-based reply checker.

Connects via IMAP SSL to Gmail (imap.gmail.com:993) using the account's
email and password (App Password or regular password with Less Secure Apps
disabled if using OAuth cookies — here we use the stored password).

Why IMAP instead of browser:
  - 10–50× faster than loading Gmail UI
  - No Playwright/DOM needed — pure protocol
  - Can run many accounts in parallel without browser overhead
  - Gmail IMAP is available 24/7 regardless of UI changes

Note: Google requires an App Password for IMAP when 2FA is enabled.
If the account has 2FA, the user must generate an App Password in
Google Account → Security → App passwords.
We try the regular password first; if IMAP auth fails, we fall back to
browser-based checking.
"""
from __future__ import annotations

import asyncio
import email
import email.header
import logging
import re
from email.utils import parseaddr
from typing import List, Optional, Set, Tuple

import aioimaplib

logger = logging.getLogger(__name__)

# Bounce / OOO / auto-reply subject fragments (case-insensitive)
AUTORESPONSE_FRAGMENTS = [
    "out of office", "auto-reply", "automatic reply", "autoreply",
    "away from", "vacation", "abwesenheit", "afwezig", "absent",
    "mailer-daemon", "delivery failure", "delivery status", "undeliverable",
    "mail delivery", "returned mail", "bounce", "postmaster",
    "noreply", "no-reply", "do-not-reply", "donotreply",
]

AUTORESPONSE_SENDERS = [
    "mailer-daemon", "postmaster", "noreply", "no-reply",
    "do-not-reply", "donotreply", "automated", "auto-confirm",
]


def _decode_header_value(raw: str) -> str:
    parts = email.header.decode_header(raw or "")
    chunks = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                chunks.append(part.decode(charset or "utf-8", errors="replace"))
            except Exception:
                chunks.append(part.decode("utf-8", errors="replace"))
        else:
            chunks.append(part)
    return "".join(chunks)


def _is_autoresponse(subject: str, from_addr: str) -> bool:
    low_subj = subject.lower()
    low_from = from_addr.lower()
    for fragment in AUTORESPONSE_FRAGMENTS:
        if fragment in low_subj:
            return True
    for sender in AUTORESPONSE_SENDERS:
        if sender in low_from:
            return True
    return False


class IMAPReplyChecker:
    """
    Connects to Gmail IMAP and finds replies from a specific set of senders.
    Returns list of (sender_email, subject) tuples for real (non-auto) replies.
    """

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993

    def __init__(self, account_email: str, password: str, timeout: int = 30):
        self.email = account_email
        self.password = password
        self.timeout = timeout
        self._client: Optional[aioimaplib.IMAP4_SSL] = None

    async def connect(self) -> bool:
        """Connect and authenticate. Returns True on success."""
        try:
            self._client = aioimaplib.IMAP4_SSL(
                host=self.IMAP_HOST,
                port=self.IMAP_PORT,
                timeout=self.timeout,
            )
            await self._client.wait_hello_from_server()
            resp = await self._client.login(self.email, self.password)
            if resp.result != "OK":
                logger.warning("[%s] IMAP login failed: %s", self.email, resp)
                return False
            logger.debug("[%s] IMAP connected", self.email)
            return True
        except Exception as exc:
            logger.warning("[%s] IMAP connection error: %s", self.email, exc)
            return False

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.logout()
            except Exception:
                pass
            self._client = None

    async def get_replies_from(
        self, expected_senders: List[str], since_days: int = 7
    ) -> List[Tuple[str, str]]:
        """
        Scan INBOX for messages from `expected_senders`.
        Returns list of (sender_email, subject) for real replies only.
        `since_days` limits search to recent messages (default 7 days).
        """
        if not self._client:
            return []

        sender_set: Set[str] = {s.lower().strip() for s in expected_senders}
        results: List[Tuple[str, str]] = []

        try:
            # Select INBOX (read-only to avoid marking messages as read)
            resp = await self._client.select("INBOX", readonly=True)
            if resp.result != "OK":
                logger.warning("[%s] Cannot select INBOX: %s", self.email, resp)
                return []

            # Build IMAP search query for recent messages
            from datetime import date, timedelta
            since_date = (date.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")
            search_criteria = f'(SINCE "{since_date}")'

            resp, data = await self._client.search(search_criteria)
            if resp != "OK" or not data or not data[0]:
                return []

            msg_ids = data[0].decode().split()
            if not msg_ids:
                return []

            logger.debug("[%s] INBOX has %d messages in last %d days",
                         self.email, len(msg_ids), since_days)

            # Fetch headers only (faster than full message)
            # Batch fetch to avoid sending too many commands
            batch_size = 50
            for i in range(0, len(msg_ids), batch_size):
                batch = msg_ids[i:i + batch_size]
                id_range = ",".join(batch)

                resp, messages = await self._client.fetch(
                    id_range, "(BODY[HEADER.FIELDS (FROM SUBJECT)])"
                )
                if resp != "OK":
                    continue

                for item in messages:
                    if not isinstance(item, bytes) or not item.strip():
                        continue
                    try:
                        msg = email.message_from_bytes(item)
                        raw_from = msg.get("From", "")
                        raw_subject = msg.get("Subject", "")

                        from_decoded = _decode_header_value(raw_from)
                        subject_decoded = _decode_header_value(raw_subject)

                        _, addr = parseaddr(from_decoded)
                        sender_clean = addr.lower().strip()

                        if not sender_clean:
                            continue
                        if sender_clean not in sender_set:
                            continue
                        if _is_autoresponse(subject_decoded, sender_clean):
                            logger.debug("[%s] Auto-response from %s — skipped",
                                         self.email, sender_clean)
                            continue

                        results.append((sender_clean, subject_decoded))
                        logger.info("[%s] Reply found from %s: '%s'",
                                    self.email, sender_clean, subject_decoded[:60])

                    except Exception as exc:
                        logger.debug("Header parse error: %s", exc)

        except Exception as exc:
            logger.error("[%s] IMAP scan error: %s", self.email, exc)

        # Deduplicate by sender
        seen: Set[str] = set()
        unique = []
        for sender, subj in results:
            if sender not in seen:
                seen.add(sender)
                unique.append((sender, subj))
        return unique

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()


async def check_replies_imap(
    account_email: str,
    password: str,
    expected_senders: List[str],
    since_days: int = 7,
) -> List[str]:
    """
    Convenience wrapper: connects, checks replies, disconnects.
    Returns list of sender emails that have replied (non-auto-response).
    Falls back to empty list on IMAP auth failure (caller should use browser).
    """
    async with IMAPReplyChecker(account_email, password) as checker:
        if not checker._client:
            # IMAP failed (wrong password / App Password not set)
            logger.warning("[%s] IMAP unavailable — reply check skipped", account_email)
            return []
        pairs = await checker.get_replies_from(expected_senders, since_days=since_days)
        return [sender for sender, _ in pairs]
