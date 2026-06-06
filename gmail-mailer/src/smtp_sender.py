"""
SMTP sender — sends emails directly via smtp.gmail.com:587 (STARTTLS).

This is the same mechanism as Google Apps Script's GmailApp.sendEmail().
- No browser needed → 10-20× faster than Playwright compose
- 100 emails per account in ~1-2 minutes (vs ~7 minutes via browser)
- Proper email headers (Message-ID, Date, MIME) to maximise deliverability
- Handles App Passwords (required when 2FA is enabled on the account)

Spam score improvements:
  - multipart/alternative (HTML + plain text fallback) — required
  - Proper Message-ID in <random@gmail.com> format
  - X-Mailer header omitted (no robot fingerprint)
  - Content-Transfer-Encoding: quoted-printable (correct for non-ASCII)
"""
from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import re
import string
import time
from email.message import EmailMessage
from typing import Callable, Optional, Tuple

import aiosmtplib

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _make_message_id(sender_email: str) -> str:
    """Generate a proper RFC-5322 Message-ID."""
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=20))
    ts = int(time.time())
    domain = sender_email.split("@")[-1]
    return f"<{rand}.{ts}@{domain}>"


class SMTPSender:
    """
    Async SMTP sender for one Gmail account.
    Keeps a single authenticated connection open for the whole batch.
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
                "[%s] SMTP auth failed — use App Password if 2FA is enabled: %s",
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

    def _build_message(
        self,
        to: str,
        subject: str,
        body_html: str,
        reply_to: str = "",
    ) -> EmailMessage:
        """
        Build a proper multipart/alternative email message.
        HTML + plain text fallback + correct headers.
        """
        msg = EmailMessage()
        msg["From"] = email.utils.formataddr((self.display_name, self.email))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=False)
        msg["Message-ID"] = _make_message_id(self.email)
        if reply_to:
            msg["Reply-To"] = reply_to

        # Plain text fallback (important for spam score)
        plain = _html_to_plain(body_html)
        msg.set_content(plain, charset="utf-8")

        # HTML version
        msg.add_alternative(body_html, subtype="html", charset="utf-8")

        return msg

    async def send_one(
        self,
        to: str,
        subject: str,
        body_html: str,
        reply_to: str = "",
    ) -> bool:
        """Send a single email. Returns True on success."""
        if not self._smtp or not self._smtp.is_connected:
            ok = await self.connect()
            if not ok:
                return False

        msg = self._build_message(to, subject, body_html, reply_to)

        try:
            await self._smtp.send_message(msg)
            logger.debug("[%s] SMTP sent → %s", self.email, to)
            return True
        except aiosmtplib.SMTPRecipientRefused as exc:
            logger.warning("[%s] Recipient refused %s: %s", self.email, to, exc)
            return False
        except aiosmtplib.SMTPSenderRefused as exc:
            logger.error("[%s] Sender refused (account may be rate-limited): %s", self.email, exc)
            return False
        except aiosmtplib.SMTPException as exc:
            logger.warning("[%s] SMTP error → %s: %s", self.email, to, exc)
            self._smtp = None  # force reconnect next time
            return False
        except Exception as exc:
            logger.error("[%s] Unexpected send error → %s: %s", self.email, to, exc)
            self._smtp = None
            return False

    async def send_batch(
        self,
        recipients: list,
        delay_range: Tuple[float, float] = (0.5, 1.5),
        progress_callback: Optional[Callable] = None,
    ) -> Tuple[int, int]:
        """
        Send a batch. Returns (sent_count, failed_count).
        recipients: list of dicts with keys: email, subject, body_html
        """
        connected = await self.connect()
        if not connected:
            return 0, len(recipients)

        sent = 0
        failed = 0

        try:
            for rec in recipients:
                ok = await self.send_one(
                    to=rec["email"],
                    subject=rec.get("subject", ""),
                    body_html=rec.get("body_html", ""),
                )
                if ok:
                    sent += 1
                else:
                    failed += 1

                if progress_callback:
                    if asyncio.iscoroutinefunction(progress_callback):
                        await progress_callback(self.email, rec["email"], ok)
                    else:
                        progress_callback(self.email, rec["email"], ok)

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
    """Strip HTML tags to produce a plain text fallback."""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
                  r"\2 (\1)", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
