"""
Two-factor authentication handler.

2fa.fb.tools URL format (discovered from BAS script):
  https://2fa.fb.tools/<key>
  The OTP is shown directly on the page — no form to fill.

Supports:
  1. TOTP via pyotp (standard Base32 TOTP seed — generated locally, instant)
  2. 2fa.fb.tools/<key> URL — open page, read the 6-digit code displayed
  3. Manual fallback — stdin prompt with configurable timeout
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from typing import Callable, Optional

import pyotp

logger = logging.getLogger(__name__)

# Standard Base32 TOTP seed: 16–64 uppercase letters + digits 2–7
_TOTP_RE = re.compile(r"^[A-Z2-7]{16,64}$")


def _looks_like_totp_seed(key: str) -> bool:
    return bool(_TOTP_RE.match(key.upper().replace(" ", "")))


async def get_totp_code(key: str, page=None) -> Optional[str]:
    """
    Given a 2FA key, return a 6-digit OTP string.

    Strategy:
      1. If key looks like a standard Base32 TOTP seed → pyotp (local, instant)
      2. Otherwise → open https://2fa.fb.tools/<key> and read the code
    """
    if not key:
        return None

    clean_key = key.strip().upper().replace(" ", "")

    if _looks_like_totp_seed(clean_key):
        totp = pyotp.TOTP(clean_key)
        code = totp.now()
        logger.debug("TOTP code via pyotp: %s", code)
        return code

    if page is None:
        logger.error("Cannot use 2fa.fb.tools without a Playwright page")
        return None

    return await _get_code_from_fb_tools(page, key.strip())


async def _get_code_from_fb_tools(page, key: str) -> Optional[str]:
    """
    Open https://2fa.fb.tools/<key> — the site shows the OTP directly in
    the page body without any interaction required.
    Read the first 6-digit number found on the page.
    """
    encoded_key = urllib.parse.quote(key, safe="")
    url = f"https://2fa.fb.tools/{encoded_key}"
    logger.debug("Opening 2fa.fb.tools URL: %s", url)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Wait a moment for the OTP counter to update
        await asyncio.sleep(1.5)

        # The page renders the 6-digit code — find it in the page text
        body_text = await page.inner_text("body")
        codes = re.findall(r"\b(\d{6})\b", body_text)
        if codes:
            code = codes[0]
            logger.debug("2fa.fb.tools returned OTP: %s", code)
            return code

        # Broader fallback: any 6 consecutive digits on the page
        match = re.search(r"(\d{6})", body_text)
        if match:
            return match.group(1)

        logger.error("No 6-digit code found on 2fa.fb.tools page (key=%s…)", key[:6])

    except Exception as exc:
        logger.error("2fa.fb.tools error: %s", exc)

    return None


async def wait_for_manual_code(
    account_email: str = "",
    prompt_callback: Optional[Callable] = None,
    timeout: int = 120,
) -> Optional[str]:
    """
    For accounts without a 2FA key: prompt the operator.
    On headless VPS blocks for `timeout` seconds; returns None on timeout.
    """
    msg = f"[2FA required] {account_email} — enter 6-digit code (timeout {timeout}s): "
    if prompt_callback:
        if asyncio.iscoroutinefunction(prompt_callback):
            await prompt_callback(account_email)
        else:
            prompt_callback(account_email)

    try:
        loop = asyncio.get_event_loop()
        code = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: input(msg).strip()),
            timeout=timeout,
        )
        if re.match(r"^\d{6}$", code):
            return code
        logger.warning("Invalid manual 2FA code: '%s'", code)
    except asyncio.TimeoutError:
        logger.warning("Manual 2FA timed out for %s — skipping account", account_email)

    return None
