"""
Gmail automation via Playwright with stealth fingerprinting.

Each account runs in its own persistent browser context (isolated profile dir),
so cookies, localStorage, and fingerprints are fully separated.

Key flows:
  - login()            — authenticate, handle 2FA, save cookies
  - clear_inbox()      — delete all inbox messages (first-run only)
  - send_email()       — compose and send a single email
  - check_replies()    — scan Sent messages and find replies in Inbox
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeout,
    async_playwright,
)

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    logging.getLogger(__name__).warning(
        "playwright-stealth not installed — fingerprint hardening disabled"
    )

from .config import AppConfig
from .database import Database
from .two_factor import get_totp_code, wait_for_manual_code

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GMAIL_URL = "https://mail.google.com/mail/u/0/#inbox"
GMAIL_BASE = "https://mail.google.com"

# Bounce / auto-reply subjects (case-insensitive fragments)
AUTORESPONSE_PATTERNS = [
    "out of office", "auto-reply", "automatic reply", "autoreply",
    "away from", "vacation", "abwesenheit", "afwezig", "absent",
    "mailer-daemon", "delivery failure", "delivery status", "undeliverable",
    "mail delivery", "returned mail", "bounce", "noreply", "no-reply",
]

# Locales to rotate for fingerprint diversity
LOCALES = ["nl-NL", "fr-FR", "de-DE", "en-GB", "en-US", "nl-BE", "fr-BE"]

# Viewport pool (common desktop resolutions)
VIEWPORTS = [
    (1366, 768), (1440, 900), (1920, 1080), (1280, 800), (1536, 864),
]

# User-agent pool — modern Chrome on Windows/macOS/Linux
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def _random_fingerprint(seed: str) -> dict:
    """Deterministic-ish fingerprint derived from account email seed."""
    rng = random.Random(seed)
    w, h = rng.choice(VIEWPORTS)
    return {
        "viewport": {"width": w, "height": h},
        "user_agent": rng.choice(USER_AGENTS),
        "locale": rng.choice(LOCALES),
        "timezone_id": "Europe/Amsterdam",
        "device_scale_factor": rng.choice([1, 1, 1, 1.25, 1.5]),
        "color_scheme": "light",
    }


def _is_autoresponse(subject: str, sender: str) -> bool:
    low_subj = subject.lower()
    low_sender = sender.lower()
    if "mailer-daemon" in low_sender or "noreply" in low_sender or "no-reply" in low_sender:
        return True
    for pattern in AUTORESPONSE_PATTERNS:
        if pattern in low_subj:
            return True
    return False


async def _human_delay(min_s: float = 0.3, max_s: float = 1.2) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _human_type(page: Page, selector: str, text: str) -> None:
    """Type text with random delays between keystrokes."""
    el = page.locator(selector).first
    await el.click()
    await _human_delay(0.2, 0.5)
    for char in text:
        await el.type(char, delay=random.randint(40, 140))


# ---------------------------------------------------------------------------
# GmailAutomation
# ---------------------------------------------------------------------------

class GmailAutomation:
    """
    One instance per account. Manages a single persistent browser context.
    Thread-safety: use one asyncio task per instance.
    """

    def __init__(
        self,
        account: dict,
        config: AppConfig,
        db: Database,
        telegram_notifier=None,
    ):
        self.account = account  # dict from DB row
        self.config = config
        self.db = db
        self.notifier = telegram_notifier
        self.email = account["email"]
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._playwright = None
        self._browser = None

    # -----------------------------------------------------------------------
    # Context lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Launch Playwright and create the browser context for this account."""
        self._playwright = await async_playwright().start()

        profile_dir = Path(self.config.paths.profiles_dir) / re.sub(
            r"[^a-zA-Z0-9_.-]", "_", self.email
        )
        profile_dir.mkdir(parents=True, exist_ok=True)

        fp = _random_fingerprint(self.email)
        proxy_cfg = self.config.proxy

        launch_kwargs: dict = {
            "headless": self.config.browser.headless,
            "slow_mo": self.config.browser.slow_mo,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                f"--window-size={fp['viewport']['width']},{fp['viewport']['height']}",
            ],
        }

        context_kwargs: dict = {
            "viewport": fp["viewport"],
            "user_agent": fp["user_agent"],
            "locale": fp["locale"],
            "timezone_id": fp["timezone_id"],
            "device_scale_factor": fp["device_scale_factor"],
            "color_scheme": fp["color_scheme"],
            "permissions": ["notifications"],
        }

        # Proxy
        if proxy_cfg.server:
            proxy = {"server": proxy_cfg.server}
            if proxy_cfg.username:
                proxy["username"] = proxy_cfg.username
                proxy["password"] = proxy_cfg.password
            context_kwargs["proxy"] = proxy

        # Use a persistent context so session data survives across runs
        self._ctx = await self._playwright.chromium.launch_persistent_context(
            str(profile_dir),
            **launch_kwargs,
            **context_kwargs,
        )

        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

        if HAS_STEALTH and self.config.fingerprint.use_stealth:
            await stealth_async(self._page)

        logger.info("[%s] Browser context started (profile=%s)", self.email, profile_dir)

    async def stop(self) -> None:
        """Save cookies and close the browser context."""
        try:
            if self._ctx:
                cookies = await self._ctx.cookies()
                await self.db.save_cookies(self.email, cookies)
                await self._ctx.close()
        except Exception as exc:
            logger.warning("[%s] Error closing context: %s", self.email, exc)
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Login
    # -----------------------------------------------------------------------

    async def login(self) -> bool:
        """
        Authenticate to Gmail.
        Returns True on success, False on unrecoverable failure.
        Also handles first-login inbox cleanup.
        """
        page = self._page
        try:
            await page.goto("https://accounts.google.com/signin/v2/identifier",
                            wait_until="domcontentloaded", timeout=30_000)
            await _human_delay(1, 2)

            # Check if already logged in
            if "mail.google.com" in page.url and "inbox" in page.url.lower():
                logger.info("[%s] Already logged in (session cookie active)", self.email)
                return await self._post_login_check()

            # --- Step 1: email ---
            email_sel = 'input[type="email"]'
            await page.wait_for_selector(email_sel, timeout=15_000)
            await _human_type(page, email_sel, self.email)
            await _human_delay(0.5, 1)
            await page.keyboard.press("Enter")
            await _human_delay(1.5, 2.5)

            # --- Step 2: password ---
            pwd_sel = 'input[type="password"]'
            await page.wait_for_selector(pwd_sel, timeout=15_000)
            await _human_type(page, pwd_sel, self.account["password"])
            await _human_delay(0.5, 1)
            await page.keyboard.press("Enter")
            await _human_delay(2, 4)

            # --- Step 3: Check what appeared after password ---
            return await self._handle_post_password(page)

        except PWTimeout as exc:
            logger.error("[%s] Timeout during login: %s", self.email, exc)
            await self.db.set_account_status(self.email, "error", notes=str(exc))
            return False
        except Exception as exc:
            logger.error("[%s] Login error: %s", self.email, exc, exc_info=True)
            await self.db.set_account_status(self.email, "error", notes=str(exc))
            return False

    async def _handle_post_password(self, page: Page) -> bool:
        """Detect and handle 2FA, captcha, or successful redirect."""
        url = page.url
        body = await page.content()
        low_body = body.lower()

        # Captcha / account protection challenge
        if any(s in low_body for s in ["captcha", "verify it's you", "confirm your identity",
                                        "unusual activity", "blocked"]):
            logger.warning("[%s] Account blocked or challenged by Google", self.email)
            await self.db.set_account_status(self.email, "blocked", notes="Google challenge on login")
            if self.notifier:
                await self.notifier.send(f"🚫 Account BLOCKED on login: {self.email}")
            return False

        # 2FA prompt
        if any(s in low_body for s in ["2-step", "2fa", "authenticator", "verification code",
                                         "enter the code", "get a verification"]):
            return await self._handle_2fa(page)

        # Phone/backup email prompt — attempt to skip
        if "verify your phone" in low_body or "add phone" in low_body:
            skip = page.locator('button:has-text("Skip"), a:has-text("Not now")')
            if await skip.count() > 0:
                await skip.first.click()
                await _human_delay(1, 2)

        # Navigate to Gmail
        await page.goto(GMAIL_URL, wait_until="domcontentloaded", timeout=30_000)
        await _human_delay(2, 4)

        if "mail.google.com" not in page.url:
            logger.warning("[%s] Login didn't reach Gmail; URL=%s", self.email, page.url)
            return False

        return await self._post_login_check()

    async def _handle_2fa(self, page: Page) -> bool:
        two_fa_key = self.account.get("two_fa_key")

        if two_fa_key:
            # Use our TOTP / 2fa.fb.tools helper
            code = await get_totp_code(two_fa_key, page=page)
        else:
            # No key → ask operator or skip
            if self.notifier:
                await self.notifier.send(
                    f"⚠️ 2FA needed for {self.email} — no key stored. "
                    "Enter code in console within 2 minutes or account will be skipped."
                )
            code = await wait_for_manual_code(account_email=self.email, timeout=120)

        if not code:
            logger.warning("[%s] No 2FA code available — skipping account", self.email)
            await self.db.set_account_status(self.email, "error", notes="2FA unavailable")
            return False

        # Navigate back to 2FA code page if we changed URL
        if "2fa.fb.tools" in page.url:
            await page.goto("javascript:history.back()")
            await _human_delay(1, 2)

        code_input = page.locator(
            'input[type="tel"], input[aria-label*="code"], input[name*="code"]'
        ).first
        await code_input.fill(code)
        await _human_delay(0.5, 1)
        await page.keyboard.press("Enter")
        await _human_delay(2, 4)

        # Check for wrong-code error
        body = (await page.content()).lower()
        if "wrong" in body or "incorrect" in body or "invalid" in body:
            logger.error("[%s] Wrong 2FA code", self.email)
            return False

        # Proceed to Gmail
        await page.goto(GMAIL_URL, wait_until="domcontentloaded", timeout=30_000)
        await _human_delay(2, 4)
        return "mail.google.com" in page.url

    async def _post_login_check(self) -> bool:
        """After reaching Gmail: handle first-login inbox cleanup."""
        row = await self.db.get_account(self.email)
        if row and not row["first_login_completed"]:
            logger.info("[%s] First login — clearing inbox", self.email)
            await self.clear_inbox()
            await self.db.mark_first_login_done(self.email)
        return True

    # -----------------------------------------------------------------------
    # Inbox cleanup (first login only)
    # -----------------------------------------------------------------------

    async def clear_inbox(self) -> int:
        """
        Delete ALL messages in Inbox using Gmail's web UI.
        Returns number of deletion waves executed.
        """
        page = self._page
        waves = 0
        await page.goto(GMAIL_URL, wait_until="domcontentloaded", timeout=30_000)
        await _human_delay(2, 3)

        while True:
            # Check if inbox has any messages
            no_mail = page.locator(
                'td.TC:has-text("No conversations"), [data-tooltip="No conversations"]'
            )
            if await no_mail.count() > 0:
                logger.info("[%s] Inbox is empty", self.email)
                break

            # Click "Select all" checkbox
            select_all_chk = page.locator(
                'div[role="checkbox"][aria-label*="Select all"], '
                'span[data-tooltip="Select all conversations in inbox"], '
                '#:rg, [gh="sl"]'
            ).first
            try:
                await select_all_chk.wait_for(state="visible", timeout=8_000)
                await select_all_chk.click()
                await _human_delay(0.5, 1)
            except PWTimeout:
                logger.warning("[%s] Select-all checkbox not found — inbox may be empty", self.email)
                break

            # If Gmail shows "Select all X conversations in Inbox" banner — click it
            select_all_banner = page.locator(
                'span:has-text("Select all"), a:has-text("Select all")'
            ).first
            if await select_all_banner.count() > 0:
                await select_all_banner.click()
                await _human_delay(0.5, 1)

            # Click Delete (trash icon)
            delete_btn = page.locator(
                'div[data-tooltip="Delete"], button[aria-label="Delete"], '
                'div[act="10"]'
            ).first
            await delete_btn.wait_for(state="visible", timeout=8_000)
            await delete_btn.click()
            await _human_delay(1.5, 2.5)

            # Confirm if a dialog appears
            confirm = page.locator('button:has-text("OK"), button:has-text("Delete")').first
            if await confirm.count() > 0:
                await confirm.click()
                await _human_delay(1, 2)

            waves += 1
            logger.info("[%s] Inbox deletion wave %d complete", self.email, waves)
            await _human_delay(2, 3)

            if waves >= 20:
                logger.warning("[%s] Reached max deletion waves (20)", self.email)
                break

        return waves

    # -----------------------------------------------------------------------
    # Compose & send
    # -----------------------------------------------------------------------

    async def send_email(
        self,
        to: str,
        subject: str,
        body_html: str,
        send_delay: Tuple[float, float] = (2.0, 5.0),
    ) -> bool:
        """
        Compose and send a single email via Gmail web UI.
        Returns True on success.
        """
        page = self._page

        # Make sure we are in Gmail
        if "mail.google.com" not in page.url:
            await page.goto(GMAIL_URL, wait_until="domcontentloaded", timeout=30_000)
            await _human_delay(1.5, 3)

        # Click Compose
        compose_btn = page.locator(
            'div[gh="cm"], div[role="button"]:has-text("Compose"), '
            '.T-I.T-I-KE.L3'
        ).first
        try:
            await compose_btn.wait_for(state="visible", timeout=10_000)
        except PWTimeout:
            logger.warning("[%s] Compose button not found; reloading", self.email)
            await page.reload(wait_until="domcontentloaded")
            await _human_delay(2, 4)
            await compose_btn.wait_for(state="visible", timeout=10_000)

        await compose_btn.click()
        await _human_delay(0.8, 1.5)

        # Fill To
        to_field = page.locator(
            'textarea[name="to"], input[aria-label="To"], '
            'div[aria-label="To"] input, div[name="to"] textarea'
        ).first
        await to_field.wait_for(state="visible", timeout=8_000)
        await _human_type(page, to_field, to)
        await page.keyboard.press("Tab")
        await _human_delay(0.3, 0.7)

        # Fill Subject
        subj_field = page.locator('input[name="subjectbox"], input[aria-label="Subject"]').first
        await subj_field.fill(subject)
        await _human_delay(0.3, 0.7)

        # Fill body — paste HTML via clipboard API to preserve formatting
        body_area = page.locator('div[aria-label="Message Body"], div[role="textbox"]').first
        await body_area.click()
        await _human_delay(0.3, 0.6)

        # Use keyboard shortcut Ctrl+A and then paste, or just set innerHTML via JS
        await page.evaluate(
            """(html) => {
                const el = document.querySelector(
                    'div[aria-label="Message Body"], div[role="textbox"]'
                );
                if (el) el.innerHTML = html;
            }""",
            body_html,
        )
        await _human_delay(0.5, 1)

        # Send — check for send button
        send_btn = page.locator(
            'div[data-tooltip="Send"], div[aria-label*="Send"], '
            'button[data-tooltip*="Send"]'
        ).first
        await send_btn.wait_for(state="visible", timeout=8_000)
        await send_btn.click()
        await _human_delay(*send_delay)

        # Detect errors
        error_el = page.locator(
            'div.Kj-JD-K7-K0, span:has-text("Address not found"), '
            'span:has-text("Couldn\'t find"), span:has-text("invalid")'
        )
        if await error_el.count() > 0:
            err_text = await error_el.first.inner_text()
            logger.warning("[%s] Send error for %s: %s", self.email, to, err_text)
            # Close compose window
            await page.keyboard.press("Escape")
            return False

        logger.info("[%s] Email sent to %s", self.email, to)
        return True

    # -----------------------------------------------------------------------
    # Reply checking
    # -----------------------------------------------------------------------

    async def check_replies(self, expected_senders: List[str]) -> List[str]:
        """
        Scan Inbox for replies from the list of expected_senders.
        Returns list of emails that have replied.
        """
        page = self._page
        replied: List[str] = []

        if not expected_senders:
            return replied

        sender_set = {e.lower() for e in expected_senders}

        await page.goto(GMAIL_URL, wait_until="domcontentloaded", timeout=30_000)
        await _human_delay(2, 3)

        # Iterate over pages of inbox
        page_num = 0
        while True:
            page_num += 1
            await _human_delay(1, 2)

            # Extract all email rows
            rows = await page.query_selector_all(
                'tr.zA'   # Gmail inbox rows
            )

            if not rows:
                logger.debug("[%s] No inbox rows found on page %d", self.email, page_num)
                break

            for row in rows:
                # Sender info
                sender_el = await row.query_selector('.yX.xY span[email], .zF')
                subject_el = await row.query_selector('.bqe, .bog')

                if not sender_el:
                    continue

                sender_email = (await sender_el.get_attribute("email") or "").lower().strip()
                subject_text = await subject_el.inner_text() if subject_el else ""

                if sender_email not in sender_set:
                    continue
                if _is_autoresponse(subject_text, sender_email):
                    logger.debug("[%s] Auto-response from %s — ignored", self.email, sender_email)
                    continue

                replied.append(sender_email)
                logger.info("[%s] Reply detected from %s", self.email, sender_email)

            # Go to next page if exists
            next_btn = page.locator(
                'div[aria-label="Older"], button[aria-label="Older"], '
                'div[act="19"]'
            ).first
            if await next_btn.count() == 0:
                break
            is_disabled = await next_btn.get_attribute("aria-disabled")
            if is_disabled == "true":
                break
            await next_btn.click()
            await _human_delay(1.5, 2.5)

            if page_num >= 10:
                break

        return list(set(replied))
