"""
Telegram integration:
  1. Send notifications (campaign stats, blocked accounts, exhausted accounts).
  2. Generate unique links via a separate Telegram bot API.

Both operations are async-safe and will not crash the campaign on failure.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)


# ─────────────────────────── notifications ───────────────────────────────────

async def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    if not bot_token or not chat_id:
        log.debug("Telegram not configured, skipping notification")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
            if resp.status_code == 200:
                return True
            log.warning("Telegram API returned %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)
    return False


async def notify_blocked(bot_token: str, chat_id: str, account_email: str, reason: str) -> None:
    await send_telegram_message(
        bot_token, chat_id,
        f"🚫 <b>Account blocked</b>\n"
        f"Email: <code>{account_email}</code>\n"
        f"Reason: {reason}",
    )


async def notify_exhausted(bot_token: str, chat_id: str, account_email: str) -> None:
    await send_telegram_message(
        bot_token, chat_id,
        f"⚠️ <b>Account exhausted</b>\n"
        f"<code>{account_email}</code> has reached the total send limit.\n"
        f"Please add a replacement account.",
    )


async def notify_daily_stats(bot_token: str, chat_id: str, stats: dict) -> None:
    msg = (
        f"📊 <b>Daily Campaign Stats — {stats['date']}</b>\n\n"
        f"First emails sent:   <b>{stats['first_sent']}</b>\n"
        f"Replies received:    <b>{stats['replied']}</b>\n"
        f"Second emails sent:  <b>{stats['second_sent']}</b>\n\n"
        f"Active accounts:     {stats['active_accounts']}\n"
        f"Exhausted accounts:  {stats['exhausted_accounts']}\n"
        f"Blocked accounts:    {stats['blocked_accounts']}"
    )
    await send_telegram_message(bot_token, chat_id, msg)


# ─────────────────────────── link generation ─────────────────────────────────

async def generate_unique_link(
    link_bot_token: str,
    link_bot_api_url: str,
    recipient_email: str,
) -> Optional[str]:
    """
    Call your Telegram bot's custom API to generate a unique tracking link.

    The API contract (to be confirmed with your bot developer):
      POST {link_bot_api_url}
      Headers: Authorization: Bearer {link_bot_token}
      Body:    {"recipient": "<email>"}
      Returns: {"link": "https://…"}

    Adjust the request/response parsing below once you have the docs.
    """
    if not link_bot_api_url:
        log.warning("link_bot_api_url not configured — cannot generate link")
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                link_bot_api_url,
                json={"recipient": recipient_email},
                headers={"Authorization": f"Bearer {link_bot_token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                link = data.get("link") or data.get("url") or data.get("result")
                if link:
                    log.info("Generated link for %s: %s", recipient_email, link)
                    return str(link)
                log.warning("Unexpected link API response: %s", data)
            else:
                log.warning("Link API returned %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.error("Link generation failed for %s: %s", recipient_email, exc)

    return None
