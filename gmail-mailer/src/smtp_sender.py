"""
SMTP sender — sends emails directly via smtp.gmail.com:587 (STARTTLS).

This is the same mechanism as Google Apps Script's GmailApp.sendEmail().
- No browser needed → 10-20× faster than Playwright compose
- 100 emails per account in ~1-2 minutes (vs ~7 minutes via browser)
- Supports HTML body, custom From display name, Reply-To
- Handles App Passwords (required when 2FA is enabled on the account)

Rate limiting:
  Gmail allows ~500 emails/day via SMTP for regular accounts.
  We stay well under that (max 100/day by design).
  Between sends we pause 0.5–1.5 s — enough to look non-robotic.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Optional, Tuple

import aiosmtplib

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


class SMTPSender:
    """
    Async SMTP sender for one Gmail account.
    Keeps a single authenticated connection open for the whole batch
    (same as GmailApp does internally) — very fast.
    """

    def __init__(
        self,
        account_email: str,
        password: str,
        display_name: str = "",
        timeout: int = 30,
    ):
        self.email = account_email
        self.password = password
        self.display_name = display_name or account_email.split("@")[0]
        self.timeout = timeout
        self._smtp: Optional[aiosmtplib.SMTP] = None

    async def connect(self) -> bool:
        """Open and authenticate SMTP connection. Returns True on success."""
        try:
            self._smtp = aiosmtplib.SMTP(
                hostname=SMTP_HOST,
                port=SMTP_PORT,
                timeout=self.timeout,
                start_tls=True,
            )
            await self._smtp.connect()
            await self._smtp.login(self.email, self.password)
            logger.info("[%s] SMTP connected", self.email)
            return True
        except aiosmtplib.SMTPAuthenticationError as exc:
            logger.error(
                "[%s] SMTP auth failed — check password or create App Password: %s",
                self.email, exc,
            )
            return False
        except Exception as exc:
            logger.error("[%s] SMTP connect error: %s", self.email, exc)
            return False

    async def disconnect(self) -> None:
        if self._smtp:
            try:
                await self._smtp.quit()
            except Exception:
                pass
            self._smtp = None

    async def send_one(
        self,
        to: str,
        subject: str,
        body_html: str,
        reply_to: str = "",
    ) -> bool:
        """
        Send a single email. Returns True on success.
        Reconnects automatically if connection was dropped.
        """
        if not self._smtp or not self._smtp.is_connected:
            ok = await self.connect()
            if not ok:
                return False

        msg = EmailMessage()
        msg["From"] = f"{self.display_name} <{self.email}>"
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to

        # Set plain text fallback + HTML
        plain = _html_to_plain(body_html)
        msg.set_content(plain)
        msg.add_alternative(body_html, subtype="html")

        try:
            await self._smtp.send_message(msg)
            logger.debug("[%s] SMTP sent → %s", self.email, to)
            return True
        except aiosmtplib.SMTPRecipientRefused as exc:
            # Destination address rejected — mark as bad
            logger.warning("[%s] Recipient refused %s: %s", self.email, to, exc)
            return False
        except aiosmtplib.SMTPSenderRefused as exc:
            logger.error("[%s] Sender refused (account may be limited): %s", self.email, exc)
            return False
        except aiosmtplib.SMTPException as exc:
            logger.warning("[%s] SMTP error sending to %s: %s", self.email, to, exc)
            # Try to reconnect for next message
            self._smtp = None
            return False
        except Exception as exc:
            logger.error("[%s] Unexpected send error to %s: %s", self.email, to, exc)
            self._smtp = None
            return False

    async def send_batch(
        self,
        recipients: list,          # list of dicts with 'email', 'subject', 'body_html'
        delay_range: Tuple[float, float] = (0.5, 1.5),
        progress_callback=None,    # optional async callable(account_email, recipient_email, ok)
    ) -> Tuple[int, int]:
        """
        Send a batch of emails from this account.
        Returns (sent_count, failed_count).
        Much faster than browser: no page loads, just API calls.
        """
        connected = await self.connect()
        if not connected:
            return 0, len(recipients)

        sent = 0
        failed = 0

        try:
            for rec in recipients:
                to = rec["email"]
                subject = rec.get("subject", "")
                body = rec.get("body_html", "")

                ok = await self.send_one(to, subject, body)

                if ok:
                    sent += 1
                else:
                    failed += 1

                if progress_callback:
                    await progress_callback(self.email, to, ok)

                # Small human-like delay between sends
                await asyncio.sleep(random.uniform(*delay_range))

        finally:
            await self.disconnect()

        logger.info("[%s] Batch done: %d sent, %d failed", self.email, sent, failed)
        return sent, failed

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()


def _html_to_plain(html: str) -> str:
    """Very simple HTML → plain text (strip tags, decode common entities)."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
