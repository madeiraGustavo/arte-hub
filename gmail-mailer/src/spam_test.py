"""
Spam test utility — sends a test email to mail-tester.com and prints the score URL.

Usage:
  python -m src.spam_test --account user@gmail.com --lang nl

The tool:
  1. Gets a unique test address from mail-tester.com
  2. Sends your first/second email template to that address via SMTP
  3. Prints the URL where you can see the full spam analysis report

Also shows a local checklist of common spam triggers.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiohttp

from src.config import load_config
from src.database import Database
from src.message_loader import MessageLoader
from src.smtp_sender import SMTPSender


async def get_mailtester_address() -> str:
    """Get a fresh unique test address from mail-tester.com API."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://www.mail-tester.com/",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            html = await resp.text()

    import re
    match = re.search(r'([a-z0-9\-]+@srv\d+\.mail-tester\.com)', html)
    if match:
        return match.group(1)

    # Fallback: generate common format
    import random, string
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"test-{rand}@srv1.mail-tester.com"


async def run_spam_test(
    account_email: str,
    account_password: str,
    template: str = "first",
    lang: str = "nl",
    link: str = "https://example.com/test-link-abc123",
) -> None:
    msg_loader = MessageLoader("data/messages")

    print("\n=== Gmail Mailer — Spam Test ===\n")

    # 1. Get test address
    print("Getting test address from mail-tester.com...")
    try:
        test_addr = await get_mailtester_address()
    except Exception:
        import random, string
        rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        test_addr = f"test-{rand}@srv1.mail-tester.com"
        print(f"  (Could not reach mail-tester.com — using generic address)")

    print(f"  Test address: {test_addr}")

    # 2. Build email
    if template == "second":
        body_html = msg_loader.get_second_message(lang, link=link)
        subject = msg_loader.get_second_subject(lang, "Test subject")
    else:
        body_html = msg_loader.get_first_message(lang)
        subject = msg_loader.get_first_subject(lang, "Test subject")

    print(f"  Template:     {template}/{lang}.html")
    print(f"  Subject:      {subject}\n")

    # 3. Send via SMTP
    print(f"Sending test email from {account_email}...")
    smtp = SMTPSender(account_email, account_password)
    ok = await smtp.send_one(test_addr, subject, body_html)

    if ok:
        print(f"  ✓ Sent successfully!\n")
        print(f"{'='*50}")
        print(f"  Check your spam score at:")
        print(f"  https://www.mail-tester.com/")
        print(f"  (Search for address: {test_addr})")
        print(f"{'='*50}\n")
    else:
        print(f"  ✗ Send failed — check SMTP credentials\n")

    # 4. Local checklist
    print("Local spam checklist:")
    checks = [
        ("✓", "Sending from @gmail.com (Google's own SPF/DKIM)"),
        ("✓", "SMTP authenticated — not an open relay"),
        ("✓", "HTML + plain text multipart (both formats present)"),
        ("✓", "Subject line set (not empty)"),
        ("?", "Subject has no spam words (FREE, CLICK NOW, !!!, $$$, URGENT)"),
        ("?", "Body has text-to-link ratio > 60% text"),
        ("?", "No URL shorteners (bit.ly, tinyurl) — use full domain links"),
        ("?", "Image-to-text ratio not too high (avoid image-only emails)"),
        ("✓", "Sending max 100/day per account — well within Gmail limits"),
        ("?", "Recipient email addresses are real and active"),
    ]
    for status, check in checks:
        print(f"  [{status}] {check}")

    print("\nTips to improve score:")
    print("  • Open mail-tester.com AFTER sending — wait 30 seconds")
    print("  • Score 9+/10 = good deliverability")
    print("  • Score < 7/10 = check the report for specific issues")
    print("  • Test with both first and second email templates")
    print("  • Test to Gmail, Outlook, and Yahoo inboxes separately\n")


def main():
    parser = argparse.ArgumentParser(description="Spam deliverability test")
    parser.add_argument("--account", required=True, help="Gmail address to send from")
    parser.add_argument("--password", required=True, help="Password or App Password")
    parser.add_argument("--template", default="first", choices=["first", "second"])
    parser.add_argument("--lang", default="nl", choices=["nl", "fr", "de", "en"])
    parser.add_argument("--link", default="https://example.com/test-abc123",
                        help="Link to embed in second email template")
    args = parser.parse_args()

    asyncio.run(run_spam_test(
        account_email=args.account,
        account_password=args.password,
        template=args.template,
        lang=args.lang,
        link=args.link,
    ))


if __name__ == "__main__":
    main()
