"""
Telegram notifier and unique-link generator.

Notifier:  uses python-telegram-bot to send alerts to the admin chat.
Link gen:  calls a secondary Telegram bot that returns a unique URL per recipient.
           The exact API of your link-bot is configurable; currently assumes the bot
           responds to a /generate_link <recipient_email> command and replies with a URL.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+")


class TelegramNotifier:
    """Send admin notifications via a Telegram bot."""

    def __init__(self, bot_token: str, admin_chat_id: int):
        self.bot_token = bot_token
        self.chat_id = admin_chat_id
        self._base = f"https://api.telegram.org/bot{bot_token}"

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.bot_token or not self.chat_id:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                }
                async with session.post(
                    f"{self._base}/sendMessage", json=payload, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning("Telegram send failed %d: %s", resp.status, body[:200])
                        return False
                    return True
        except Exception as exc:
            logger.error("Telegram notification error: %s", exc)
            return False

    async def send_daily_stats(self, stats: dict) -> None:
        msg = (
            f"📊 <b>Daily stats — {stats['date']}</b>\n"
            f"First emails sent:  <b>{stats['first_sent']}</b>\n"
            f"Replies received:   <b>{stats['replies']}</b>\n"
            f"Second emails sent: <b>{stats['second_sent']}</b>\n"
            f"Active accounts:    <b>{stats['active_accounts']}</b>"
        )
        await self.send(msg)

    async def alert_account_blocked(self, email: str, reason: str = "") -> None:
        await self.send(f"🚫 Account BLOCKED: <code>{email}</code>\n{reason}")

    async def alert_account_exhausted(self, email: str) -> None:
        await self.send(
            f"⚠️ Account EXHAUSTED (300 sends): <code>{email}</code>\n"
            "Please add a replacement account."
        )

    async def alert_accounts_low(self, remaining: int) -> None:
        await self.send(
            f"⚠️ Only <b>{remaining}</b> active accounts remaining. "
            "Please add fresh accounts soon."
        )


class LinkGenerator:
    """
    Generates unique links by messaging a Telegram bot.

    Protocol (adjustable once you share your bot's API docs):
      1. Send /generate_link <recipient_email> to LINK_BOT_CHAT_ID
      2. Poll getUpdates until the bot replies with a URL
      3. Return the URL string

    If your bot uses a different command or HTTP webhook, adapt _request_link().
    """

    def __init__(self, bot_token: str, chat_id: int, timeout: int = 30):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._last_update_id: int = 0

    async def generate_link(self, recipient_email: str) -> Optional[str]:
        """Request and return a unique link for the given recipient."""
        try:
            sent = await self._send_command(recipient_email)
            if not sent:
                return None

            # Poll for the bot's reply
            deadline = asyncio.get_event_loop().time() + self.timeout
            while asyncio.get_event_loop().time() < deadline:
                url = await self._poll_for_link()
                if url:
                    logger.info("Generated link for %s: %s", recipient_email, url)
                    return url
                await asyncio.sleep(2)

            logger.error("Timed out waiting for link for %s", recipient_email)
            return None

        except Exception as exc:
            logger.error("LinkGenerator error for %s: %s", recipient_email, exc)
            return None

    async def _send_command(self, recipient_email: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": self.chat_id,
                    "text": f"/generate_link {recipient_email}",
                }
                async with session.post(
                    f"{self._base}/sendMessage",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    return resp.status == 200
        except Exception as exc:
            logger.error("Failed to send /generate_link command: %s", exc)
            return False

    async def _poll_for_link(self) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "offset": self._last_update_id + 1,
                    "timeout": 5,
                    "allowed_updates": ["message"],
                }
                async with session.get(
                    f"{self._base}/getUpdates",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    data = await resp.json()

            for update in data.get("result", []):
                self._last_update_id = max(self._last_update_id, update["update_id"])
                msg = update.get("message", {})
                text = msg.get("text", "")
                # Look for a URL in the bot's reply
                match = _URL_RE.search(text)
                if match:
                    return match.group(0)

        except Exception as exc:
            logger.debug("Poll error: %s", exc)

        return None
