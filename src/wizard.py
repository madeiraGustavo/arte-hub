"""
Interactive setup wizard.

Run:  python main.py wizard

Walks the user through every configuration step:
  1. Proxy
  2. Telegram bot + chat ID
  3. Telegram link-generation bot
  4. Campaign limits
  5. Email templates (open editor or paste text)
  6. 2FA strategy

Saves config.json and all template files when done.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path


# ─────────────────────────── colour helpers ──────────────────────────────────

def _c(code: str, text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text

def bold(t):    return _c("1", t)
def green(t):   return _c("32", t)
def yellow(t):  return _c("33", t)
def cyan(t):    return _c("36", t)
def red(t):     return _c("31", t)
def dim(t):     return _c("2", t)


def header(title: str) -> None:
    print()
    print(bold(cyan("━" * 55)))
    print(bold(f"  {title}"))
    print(bold(cyan("━" * 55)))


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    hint = f"  {dim(f'[Enter = {default}]')}" if default else ""
    full_prompt = f"\n  {bold('→')} {prompt}{hint}\n  > "
    if secret:
        import getpass
        val = getpass.getpass(full_prompt)
    else:
        val = input(full_prompt).strip()
    return val if val else default


def ask_multiline(prompt: str, existing: str = "") -> str:
    """Ask user to paste multi-line text. Ends on a lone '.' on its own line."""
    print(f"\n  {bold('→')} {prompt}")
    if existing:
        print(dim("  Current text (press Enter to keep, or type new text):"))
        print(dim(textwrap.indent(existing[:200] + ("…" if len(existing) > 200 else ""), "    ")))
    print(dim("  Paste your text below. When done type a single dot  .  and press Enter."))
    print()
    lines = []
    while True:
        try:
            line = input("  ")
        except EOFError:
            break
        if line.strip() == ".":
            break
        lines.append(line)
    result = "\n".join(lines).strip()
    return result if result else existing


def yes_no(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    val = ask(f"{prompt} {hint}", "y" if default else "n").lower()
    return val in ("y", "yes", "")


# ─────────────────────────── wizard steps ────────────────────────────────────

def step_proxy(cfg: dict) -> dict:
    header("STEP 1 — Proxy Settings")
    print(dim("  A residential or ISP proxy is required for Gmail to trust the connection."))
    print(dim("  Format:  http://ip:port  or  http://user:pass@ip:port"))
    print(dim("  Leave empty if you want to configure later.\n"))

    proxy = cfg.get("proxy", {})
    server = ask("Proxy address (e.g. http://1.2.3.4:8888)", proxy.get("server", ""))
    username = ""
    password = ""
    if server and "@" not in server:
        if yes_no("Does this proxy require a username/password?", False):
            username = ask("Proxy username", proxy.get("username", ""))
            password = ask("Proxy password", proxy.get("password", ""), secret=True)

    cfg["proxy"] = {"server": server, "username": username, "password": password}
    print(green("  ✓ Proxy saved"))
    return cfg


def step_telegram_notify(cfg: dict) -> dict:
    header("STEP 2 — Telegram Notifications")
    print(dim("  You will receive alerts when accounts get blocked, exhausted,"))
    print(dim("  and a daily stats summary.\n"))
    print(dim("  How to get a bot token:"))
    print(dim("  1. Open Telegram → search @BotFather → /newbot"))
    print(dim("  2. Follow instructions → copy the token\n"))
    print(dim("  How to get your chat ID:"))
    print(dim("  1. Send any message to your bot"))
    print(dim("  2. Open https://api.telegram.org/bot<TOKEN>/getUpdates"))
    print(dim("  3. Copy the 'id' number from 'chat' object\n"))

    tg = cfg.get("telegram", {})
    token   = ask("Notification bot token", tg.get("bot_token", ""), secret=True)
    chat_id = ask("Your Telegram chat ID",  tg.get("chat_id", ""))

    cfg.setdefault("telegram", {})
    cfg["telegram"]["bot_token"] = token
    cfg["telegram"]["chat_id"]   = chat_id
    print(green("  ✓ Telegram notifications saved"))
    return cfg


def step_telegram_links(cfg: dict) -> dict:
    header("STEP 3 — Link Generation Bot (API)")
    print(dim("  This bot generates unique links for the second email."))
    print(dim("  You said you will provide the API documentation later — fill this in when ready.\n"))

    tg = cfg.get("telegram", {})
    link_token   = ask("Link-bot token (leave empty to fill later)", tg.get("link_bot_token", ""), secret=True)
    link_api_url = ask("Link-bot API URL (e.g. https://your-api.com/generate)", tg.get("link_bot_api_url", ""))

    cfg["telegram"]["link_bot_token"]   = link_token
    cfg["telegram"]["link_bot_api_url"] = link_api_url
    print(green("  ✓ Link bot saved"))
    return cfg


def step_limits(cfg: dict) -> dict:
    header("STEP 4 — Send Limits")
    print(dim("  These protect accounts from being flagged by Gmail.\n"))

    camp = cfg.get("campaign", {})
    daily  = ask("Emails per account per day",  str(camp.get("daily_limit", 100)))
    total  = ask("Total emails per account ever", str(camp.get("total_limit", 300)))
    expiry = ask("Days after exhaustion → auto-delete account", str(camp.get("account_expiry_days", 3)))
    delay_min = ask("Min delay between sends (seconds)", str(camp.get("min_send_delay", 2.0)))
    delay_max = ask("Max delay between sends (seconds)", str(camp.get("max_send_delay", 5.0)))
    conc  = ask("Max parallel browser sessions (recommend 1–2)", str(camp.get("max_concurrent_senders", 2)))

    cfg["campaign"] = {
        **camp,
        "daily_limit": int(daily),
        "total_limit": int(total),
        "account_expiry_days": int(expiry),
        "min_send_delay": float(delay_min),
        "max_send_delay": float(delay_max),
        "max_concurrent_senders": int(conc),
    }
    print(green("  ✓ Limits saved"))
    return cfg


def step_2fa_strategy(cfg: dict) -> dict:
    header("STEP 5 — 2FA Strategy")
    print(dim("  What to do when an account has no 2FA key stored?\n"))
    print("    1 — skip   (mark as blocked, send Telegram alert)  " + dim("← recommended for unattended VPS"))
    print("    2 — wait   (pause and ask you to type code manually via SSH)\n")

    choice = ask("Choose 1 or 2", "1")
    cfg["missing_2fa_strategy"] = "skip" if choice != "2" else "wait"
    cfg["headless"] = True
    print(green("  ✓ 2FA strategy saved"))
    return cfg


def step_templates(templates_dir: str) -> None:
    header("STEP 6 — Email Templates")
    print(dim("  You have 4 languages: EN (English), NL (Dutch), FR (French), DE (German)."))
    print(dim("  For each language: first email and second email (with unique link).\n"))
    print(dim("  Placeholders you can use in your texts:"))
    print(dim("    {{recipient_email}}  — email of the recipient"))
    print(dim("    {{subject}}          — subject from the Excel file"))
    print(dim("    {{unique_link}}      — generated link (second email only)\n"))
    print(dim("  The FIRST line of your text should be:  SUBJECT: your subject here\n"))

    langs = [("en", "English"), ("nl", "Dutch / Nederlands"), ("fr", "French / Français"), ("de", "German / Deutsch")]
    stages = [
        ("first",  "FIRST email (initial contact)"),
        ("second", "SECOND email (reply with unique link — must contain {{unique_link}})"),
    ]

    for lang_code, lang_name in langs:
        print(f"\n  {bold(f'── {lang_name} ({lang_code.upper()}) ──')}")
        for stage_code, stage_name in stages:
            path = Path(templates_dir) / f"{lang_code}_{stage_code}.txt"
            existing = path.read_text(encoding="utf-8") if path.exists() else ""

            edit = yes_no(f"  Edit {lang_name} {stage_name}?", False)
            if edit:
                print(f"\n  {bold('Template:')} {path}")
                new_text = ask_multiline(f"{lang_name} — {stage_name}", existing)
                if new_text:
                    path.write_text(new_text, encoding="utf-8")
                    print(green(f"  ✓ Saved {path.name}"))
                else:
                    print(dim("  (no change)"))
            else:
                print(dim(f"  Skipped {path.name}"))


def step_done(config_path: str) -> None:
    header("SETUP COMPLETE")
    print(green("  ✓ config.json saved"))
    print()
    print(bold("  Next steps:"))
    print()
    print("  1. Add account files to  " + bold("data/accounts/"))
    print("     Format per line:  email:password:submail:2fa_key")
    print("     or:               email:password:submail")
    print()
    print("  2. Import accounts:")
    print("     " + bold("python3 main.py import-accounts"))
    print()
    print("  3. Upload today's Excel recipients file, then:")
    print("     " + bold("python3 main.py import-recipients yourfile.xlsx"))
    print()
    print("  4. Start the web dashboard:")
    print("     " + bold("python3 main.py dashboard"))
    print(dim(f"     Then open  http://YOUR_SERVER_IP:8080  in your browser"))
    print()
    print("  5. Or run the auto-scheduler (sends at 08:00, checks at 09/11/15/19):")
    print("     " + bold("python3 main.py scheduler"))
    print()


# ─────────────────────────── main entry ──────────────────────────────────────

def run_wizard(config_path: str = "config.json", templates_dir: str = "templates") -> None:
    print()
    print(bold(cyan("╔══════════════════════════════════════════════════════╗")))
    print(bold(cyan("║      Gmail Campaign — Interactive Setup Wizard       ║")))
    print(bold(cyan("╚══════════════════════════════════════════════════════╝")))
    print()
    print(dim("  This wizard will create your config.json and optionally"))
    print(dim("  let you enter your email templates step by step."))
    print(dim("  You can re-run it at any time to update settings."))

    # Load existing config if present
    if Path(config_path).exists():
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        print(yellow("\n  Existing config.json found — values shown as defaults."))
    else:
        cfg = {}

    try:
        cfg = step_proxy(cfg)
        cfg = step_telegram_notify(cfg)
        cfg = step_telegram_links(cfg)
        cfg = step_limits(cfg)
        cfg = step_2fa_strategy(cfg)

        # Ensure paths are set
        cfg.setdefault("paths", {
            "db_path": "data/campaign.db",
            "profiles_dir": "profiles",
            "logs_dir": "logs",
            "templates_dir": "templates",
            "accounts_dir": "data/accounts",
            "recipients_dir": "data/recipients",
        })

        # Save config before template step (in case user Ctrl+C after)
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)

        Path(templates_dir).mkdir(parents=True, exist_ok=True)
        step_templates(templates_dir)

        # Re-save (paths might have been updated)
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)

        step_done(config_path)

    except KeyboardInterrupt:
        print(yellow("\n\n  Wizard interrupted. Partial config may have been saved."))
        sys.exit(0)
