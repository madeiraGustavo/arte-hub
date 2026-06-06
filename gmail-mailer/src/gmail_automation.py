"""
Gmail automation via Playwright with stealth fingerprinting.

Each account runs in its own persistent browser context (isolated profile dir),
so cookies, localStorage, and fingerprints are fully separated.

Key flows:
  - login()            — authenticate, handle 2FA + captcha, save cookies
  - clear_inbox()      — delete all inbox messages (first-run only)
  - send_email()       — compose and send a single email with retry logic
  - check_replies()    — scan Inbox using IMAP (fast) or browser (fallback)
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    async_playwright,
)

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

from .captcha_solver import CaptchaSolver
from .config import AppConfig
from .database import Database
from .two_factor import get_totp_code, wait_for_manual_code

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"

VIEWPORTS = [
    (1366, 768), (1440, 900), (1920, 1080), (1280, 800), (1536, 864),
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

LOCALES = ["nl-NL", "fr-FR", "de-DE", "en-GB", "en-US", "nl-BE", "fr-BE"]


def _random_fingerprint(seed: str) -> dict:
    rng = random.Random(seed)
    w, h = rng.choice(VIEWPORTS)
    return {
        "viewport": {"width": w, "height": h},
        "user_agent": rng.choice(USER_AGENTS),
        "locale": rng.choice(LOCALES),
        "timezone_id": "Europe/Amsterdam",
        "device_scale_factor": rng.choice([1, 1, 1, 1.25]),
        "color_scheme": "light",
    }


async def _delay(min_s: float = 0.3, max_s: float = 1.2) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _human_type(page: Page, locator, text: str) -> None:
    """Type text character by character with random delays."""
    el = locator.first if hasattr(locator, "first") else locator
    await el.click()
    await _delay(0.2, 0.5)
    for char in text:
        await el.type(char, delay=random.randint(40, 130))


# ---------------------------------------------------------------------------
# GmailAutomation
# ---------------------------------------------------------------------------

class GmailAutomation:
    """
    One instance per account. Manages a single persistent browser context.
    Use one asyncio task per instance.
    """

    def __init__(self, account: dict, config: AppConfig, db: Database,
                 telegram_notifier=None):
        self.account = account
        self.config = config
        self.db = db
        self.notifier = telegram_notifier
        self.email = account["email"]
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._playwright = None
        self._captcha_solver: Optional[CaptchaSolver] = None

        if config.captcha.api_key:
            self._captcha_solver = CaptchaSolver(
                api_key=config.captcha.api_key,
                service=config.captcha.service,
            )

    # -----------------------------------------------------------------------
    # Context lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
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
        }

        if proxy_cfg.server:
            proxy = {"server": proxy_cfg.server}
            if proxy_cfg.username:
                proxy["username"] = proxy_cfg.username
                proxy["password"] = proxy_cfg.password
            context_kwargs["proxy"] = proxy

        self._ctx = await self._playwright.chromium.launch_persistent_context(
            str(profile_dir), **launch_kwargs, **context_kwargs
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

        if HAS_STEALTH and self.config.fingerprint.use_stealth:
            await stealth_async(self._page)

        logger.info("[%s] Browser context started", self.email)

    async def stop(self) -> None:
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
        page = self._page
        try:
            await page.goto(
                "https://accounts.google.com/signin/v2/identifier",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await _delay(1, 2)

            # Already logged in?
            if "mail.google.com" in page.url:
                return await self._post_login_check()

            # ---- Email step ----
            email_input = page.locator('input[type="email"]')
            await email_input.wait_for(state="visible", timeout=15_000)
            await _human_type(page, email_input, self.email)
            await _delay(0.5, 1)
            await page.keyboard.press("Enter")
            await _delay(1.5, 2.5)

            # ---- Password step ----
            pwd_input = page.locator('input[type="password"]')
            await pwd_input.wait_for(state="visible", timeout=15_000)
            await _human_type(page, pwd_input, self.account["password"])
            await _delay(0.5, 1)
            await page.keyboard.press("Enter")
            await _delay(2, 4)

            return await self._handle_post_password(page)

        except PWTimeout as exc:
            logger.error("[%s] Login timeout: %s", self.email, exc)
            await self.db.set_account_status(self.email, "error", notes=str(exc))
            return False
        except Exception as exc:
            logger.error("[%s] Login error: %s", self.email, exc, exc_info=True)
            await self.db.set_account_status(self.email, "error", notes=str(exc))
            return False

    async def _handle_post_password(self, page: Page) -> bool:
        content = (await page.content()).lower()

        # Captcha
        if "captcha" in content or "i'm not a robot" in content:
            solved = await self._handle_captcha(page)
            if not solved:
                await self.db.set_account_status(self.email, "blocked", notes="captcha unsolved")
                if self.notifier:
                    await self.notifier.send(f"🚫 Captcha unsolved: <code>{self.email}</code>")
                return False
            await _delay(2, 3)
            content = (await page.content()).lower()

        # Account blocked / challenge
        if any(s in content for s in [
            "verify it's you", "confirm your identity", "unusual activity",
            "suspicious activity", "account has been", "disabled"
        ]):
            await self.db.set_account_status(self.email, "blocked", notes="Google challenge")
            if self.notifier:
                await self.notifier.send(f"🚫 Account blocked: <code>{self.email}</code>")
            return False

        # Wrong password
        if any(s in content for s in ["wrong password", "incorrect password",
                                        "password is incorrect"]):
            await self.db.set_account_status(self.email, "error", notes="wrong password")
            logger.error("[%s] Wrong password", self.email)
            return False

        # 2FA
        if any(s in content for s in [
            "2-step", "2fa", "verification code", "authenticator",
            "enter the code", "enter a verification"
        ]):
            return await self._handle_2fa(page)

        # Recovery email challenge — try submail
        if "challengetype" in content or "recovery" in content:
            submail = self.account.get("submail", "")
            if submail and "@" in submail:
                sub_input = page.locator('input[type="email"]').first
                if await sub_input.count() > 0:
                    await sub_input.fill(submail)
                    await page.keyboard.press("Enter")
                    await _delay(2, 3)

        # Skip phone prompts
        skip_btn = page.locator(
            'button:has-text("Skip"), a:has-text("Not now"), '
            'button:has-text("Not now"), a:has-text("Skip")'
        ).first
        if await skip_btn.count() > 0:
            await skip_btn.click()
            await _delay(1, 2)

        await page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=30_000)
        await _delay(2, 4)

        if "mail.google.com" not in page.url:
            logger.warning("[%s] Did not reach Gmail after login (url=%s)", self.email, page.url)
            return False

        return await self._post_login_check()

    async def _handle_2fa(self, page: Page) -> bool:
        two_fa_key = self.account.get("two_fa_key")

        if two_fa_key:
            code = await get_totp_code(two_fa_key, page=page)
        else:
            if self.notifier:
                await self.notifier.send(
                    f"⚠️ 2FA needed for <code>{self.email}</code> — no key. "
                    "Enter code in console (120s timeout) or account will be skipped."
                )
            code = await wait_for_manual_code(account_email=self.email, timeout=120)

        if not code:
            await self.db.set_account_status(self.email, "error", notes="2FA unavailable")
            return False

        # If we navigated to 2fa.fb.tools, go back
        if "2fa.fb.tools" in page.url:
            await page.go_back()
            await _delay(1, 2)

        code_input = page.locator(
            'input[type="tel"], input[aria-label*="code"], '
            'input[name*="code"], input[autocomplete="one-time-code"]'
        ).first
        await code_input.fill(code)
        await _delay(0.5, 1)
        await page.keyboard.press("Enter")
        await _delay(2, 4)

        body = (await page.content()).lower()
        if any(s in body for s in ["wrong", "incorrect", "invalid", "didn't work"]):
            logger.error("[%s] Wrong 2FA code", self.email)
            return False

        await page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=30_000)
        await _delay(2, 4)
        return "mail.google.com" in page.url

    async def _handle_captcha(self, page: Page) -> bool:
        """Try to solve reCAPTCHA v2 via solver service."""
        if not self._captcha_solver:
            logger.warning("[%s] Captcha encountered but no solver configured", self.email)
            return False

        # Find reCAPTCHA site key in page source
        src = await page.content()
        m = re.search(r'data-sitekey=["\']([^"\']+)["\']', src)
        if not m:
            logger.warning("[%s] reCAPTCHA sitekey not found", self.email)
            return False

        site_key = m.group(1)
        logger.info("[%s] Solving reCAPTCHA (sitekey=%s…)", self.email, site_key[:12])

        token = await self._captcha_solver.solve_recaptcha_v2(site_key, page.url)
        if not token:
            return False

        # Inject the token and submit
        await page.evaluate(
            """(token) => {
                const el = document.querySelector('[name="g-recaptcha-response"]');
                if (el) { el.value = token; }
                if (window.___grecaptcha_cfg) {
                    const id = Object.keys(window.___grecaptcha_cfg.clients)[0];
                    const cb = window.___grecaptcha_cfg.clients[id]?.U?.callback
                           || window.___grecaptcha_cfg.clients[id]?.S?.callback;
                    if (typeof cb === 'function') cb(token);
                }
            }""",
            token,
        )
        await _delay(1, 2)

        submit = page.locator('input[type="submit"], button[type="submit"]').first
        if await submit.count() > 0:
            await submit.click()

        return True

    async def _post_login_check(self) -> bool:
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
        """Delete ALL messages in Inbox. Returns number of deletion waves."""
        page = self._page
        waves = 0
        await page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=30_000)
        await _delay(2, 3)

        while waves < 20:
            # Is inbox empty?
            empty = page.locator(
                '[data-tooltip="No conversations"], .TC:has-text("No conversations")'
            )
            if await empty.count() > 0:
                break

            # Click "Select all" checkbox (top-left)
            chk = page.locator(
                'div[gh="sl"] span[role="checkbox"], '
                'div[class*="T-Jo"] span[role="checkbox"]'
            ).first
            try:
                await chk.wait_for(state="visible", timeout=8_000)
                await chk.click()
                await _delay(0.5, 1)
            except PWTimeout:
                break

            # Click "Select all X conversations in Inbox"
            all_banner = page.locator(
                ':text("Select all"), :text("conversations in Inbox")'
            ).first
            if await all_banner.count() > 0:
                await all_banner.click()
                await _delay(0.5, 1)

            # Delete
            del_btn = page.locator(
                'div[data-tooltip="Delete"], button[aria-label="Delete"]'
            ).first
            await del_btn.wait_for(state="visible", timeout=8_000)
            await del_btn.click()
            await _delay(1.5, 2.5)

            # Confirm dialog if present
            ok = page.locator('button:has-text("OK"), button:has-text("Delete")').first
            if await ok.count() > 0:
                await ok.click()
                await _delay(1, 2)

            waves += 1
            logger.info("[%s] Inbox deletion wave %d", self.email, waves)
            await _delay(2, 3)

        logger.info("[%s] Inbox cleared after %d waves", self.email, waves)
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
        Compose and send one email. Returns True on success.
        Retries once if compose window does not open.
        """
        page = self._page

        if "mail.google.com" not in page.url:
            await page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=30_000)
            await _delay(1.5, 3)

        # Make sure we are in Gmail and inbox is loaded
        await self._ensure_gmail_loaded(page)

        for attempt in range(2):
            ok = await self._do_compose_and_send(page, to, subject, body_html)
            if ok:
                await _delay(*send_delay)
                return True
            if attempt == 0:
                logger.warning("[%s] Compose attempt 1 failed — retrying", self.email)
                await _delay(3, 6)
                # Reload Gmail before retry
                await page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=30_000)
                await _delay(2, 4)

        logger.error("[%s] Failed to send to %s after 2 attempts", self.email, to)
        return False

    async def _ensure_gmail_loaded(self, page: Page) -> None:
        """Wait until Gmail inbox is fully loaded and compose button is visible."""
        compose_btn = page.locator(
            'div[gh="cm"], .T-I.T-I-KE.L3, div[role="button"]:has-text("Compose")'
        ).first
        try:
            await compose_btn.wait_for(state="visible", timeout=15_000)
        except PWTimeout:
            await page.reload(wait_until="domcontentloaded")
            await _delay(2, 4)
            await compose_btn.wait_for(state="visible", timeout=15_000)

    async def _do_compose_and_send(
        self, page: Page, to: str, subject: str, body_html: str
    ) -> bool:
        """Single compose+send attempt. Returns True on success."""
        try:
            # Click Compose
            compose_btn = page.locator(
                'div[gh="cm"], .T-I.T-I-KE.L3, div[role="button"]:has-text("Compose")'
            ).first
            await compose_btn.click()
            await _delay(0.8, 1.5)

            # Fill To
            to_field = page.locator(
                'textarea[name="to"], input[aria-label="To"], '
                'div[aria-label="To"] input'
            ).first
            await to_field.wait_for(state="visible", timeout=10_000)
            await to_field.fill(to)
            await page.keyboard.press("Tab")
            await _delay(0.4, 0.8)

            # Fill Subject
            subj_field = page.locator(
                'input[name="subjectbox"], input[aria-label="Subject"]'
            ).first
            await subj_field.fill(subject)
            await _delay(0.3, 0.7)

            # Inject HTML body via JS (preserves formatting, same as BAS approach)
            body_area = page.locator(
                'div[aria-label="Message Body"], div[role="textbox"][g_editable="true"]'
            ).first
            await body_area.click()
            await _delay(0.3, 0.5)

            # Use createContextualFragment approach from BAS
            await page.evaluate(
                """(html) => {
                    // Try TrustedTypes policy first (Chrome security requirement)
                    let trustedHTML;
                    if (window.trustedTypes && window.trustedTypes.createPolicy) {
                        try {
                            const policy = window.trustedTypes.createPolicy('gmailMailer', {
                                createHTML: (s) => s
                            });
                            trustedHTML = policy.createHTML(html);
                        } catch(e) {
                            // Policy already exists — use default
                            try {
                                trustedHTML = window.trustedTypes.defaultPolicy
                                    ? window.trustedTypes.defaultPolicy.createHTML(html)
                                    : html;
                            } catch(e2) { trustedHTML = html; }
                        }
                    } else {
                        trustedHTML = html;
                    }

                    const el = document.querySelector(
                        "div[aria-label='Message Body'], " +
                        "div[role='textbox'][g_editable='true']"
                    );
                    if (!el) return;
                    el.focus();
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    range.deleteContents();
                    const fragment = range.createContextualFragment(trustedHTML);
                    range.insertNode(fragment);
                }""",
                body_html,
            )
            await _delay(0.5, 1)

            # Wait for Send button to be available (same pattern as BAS IS_EXISTS_SEND)
            send_btn = page.locator(
                'div[data-tooltip*="Send"], div[aria-label*="Send"], '
                'button[data-tooltip*="Send"]'
            ).first

            # Poll until send button is visible (max 15s, as in BAS)
            try:
                await send_btn.wait_for(state="visible", timeout=15_000)
            except PWTimeout:
                logger.warning("[%s] Send button not visible", self.email)
                await page.keyboard.press("Escape")
                return False

            await send_btn.click()
            await _delay(1, 2)

            # Check for address-not-found / error toast
            error_el = page.locator(
                'span:has-text("Address not found"), '
                'span:has-text("Couldn\'t find"), '
                'span:has-text("Invalid address"), '
                'div.Kj-JD-K7-K0'
            )
            if await error_el.count() > 0:
                err = await error_el.first.inner_text()
                logger.warning("[%s] Address error for %s: %s", self.email, to, err[:80])
                await page.keyboard.press("Escape")
                return False

            logger.info("[%s] Sent → %s", self.email, to)
            return True

        except PWTimeout as exc:
            logger.warning("[%s] Compose timeout: %s", self.email, exc)
            await page.keyboard.press("Escape")
            return False
        except Exception as exc:
            logger.error("[%s] Compose error: %s", self.email, exc)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    # -----------------------------------------------------------------------
    # Reply checking (browser fallback — prefer IMAP via imap_checker.py)
    # -----------------------------------------------------------------------

    async def check_replies_browser(self, expected_senders: List[str]) -> List[str]:
        """
        Browser-based fallback for reply checking.
        Use imap_checker.check_replies_imap() first (it's faster).
        """
        page = self._page
        replied: List[str] = []

        if not expected_senders:
            return replied

        sender_set = {e.lower() for e in expected_senders}
        await page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=30_000)
        await _delay(2, 3)

        page_num = 0
        while page_num < 10:
            page_num += 1
            rows = await page.query_selector_all("tr.zA")

            for row in rows:
                sender_el = await row.query_selector(".yX.xY span[email], .zF, [data-hovercard-id]")
                subj_el = await row.query_selector(".bog, .bqe")

                if not sender_el:
                    continue

                sender_email = (
                    await sender_el.get_attribute("email")
                    or await sender_el.get_attribute("data-hovercard-id")
                    or ""
                ).lower().strip()

                subject_text = await subj_el.inner_text() if subj_el else ""

                if sender_email not in sender_set:
                    continue

                low_subj = subject_text.lower()
                low_sender = sender_email.lower()
                is_auto = any(p in low_subj or p in low_sender for p in [
                    "out of office", "auto-reply", "mailer-daemon", "no-reply",
                    "noreply", "bounce", "delivery failure",
                ])
                if is_auto:
                    continue

                replied.append(sender_email)

            # Next page
            older = page.locator('div[aria-label="Older"], div[act="19"]').first
            if await older.count() == 0:
                break
            disabled = await older.get_attribute("aria-disabled")
            if disabled == "true":
                break
            await older.click()
            await _delay(1.5, 2.5)

        return list(set(replied))
