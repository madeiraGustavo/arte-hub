"""
Gmail web automation via Playwright.

Responsibilities:
  • Login with 2FA support
  • Save / restore session cookies
  • Delete all inbox messages on first login
  • Compose and send emails
  • Check inbox for replies from a known set of addresses
  • Detect captcha / security blocks
  • Apply playwright-stealth fingerprint per account

Each account gets an isolated persistent browser profile directory so
cookies, local storage, and fingerprint data survive across runs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import string
import time
from pathlib import Path
from typing import Optional, Tuple

from playwright.async_api import (
    BrowserContext,
    ElementHandle,
    Page,
    Playwright,
    async_playwright,
)

from .config import Config
from .two_factor import resolve_2fa_code

log = logging.getLogger(__name__)

GMAIL_URL = "https://mail.google.com/"
GMAIL_ACCOUNTS_URL = "https://accounts.google.com/ServiceLogin?service=mail"

# ─────────────────────────── fingerprint helpers ─────────────────────────────

_SCREEN_RESOLUTIONS = [
    (1366, 768), (1440, 900), (1536, 864), (1600, 900),
    (1920, 1080), (2560, 1440),
]

_TIMEZONES = [
    "Europe/Amsterdam", "Europe/Berlin", "Europe/Paris",
    "Europe/London", "America/New_York", "America/Chicago",
    "America/Los_Angeles", "Europe/Brussels", "Europe/Zurich",
]

_LOCALES = ["nl-NL", "fr-FR", "de-DE", "en-GB", "en-US"]

_USER_AGENTS = [
    # Chrome 124 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 123 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome 124 / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Edge 124
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


def _deterministic_seed(email: str) -> int:
    """Return a stable integer seed for an email so fingerprint is consistent across runs."""
    return sum(ord(c) * (i + 1) for i, c in enumerate(email))


def _fingerprint_for_account(email: str) -> dict:
    seed = _deterministic_seed(email)
    rng = random.Random(seed)
    w, h = rng.choice(_SCREEN_RESOLUTIONS)
    return {
        "user_agent": rng.choice(_USER_AGENTS),
        "viewport": {"width": w, "height": h},
        "timezone_id": rng.choice(_TIMEZONES),
        "locale": rng.choice(_LOCALES),
        "color_scheme": "light",
        "device_scale_factor": rng.choice([1, 1.25, 1.5, 2]),
        "is_mobile": False,
        "has_touch": False,
    }


# ─────────────────────────── browser context factory ────────────────────────

async def create_browser_context(
    playwright: Playwright,
    email: str,
    config: Config,
    headless: Optional[bool] = None,
) -> BrowserContext:
    """
    Launch a Chromium browser with a persistent profile and stealth settings.
    Each email gets its own subdirectory so profiles never share state.
    """
    profile_dir = Path(config.paths.profiles_dir) / _safe_dir(email)
    profile_dir.mkdir(parents=True, exist_ok=True)

    fp = _fingerprint_for_account(email)
    is_headless = config.headless if headless is None else headless

    launch_opts: dict = {
        "headless": is_headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            f"--window-size={fp['viewport']['width']},{fp['viewport']['height']}",
        ],
    }

    if config.proxy.server:
        launch_opts["proxy"] = config.proxy.playwright_proxy

    context = await playwright.chromium.launch_persistent_context(
        str(profile_dir),
        **launch_opts,
        user_agent=fp["user_agent"],
        viewport=fp["viewport"],
        timezone_id=fp["timezone_id"],
        locale=fp["locale"],
        color_scheme=fp["color_scheme"],
        device_scale_factor=fp["device_scale_factor"],
        is_mobile=fp["is_mobile"],
        has_touch=fp["has_touch"],
        # Mask navigator.webdriver
        extra_http_headers={"Accept-Language": fp["locale"].replace("_", "-") + ",en;q=0.9"},
    )

    # Apply playwright-stealth if installed
    try:
        from playwright_stealth import stealth_async  # type: ignore
        for page in context.pages or []:
            await stealth_async(page)
    except ImportError:
        log.debug("playwright-stealth not installed; using manual overrides only")
        # Manual JS overrides as fallback
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['nl-NL','nl','en-US','en']});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1,2,3,4,5].map(i=>({name:`Plugin${i}`}))
            });
        """)

    return context


def _safe_dir(email: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", email)


# ─────────────────────────── login flow ──────────────────────────────────────

class GmailLoginError(Exception):
    pass


class GmailBlockedError(GmailLoginError):
    pass


class GmailCaptchaError(GmailLoginError):
    pass


async def login_gmail(
    context: BrowserContext,
    email: str,
    password: str,
    two_fa_key: Optional[str],
    config: Config,
) -> Page:
    """
    Attempt to log into Gmail. Returns the main Gmail page if successful.
    Raises GmailBlockedError / GmailCaptchaError on detection events.
    """
    page = await context.new_page()
    try:
        # Apply stealth per-page if available
        try:
            from playwright_stealth import stealth_async  # type: ignore
            await stealth_async(page)
        except ImportError:
            pass

        await page.goto(GMAIL_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1, 2))

        # Already logged in?
        if await _is_gmail_inbox(page):
            log.info("%s: already logged in (session restored)", email)
            return page

        # Navigate to login
        await page.goto(GMAIL_ACCOUNTS_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1.5, 2.5))

        # Check for captcha / unusual activity
        await _check_for_blocks(page, email)

        # ── Email field ───────────────────────────────────────────────────
        email_input = await _wait_for_selector(page, 'input[type="email"]', timeout=15000)
        await _human_type(page, email_input, email)
        await page.keyboard.press("Enter")
        await asyncio.sleep(random.uniform(1.5, 2.5))

        await _check_for_blocks(page, email)

        # ── Password field ────────────────────────────────────────────────
        pwd_input = await _wait_for_selector(page, 'input[type="password"]', timeout=15000)
        await _human_type(page, pwd_input, password)
        await page.keyboard.press("Enter")
        await asyncio.sleep(random.uniform(2, 3))

        await _check_for_blocks(page, email)

        # ── 2FA / verification ────────────────────────────────────────────
        if await _needs_2fa(page):
            code = await resolve_2fa_code(two_fa_key, page, config.missing_2fa_strategy)
            if not code:
                raise GmailLoginError(f"{email}: 2FA required but no code available")

            code_input = await _wait_for_selector(page, 'input[type="tel"], input[aria-label*="code" i]', timeout=10000)
            await _human_type(page, code_input, code)
            await page.keyboard.press("Enter")
            await asyncio.sleep(random.uniform(2, 3))

        await _check_for_blocks(page, email)

        # ── Wait for inbox ─────────────────────────────────────────────────
        for attempt in range(3):
            if await _is_gmail_inbox(page):
                log.info("%s: login successful", email)
                return page
            await asyncio.sleep(3)
            # Handle "Stay signed in?" prompts
            try:
                btn = page.locator('button:has-text("Yes"), button:has-text("Confirm"), button[jsname="LgbsSe"]')
                if await btn.count() > 0:
                    await btn.first.click()
                    await asyncio.sleep(2)
            except Exception:
                pass

        raise GmailLoginError(f"{email}: login flow did not reach inbox after multiple attempts")

    except (GmailBlockedError, GmailCaptchaError):
        raise
    except GmailLoginError:
        raise
    except Exception as exc:
        raise GmailLoginError(f"{email}: unexpected login error: {exc}") from exc


async def _is_gmail_inbox(page: Page) -> bool:
    try:
        url = page.url
        if "mail.google.com" in url and "inbox" not in url.lower():
            # Might be at /mail/u/0/#inbox or just /mail/u/0/
            if "/mail/u/" in url or "#inbox" in url or "mail.google.com/#" in url:
                return True
        # Check for compose button or inbox label
        btn = page.locator('[gh="cm"], [data-tooltip*="Compose" i]')
        return await btn.count() > 0
    except Exception:
        return False


async def _needs_2fa(page: Page) -> bool:
    try:
        text = await page.inner_text("body")
        indicators = [
            "2-step verification", "2-Step Verification",
            "verification code", "Verification code",
            "authenticator app", "Enter the code",
            "Enter the 6-digit code", "6-digit code",
        ]
        return any(i in text for i in indicators)
    except Exception:
        return False


async def _check_for_blocks(page: Page, email: str) -> None:
    """Raise appropriate exception if captcha or unusual-activity page detected."""
    try:
        url = page.url
        text = await page.inner_text("body")
    except Exception:
        return

    captcha_signals = ["recaptcha", "CAPTCHA", "I'm not a robot", "challenge", "verify you're human"]
    if any(s.lower() in text.lower() for s in captcha_signals) or "recaptcha" in url:
        raise GmailCaptchaError(f"{email}: captcha detected at {url}")

    block_signals = [
        "unusual activity", "Couldn't sign you in", "account has been disabled",
        "This account has been suspended", "account was recently hacked",
        "Something went wrong", "access was blocked",
    ]
    if any(s.lower() in text.lower() for s in block_signals):
        raise GmailBlockedError(f"{email}: account blocked / unusual activity at {url}")


# ─────────────────────────── inbox cleanup (first login) ─────────────────────

async def delete_all_inbox(page: Page, email: str) -> int:
    """
    Select all conversations in the inbox and move them to Trash.
    Returns the approximate count of deleted threads (or 0 if inbox empty).

    Strategy:
      1. Navigate to inbox
      2. Click 'Select all' checkbox
      3. Click 'Select all X conversations in inbox' banner if it appears
      4. Click 'Delete' (move to trash)
      5. Empty trash
    """
    log.info("%s: starting first-login inbox cleanup", email)

    # Ensure we're on inbox
    await page.goto("https://mail.google.com/mail/u/0/#inbox", timeout=30000)
    await asyncio.sleep(2)

    # Check if inbox is empty
    empty_indicators = [
        'img[alt*="empty" i]',
        'td:has-text("No new mail")',
        'div[class*="nH"]:has-text("No new mail")',
    ]
    for sel in empty_indicators:
        try:
            el = page.locator(sel)
            if await el.count() > 0:
                log.info("%s: inbox already empty", email)
                return 0
        except Exception:
            pass

    # Scroll to make sure something is loaded
    await page.mouse.wheel(0, 300)
    await asyncio.sleep(1)

    deleted_total = 0

    # Gmail only selects 50 messages at a time; loop until inbox is empty
    for iteration in range(100):  # safety cap at 5000 emails
        # Click the 'Select all' checkbox
        select_all = page.locator(
            'div[gh="tl"] span[role="checkbox"], '
            'div.T-Jo-JW span[role="checkbox"], '
            '[data-tooltip*="Select all" i] input[type="checkbox"]'
        )
        try:
            checkbox = await page.wait_for_selector(
                'span.aid[data-tooltip], div[data-tooltip="Select all"] span',
                timeout=5000,
            )
            await checkbox.click()
        except Exception:
            # Fallback: keyboard shortcut * + a (select all)
            await page.keyboard.press("*")
            await asyncio.sleep(0.5)
            await page.keyboard.press("a")

        await asyncio.sleep(1)

        # Click "Select all conversations in Inbox" banner if shown
        try:
            banner = page.locator(
                'span:has-text("Select all"), '
                'div:has-text("conversations in this search"), '
                'span.bqe'
            )
            for _ in range(3):
                if await banner.count() > 0:
                    await banner.first.click()
                    await asyncio.sleep(0.8)
                    break
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # Check if anything is selected
        try:
            selected_label = page.locator('[gh="tl"] .ya')
            if await selected_label.count() == 0:
                log.info("%s: nothing more selected — inbox cleared", email)
                break
        except Exception:
            pass

        # Click Delete (trash icon)
        deleted = await _click_delete_button(page)
        if not deleted:
            log.warning("%s: could not find delete button on iteration %d", email, iteration)
            break

        deleted_total += 50
        await asyncio.sleep(2)

        # Check if inbox is now empty
        try:
            empty = page.locator('div[data-tooltip="Inbox"] ~ div:has-text("0")')
            if await empty.count() > 0:
                break
        except Exception:
            pass

        # Reload inbox for next batch
        await page.reload(wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

    # Empty trash to free space
    await _empty_trash(page)

    log.info("%s: inbox cleanup done (~%d emails moved to trash)", email, deleted_total)
    return deleted_total


async def _click_delete_button(page: Page) -> bool:
    """Click the delete/trash button in Gmail toolbar. Returns True if clicked."""
    selectors = [
        'div[data-tooltip="Delete"] div[role="button"]',
        'div[act="10"] div[role="button"]',
        'div[role="button"][data-tooltip*="Delete" i]',
        'button[aria-label*="Delete" i]',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click()
                await asyncio.sleep(1.5)
                return True
        except Exception:
            continue

    # Keyboard shortcut: # (hash) deletes selected in Gmail
    try:
        await page.keyboard.press("#")
        await asyncio.sleep(1.5)
        return True
    except Exception:
        pass
    return False


async def _empty_trash(page: Page) -> None:
    try:
        await page.goto("https://mail.google.com/mail/u/0/#trash", timeout=20000)
        await asyncio.sleep(2)
        # Click 'Empty Trash now' link
        empty_link = page.locator('a:has-text("Empty Trash now"), span:has-text("Empty Trash now")')
        if await empty_link.count() > 0:
            await empty_link.first.click()
            await asyncio.sleep(1)
            # Confirm dialog
            try:
                ok_btn = page.locator('button:has-text("OK"), button[name="ok"]')
                if await ok_btn.count() > 0:
                    await ok_btn.first.click()
                    await asyncio.sleep(1)
            except Exception:
                pass
        log.debug("Trash emptied")
    except Exception as exc:
        log.warning("Could not empty trash: %s", exc)


# ─────────────────────────── compose & send ──────────────────────────────────

class SendError(Exception):
    pass


class BadAddressError(SendError):
    pass


class AccountSendBlockedError(SendError):
    pass


async def compose_and_send(
    page: Page,
    to_email: str,
    subject: str,
    body: str,
    sender_email: str,
) -> None:
    """
    Open Gmail compose window, fill in fields, and send.
    Raises SendError subclasses on failure.
    """
    # Open compose window
    await _open_compose(page)

    # Fill recipient
    to_field = await _wait_for_selector(page, 'div[name="to"] input, textarea[name="to"], input[aria-label="To"]', timeout=10000)
    await _human_type(page, to_field, to_email)
    await page.keyboard.press("Tab")
    await asyncio.sleep(0.5)

    # Dismiss autocomplete
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass

    # Subject
    subj_field = await _wait_for_selector(page, 'input[name="subjectbox"], input[aria-label="Subject"]', timeout=5000)
    await _human_type(page, subj_field, subject)
    await asyncio.sleep(0.3)

    # Body (click body area, then type)
    body_field = await _wait_for_selector(
        page,
        'div[aria-label="Message Body"], div[role="textbox"][aria-multiline="true"]',
        timeout=5000,
    )
    await body_field.click()
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Delete")
    # Type body with human-like pauses
    await _human_type_body(page, body_field, body)

    await asyncio.sleep(random.uniform(0.5, 1.2))

    # Click Send button
    send_btn = await _wait_for_selector(
        page,
        'div[data-tooltip*="Send" i][role="button"], '
        'button[data-tooltip*="Send" i], '
        'div[aria-label*="Send" i]',
        timeout=8000,
    )
    await send_btn.click()
    await asyncio.sleep(random.uniform(2, 3.5))

    # Check for error dialogs
    await _check_send_errors(page, to_email, sender_email)

    log.info("%s → %s: email sent (subject: %s)", sender_email, to_email, subject[:50])


async def _open_compose(page: Page) -> None:
    """Click the Compose button; handle cases where it's already open."""
    for attempt in range(3):
        try:
            compose_btn = page.locator(
                'div[gh="cm"] div[role="button"], '
                'div.z0 > div[role="button"]:has-text("Compose"), '
                '[data-tooltip*="Compose" i][role="button"]'
            )
            if await compose_btn.count() > 0:
                await compose_btn.first.click()
                await asyncio.sleep(random.uniform(0.8, 1.5))
                # Check if compose window opened
                compose_win = page.locator('div[role="dialog"], div.nH.Hd')
                if await compose_win.count() > 0:
                    return
        except Exception:
            pass

        await asyncio.sleep(1)

    # Fallback: use keyboard shortcut 'c'
    await page.keyboard.press("c")
    await asyncio.sleep(1.5)


async def _check_send_errors(page: Page, to_email: str, sender_email: str) -> None:
    """Check for post-send error messages."""
    try:
        body_text = await page.inner_text("body")
    except Exception:
        return

    bad_address_signals = [
        "The address", "doesn't exist", "couldn't be found",
        "invalid", "No such user", "User unknown",
    ]
    for sig in bad_address_signals:
        if sig.lower() in body_text.lower():
            raise BadAddressError(f"Bad address {to_email}: {sig}")

    blocked_signals = [
        "Message blocked", "couldn't send", "sending limit",
        "delivery failed", "quota exceeded", "suspended",
    ]
    for sig in blocked_signals:
        if sig.lower() in body_text.lower():
            raise AccountSendBlockedError(
                f"{sender_email}: account blocked while sending to {to_email}: {sig}"
            )


# ─────────────────────────── check replies ───────────────────────────────────

async def check_replies(
    page: Page,
    sender_email: str,
    known_recipients: set[str],
) -> list[str]:
    """
    Scan inbox for replies from addresses in known_recipients.
    Returns list of email addresses that have replied.

    Strategy: Navigate to inbox, read thread list, match From addresses.
    Gmail shows 'From: Name <addr>' or just the name; we parse sender addresses.
    """
    repliers: list[str] = []

    try:
        await page.goto("https://mail.google.com/mail/u/0/#inbox", timeout=30000)
        await asyncio.sleep(2)

        # Collect all visible sender email addresses from thread list
        sender_elements = page.locator('span[email], span.yX[email], td.yX span[email]')
        count = await sender_elements.count()

        for i in range(count):
            try:
                addr = await sender_elements.nth(i).get_attribute("email")
                if addr and addr.lower() in known_recipients:
                    repliers.append(addr.lower())
            except Exception:
                continue

        # Also parse thread subjects/snippets looking for re: patterns from known senders
        if not repliers:
            repliers = await _check_replies_via_search(page, sender_email, known_recipients)

    except Exception as exc:
        log.error("%s: error checking replies: %s", sender_email, exc)

    return list(set(repliers))


async def _check_replies_via_search(
    page: Page,
    sender_email: str,
    known_recipients: set[str],
) -> list[str]:
    """
    Use Gmail search to find replies: 'is:reply from:(<addr1> OR <addr2> …)'
    Gmail search handles up to ~50 addresses per query.
    """
    repliers: list[str] = []
    recipients_list = list(known_recipients)

    # Process in chunks of 40 addresses
    for chunk_start in range(0, len(recipients_list), 40):
        chunk = recipients_list[chunk_start:chunk_start + 40]
        from_query = " OR ".join(f"from:{addr}" for addr in chunk)
        search_query = f"is:inbox ({from_query})"

        try:
            encoded = search_query.replace(" ", "+").replace(":", "%3A").replace("(", "%28").replace(")", "%29")
            search_url = f"https://mail.google.com/mail/u/0/#search/{encoded}"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)

            # Extract sender emails from results
            sender_els = page.locator('span[email]')
            for i in range(await sender_els.count()):
                try:
                    addr = await sender_els.nth(i).get_attribute("email")
                    if addr and addr.lower() in known_recipients:
                        repliers.append(addr.lower())
                except Exception:
                    continue
        except Exception as exc:
            log.warning("%s: search check failed for chunk: %s", sender_email, exc)

    return list(set(repliers))


# ─────────────────────────── auto-reply detection ────────────────────────────

_AUTOREPLY_PATTERNS = [
    r"\baut[oi]mat(ic|isch|ique)\b",
    r"\bout[ -]of[ -]office\b",
    r"\bvakantie\b",
    r"\bhors du bureau\b",
    r"\babwesenheit(snotiz)?\b",
    r"\bno.reply\b",
    r"\bnoreply\b",
    r"\bdo.not.reply\b",
    r"\bmailer.daemon\b",
    r"\bdelivery.status\b",
    r"\bmailer.daemon\b",
    r"\bundeliverable\b",
    r"\bbounce\b",
]

_AUTOREPLY_RE = re.compile("|".join(_AUTOREPLY_PATTERNS), re.IGNORECASE)


def is_autoreply(sender: str, subject: str, body_snippet: str) -> bool:
    combined = f"{sender} {subject} {body_snippet}"
    return bool(_AUTOREPLY_RE.search(combined))


# ─────────────────────────── utility helpers ─────────────────────────────────

async def _wait_for_selector(page: Page, selector: str, timeout: int = 10000) -> ElementHandle:
    el = await page.wait_for_selector(selector, timeout=timeout)
    if el is None:
        raise RuntimeError(f"Selector not found: {selector}")
    return el


async def _human_type(page: Page, element: ElementHandle, text: str) -> None:
    """Type text character by character with random delays."""
    await element.click()
    await asyncio.sleep(random.uniform(0.1, 0.3))
    for char in text:
        await element.type(char, delay=random.uniform(40, 130))
    await asyncio.sleep(random.uniform(0.1, 0.4))


async def _human_type_body(page: Page, element: ElementHandle, text: str) -> None:
    """Type email body with slightly faster pace and occasional pauses."""
    await element.click()
    words = text.split()
    for i, word in enumerate(words):
        space = " " if i < len(words) - 1 else ""
        await element.type(word + space, delay=random.uniform(30, 90))
        if random.random() < 0.05:  # occasional 'thinking' pause
            await asyncio.sleep(random.uniform(0.3, 0.8))
