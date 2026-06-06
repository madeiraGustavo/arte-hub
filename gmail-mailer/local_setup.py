"""
LOCAL SETUP — запускается на твоём компьютере (Windows/Mac/Linux).

Логинится в каждый аккаунт через видимый браузер,
сохраняет cookies в папку sessions/.

Потом просто копируешь папку sessions/ на VPS и всё — никакого
SMTP, никакого повторного логина, отправка через Compose как живой человек.

Запуск:
  python local_setup.py                          # все аккаунты
  python local_setup.py --only user@gmail.com    # один аккаунт
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SESSIONS_DIR = Path("sessions")
ACCOUNTS_FILE = Path("data/accounts.txt")


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    sep = "|" if "|" in line else ":"
    p = line.split(sep, 3)
    if len(p) < 2:
        return None
    return {
        "email":       p[0].strip().lower(),
        "password":    p[1].strip(),
        "submail":     p[2].strip() if len(p) > 2 else "",
        "two_fa_key":  p[3].strip() if len(p) > 3 else "",
    }


async def login_and_save_cookies(account: dict) -> bool:
    """
    Open Chrome visibly, log into the account, save cookies.
    The user can manually solve any captcha/2FA in the browser window.
    """
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    email = account["email"]
    password = account["password"]
    two_fa = account.get("two_fa_key", "")

    print(f"\n{'='*55}")
    print(f"  Account: {email}")
    print(f"{'='*55}")

    # Session file path
    session_file = SESSIONS_DIR / f"{re.sub(r'[^a-z0-9._-]', '_', email)}.json"

    # If session already exists and is recent — skip
    if session_file.exists():
        print(f"  ✓ Session already saved — skipping (delete {session_file.name} to redo)")
        return True

    async with async_playwright() as pw:
        profile_dir = SESSIONS_DIR / f"profile_{re.sub(r'[^a-z0-9._-]', '_', email)}"
        profile_dir.mkdir(parents=True, exist_ok=True)

        ctx = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            slow_mo=60,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
            no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        try:
            # Check if already logged in
            print(f"  Opening Gmail...")
            await page.goto("https://mail.google.com/mail/u/0/", timeout=30_000)
            await asyncio.sleep(4)

            if "mail.google.com" in page.url and "accounts.google" not in page.url:
                print(f"  ✓ Already in Gmail inbox!")
            else:
                # Go to login page
                await page.goto(
                    "https://accounts.google.com/signin/v2/identifier",
                    timeout=30_000,
                )
                await asyncio.sleep(2)

                # Type email
                email_input = page.locator('input[type="email"]').first
                await email_input.wait_for(state="visible", timeout=15_000)
                await email_input.fill(email)
                await page.keyboard.press("Enter")
                await asyncio.sleep(3)

                # Type password
                try:
                    pwd_input = page.locator('input[type="password"]').first
                    await pwd_input.wait_for(state="visible", timeout=10_000)
                    await pwd_input.fill(password)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(4)
                except PWTimeout:
                    print(f"\n  ⚠️  Password field not found automatically.")
                    print(f"  Complete the login manually in the browser window.")
                    input("  Press ENTER here when you see the Gmail inbox: ")

                # Handle 2FA if needed
                body = (await page.inner_text("body")).lower()
                if any(k in body for k in ["verification code", "2-step", "authenticator", "enter the code"]):
                    if two_fa:
                        import pyotp
                        clean = two_fa.strip().upper().replace(" ", "")
                        if re.match(r"^[A-Z2-7]{16,64}$", clean):
                            code = pyotp.TOTP(clean).now()
                            code_input = page.locator('input[type="tel"], input[name*="code"]').first
                            await code_input.fill(code)
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(3)
                            print(f"  ✓ 2FA code entered automatically")
                        else:
                            print(f"\n  ⚠️  2FA required — enter code in browser")
                            input("  Press ENTER after entering 2FA: ")
                    else:
                        print(f"\n  ⚠️  2FA required — enter code in browser")
                        input("  Press ENTER after entering 2FA: ")

                # Handle captcha / security check
                body = (await page.inner_text("body")).lower()
                if any(k in body for k in ["pas un robot", "not a robot", "confirm it's you",
                                            "confirmez", "verify it's you"]):
                    print(f"\n  ⚠️  Google security check!")
                    print(f"  Solve it manually in the browser window.")
                    print(f"  After solving, you'll see Gmail inbox.")
                    input("  Press ENTER when you're in Gmail inbox: ")

                # Navigate to Gmail
                if "mail.google.com" not in page.url:
                    await page.goto("https://mail.google.com/mail/u/0/", timeout=30_000)
                    await asyncio.sleep(4)

                if "mail.google.com" not in page.url:
                    print(f"  ✗ Failed to reach Gmail")
                    return False

            # Verify we're in inbox
            compose_btn = page.locator('div[gh="cm"], .T-I.T-I-KE').first
            try:
                await compose_btn.wait_for(state="visible", timeout=10_000)
                print(f"  ✓ In Gmail inbox, Compose button found")
            except PWTimeout:
                print(f"  ⚠️  Gmail loaded but Compose not found — might need verification")
                input("  Press ENTER when you see the inbox with Compose button: ")

            # Save cookies
            cookies = await ctx.cookies()
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            session_file.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
            print(f"  ✓ Session saved → {session_file}")
            return True

        except Exception as exc:
            print(f"  ✗ Error: {exc}")
            return False
        finally:
            await ctx.close()


async def main():
    parser = argparse.ArgumentParser(description="Local Gmail session setup")
    parser.add_argument("--accounts", default=str(ACCOUNTS_FILE))
    parser.add_argument("--only",     help="Process only this email")
    args = parser.parse_args()

    accounts_path = Path(args.accounts)
    if not accounts_path.exists():
        print(f"Accounts file not found: {accounts_path}")
        print("Create data/accounts.txt with format: email|password or email|password|submail")
        sys.exit(1)

    accounts = []
    with accounts_path.open(encoding="utf-8") as f:
        for line in f:
            a = parse_line(line)
            if a:
                accounts.append(a)

    if args.only:
        accounts = [a for a in accounts if a["email"] == args.only.lower()]

    print(f"\n{'='*55}")
    print(f"  Gmail Session Setup — {len(accounts)} account(s)")
    print(f"  Sessions will be saved to: {SESSIONS_DIR}/")
    print(f"{'='*55}\n")
    print(f"Instructions:")
    print(f"  • Browser will open for each account")
    print(f"  • If Google shows a captcha — solve it manually")
    print(f"  • If 2FA appears — enter the code")
    print(f"  • Press ENTER in this window when you're in Gmail inbox\n")

    ok = 0
    for acc in accounts:
        result = await login_and_save_cookies(acc)
        if result:
            ok += 1

    print(f"\n{'='*55}")
    print(f"  Done: {ok}/{len(accounts)} sessions saved")
    print(f"\n  Next — copy sessions/ folder to VPS:")
    print(f"  scp -r sessions/ root@VPS_IP:/root/arte-hub/gmail-mailer/")
    print(f"\n  Or upload via Telegram bot (send sessions.zip to your bot)")
    print(f"{'='*55}")

    # Create zip for easy upload
    if ok > 0:
        import zipfile
        zip_path = Path("sessions.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in SESSIONS_DIR.glob("*.json"):
                zf.write(f, f"sessions/{f.name}")
        print(f"\n  sessions.zip created — upload this to VPS")


if __name__ == "__main__":
    asyncio.run(main())
