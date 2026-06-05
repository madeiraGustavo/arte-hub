"""
2FA TOTP code generation.

Primary method: pyotp (fast, offline, no browser needed).
Fallback  method: Playwright → 2fa.fb.tools (if pyotp fails or key format unknown).

The 2fa_key stored in account files is a standard Base32 TOTP secret
(the same string you'd scan as a QR code in Google Authenticator).
Most account exporters store it this way.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

log = logging.getLogger(__name__)

# ─────────────────────────── offline TOTP via pyotp ─────────────────────────

def get_totp_code_offline(two_fa_key: str) -> Optional[str]:
    """
    Returns current 6-digit TOTP code from the secret key.
    Returns None if key is invalid or pyotp is unavailable.
    """
    try:
        import pyotp  # type: ignore
        totp = pyotp.TOTP(two_fa_key.strip().replace(" ", ""))
        code = totp.now()
        log.debug("TOTP code generated offline: %s", code)
        return code
    except Exception as exc:
        log.warning("pyotp failed for key %s…: %s", two_fa_key[:8], exc)
        return None


def wait_for_fresh_totp(two_fa_key: str) -> str:
    """
    Waits until there are at least 10 seconds remaining in the current
    30-second TOTP window, then returns the code.
    This prevents race conditions where the code expires mid-login.
    """
    try:
        import pyotp  # type: ignore
        totp = pyotp.TOTP(two_fa_key.strip().replace(" ", ""))
        remaining = 30 - (int(time.time()) % 30)
        if remaining < 10:
            log.debug("TOTP window ending soon (%ds), waiting for next window…", remaining)
            time.sleep(remaining + 1)
        return totp.now()
    except ImportError:
        # Fallback: just generate without waiting
        return get_totp_code_offline(two_fa_key) or ""


# ─────────────────────────── online fallback via browser ────────────────────

async def get_totp_code_via_browser(page, two_fa_key: str) -> Optional[str]:
    """
    Navigate to 2fa.fb.tools, enter the key, extract the OTP code.
    `page` is an already-opened Playwright page object.
    Uses a separate tab to avoid disturbing the Gmail login flow.
    """
    try:
        context = page.context
        tab = await context.new_page()
        try:
            await tab.goto("https://2fa.fb.tools/", timeout=20000)
            await tab.wait_for_load_state("networkidle", timeout=15000)

            # The site has a single text input for the secret key
            input_sel = 'input[type="text"], input[placeholder*="key" i], input[placeholder*="secret" i], #secret'
            await tab.wait_for_selector(input_sel, timeout=8000)
            await tab.fill(input_sel, two_fa_key.strip())

            # Some implementations auto-generate; others need a button click
            try:
                btn = tab.locator('button[type="submit"], button:has-text("Generate")')
                if await btn.count() > 0:
                    await btn.first.click()
                    await tab.wait_for_timeout(1500)
            except Exception:
                pass

            # Extract the displayed code — typically a 6-digit number
            code_sel = (
                '.code, #code, [class*="otp"], [class*="token"], '
                '[data-code], span:text-matches("^\\\\d{6}$")'
            )
            try:
                code_el = await tab.wait_for_selector(code_sel, timeout=8000)
                code = (await code_el.inner_text()).strip()
                code = re.sub(r"\D", "", code)[:6]
                if len(code) == 6:
                    log.info("Got 2FA code from 2fa.fb.tools: %s", code)
                    return code
            except Exception:
                # Last resort: grep the whole page text
                body = await tab.inner_text("body")
                match = re.search(r"\b(\d{6})\b", body)
                if match:
                    return match.group(1)

            log.warning("Could not extract code from 2fa.fb.tools page")
            return None
        finally:
            await tab.close()

    except Exception as exc:
        log.error("Browser 2FA failed: %s", exc)
        return None


# ─────────────────────────── unified entry point ────────────────────────────

async def resolve_2fa_code(two_fa_key: Optional[str], page=None,
                           strategy: str = "skip") -> Optional[str]:
    """
    Resolve 2FA code using the best available method.

    strategy:
      "skip"  — return None if no key, caller will mark account as needs-manual
      "wait"  — print prompt and read from stdin (blocks event loop briefly)
    """
    if not two_fa_key:
        if strategy == "wait":
            # In headless mode this blocks until the operator types the code
            code = input(
                "\n[2FA REQUIRED] Enter 6-digit code manually (or press Enter to skip): "
            ).strip()
            return code if len(code) == 6 else None
        return None

    # Try fast offline first
    code = wait_for_fresh_totp(two_fa_key)
    if code:
        return code

    # Fallback: browser-based extraction
    if page is not None:
        return await get_totp_code_via_browser(page, two_fa_key)

    log.error("No method available to generate 2FA code for key %s…", two_fa_key[:8])
    return None
