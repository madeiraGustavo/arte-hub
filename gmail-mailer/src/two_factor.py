"""
Two-factor authentication handler.
Supports:
  1. TOTP via pyotp (standard RFC-6238, works with Google Authenticator seeds)
  2. 2fa.fb.tools — when the key is NOT a standard TOTP seed but an FB-style key,
     we open the website via Playwright and scrape the OTP.
  3. Manual fallback — prints prompt and waits for stdin (headless-safe via
     an optional callback).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable, Optional

import pyotp

logger = logging.getLogger(__name__)

# Regex: standard Base32 TOTP seed looks like JBSWY3DPEHPK3PXP (16–32 chars, uppercase)
_TOTP_RE = re.compile(r"^[A-Z2-7]{16,64}$")


def _looks_like_totp_seed(key: str) -> bool:
    return bool(_TOTP_RE.match(key.upper().replace(" ", "")))


async def get_totp_code(key: str, page=None) -> Optional[str]:
    """
    Given a 2FA key, return a 6-digit OTP string.

    Strategy:
      - If key matches Base32 pattern → generate via pyotp (instant).
      - Otherwise → scrape https://2fa.fb.tools/ via Playwright.
    """
    if not key:
        return None

    clean_key = key.strip().upper().replace(" ", "")

    if _looks_like_totp_seed(clean_key):
        totp = pyotp.TOTP(clean_key)
        code = totp.now()
        logger.debug("Generated TOTP code via pyotp: %s", code)
        return code

    # Fall back to 2fa.fb.tools web scraping
    if page is None:
        logger.error("Cannot use 2fa.fb.tools without an active Playwright page")
        return None

    return await _scrape_fb_tools(page, key)


async def _scrape_fb_tools(page, key: str) -> Optional[str]:
    """Open 2fa.fb.tools in the current page, enter the key, read the OTP."""
    try:
        logger.debug("Opening 2fa.fb.tools to resolve key")
        await page.goto("https://2fa.fb.tools/", wait_until="domcontentloaded", timeout=30_000)

        # The site has a single input for the secret key
        input_sel = 'input[type="text"], input[placeholder*="key"], input[placeholder*="secret"]'
        await page.wait_for_selector(input_sel, timeout=10_000)
        await page.fill(input_sel, key)

        # OTP code is usually displayed automatically or after clicking generate
        btn_sel = 'button[type="submit"], button:has-text("Generate"), button:has-text("Get")'
        btn = page.locator(btn_sel)
        if await btn.count() > 0:
            await btn.first.click()

        # Wait for a 6-digit number to appear
        await asyncio.sleep(2)
        code_sel = ".code, .otp, [class*='code'], [class*='otp'], span:has-text(/^\\d{6}$/)"
        try:
            element = page.locator("text=/^\\d{6}$/").first
            await element.wait_for(timeout=8_000)
            code = (await element.inner_text()).strip()
            logger.debug("Scraped OTP from 2fa.fb.tools: %s", code)
            return code
        except Exception:
            # Broader fallback: find any 6-digit string on the page
            body = await page.inner_text("body")
            match = re.search(r"\b(\d{6})\b", body)
            if match:
                return match.group(1)

    except Exception as exc:
        logger.error("2fa.fb.tools scraping failed: %s", exc)

    return None


async def wait_for_manual_code(
    prompt_callback: Optional[Callable[[str], None]] = None,
    account_email: str = "",
    timeout: int = 120,
) -> Optional[str]:
    """
    For accounts without a 2FA key: print a prompt and wait for operator input.
    On a headless VPS this blocks for `timeout` seconds; if no code is entered,
    returns None and the account is skipped.

    prompt_callback: optional async/sync callable that sends the prompt somewhere
                     (e.g. Telegram) — receives the account_email string.
    """
    msg = f"[2FA] Manual code needed for {account_email}. Enter 6-digit code: "
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
