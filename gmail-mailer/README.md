# Gmail Mass Mailer

Automated two-stage email campaign system using Playwright browser automation.

## Architecture

```
gmail-mailer/
├── main.py                   # CLI entry point + scheduler boot
├── requirements.txt
├── config/
│   └── config.yaml.example   # Template — copy to config.yaml
├── data/
│   ├── accounts.txt          # Account list (one per line)
│   ├── recipients.xlsx       # Daily Excel with recipients
│   └── messages/
│       ├── first/            # First email templates (nl/fr/de/en.html)
│       └── second/           # Second email templates (nl/fr/de/en.html)
├── deploy/
│   ├── gmail-mailer.service  # systemd unit
│   └── setup_vps.sh          # One-time VPS setup
├── logs/                     # Rotating log files
├── profiles/                 # Persistent Playwright browser profiles (per account)
└── src/
    ├── config.py             # YAML config loader → typed dataclasses
    ├── database.py           # Async SQLite (aiosqlite) — all state
    ├── account_manager.py    # Account file parser, round-robin dispatcher
    ├── gmail_automation.py   # Playwright automation: login, send, check replies
    ├── two_factor.py         # TOTP via pyotp + 2fa.fb.tools scraping fallback
    ├── language_detector.py  # Keyword + langdetect language detection
    ├── excel_reader.py       # openpyxl reader → (email, subject, lang) tuples
    ├── message_loader.py     # HTML template loader with {{LINK}} substitution
    ├── send_wave.py          # Daily send wave orchestrator
    ├── reply_checker.py      # Reply scanning + second email dispatch
    ├── telegram_notifier.py  # Admin alerts + link generation via Telegram bot
    └── scheduler.py          # APScheduler: daily wave + hourly reply checks
```

## How It Works

### Daily Flow

```
09:00  Scheduler fires _job_send_wave
         ├─ reset_daily_counters()
         ├─ load recipients from Excel → upsert to DB
         └─ for each active account (up to concurrent_accounts in parallel):
               ├─ start Playwright context (isolated profile + unique fingerprint)
               ├─ login() — restore session from cookies or full login + 2FA
               ├─ [first run only] clear_inbox() — select-all → delete
               └─ for each assigned recipient (≤100 per account per day):
                     ├─ compose & send first email
                     ├─ mark_first_sent() in DB
                     ├─ increment_sent() — update daily/total counters
                     └─ human delay (2–5 sec)

10:00  Scheduler fires _job_check_replies
         └─ for each active account:
               ├─ scan Inbox for replies from first_sent recipients
               ├─ filter auto-responses / bounces
               └─ for each real reply:
                     ├─ mark_replied()
                     ├─ generate_link() via Telegram bot
                     └─ send second email (same language) with embedded link

12:00  Another reply check
17:00  Another reply check

23:00  _job_daily_stats  → Telegram report
00:30  _job_cleanup      → purge exhausted accounts older than 3 days
```

### Account Lifecycle

```
accounts.txt
    │
    ▼
DB: status=active, total_sent=0, daily_sent=0
    │
    │  daily_sent reaches 100  →  daily limit hit, skip until next day
    │  total_sent reaches 300  →  status=exhausted, exhausted_date=today
    │                              Telegram alert: "account exhausted"
    │  3 days after exhausted  →  purged from DB
    │
    ├─ Login fails / Google challenge  →  status=blocked
    └─ Send error (bad address)        →  recipient marked bad, account continues
```

### Fingerprint Isolation

Each Gmail account gets:
- Its own **persistent browser profile** directory (`profiles/<email_sanitised>/`)
- Deterministic-random **viewport size** (seeded by email address)
- Unique **User-Agent** string (rotated from pool of 5 real Chrome UA strings)
- Unique **locale** (nl-NL, fr-FR, de-DE, en-GB, en-US, …)
- **playwright-stealth** patches: removes `navigator.webdriver`, fakes WebGL, canvas, AudioContext fingerprints, etc.
- All cookies/localStorage persist across runs → no repeated login challenges

## Setup

### 1. Install

```bash
git clone <repo> /opt/gmail-mailer
cd /opt/gmail-mailer
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

Or run the automated script (as root on a fresh VPS):

```bash
bash deploy/setup_vps.sh
```

### 2. Configure

```bash
cp config/config.yaml.example config/config.yaml
nano config/config.yaml
```

Fill in:
- `proxy.server` — your residential/ISP proxy URL
- `telegram.bot_token` — admin notification bot token
- `telegram.admin_chat_id` — your personal Telegram chat ID
- `telegram.link_bot_token` — the bot that generates unique links
- `telegram.link_bot_chat_id` — chat ID for link requests

### 3. Add Accounts

Create `data/accounts.txt` with one account per line:

```
# Format 1 (with 2FA key):
user@gmail.com:MyPassword123:backup@example.com:JBSWY3DPEHPK3PXP

# Format 2 (no 2FA):
user2@gmail.com:AnotherPass:backup2@example.com
```

**2FA key note:** The key you paste into `https://2fa.fb.tools/` to get a 6-digit code.
If it is a standard Base32 TOTP seed (uppercase letters + digits 2–7, 16–64 chars),
the system generates codes locally via `pyotp` without opening the website.
Otherwise it opens `2fa.fb.tools` in the browser and scrapes the code.

### 4. Add Recipients

Place your Excel file at `data/recipients.xlsx`:
- **Column A**: recipient email address
- **Column B**: subject line (used for language detection)

Language detection order:
1. Keyword match (fast, deterministic) — e.g. "Hallo" → NL, "Bonjour" → FR
2. `langdetect` library fallback (probabilistic)
3. Default: English

### 5. Customise Email Templates

Edit the HTML files in `data/messages/first/` and `data/messages/second/`.
The second-email templates must contain `{{LINK}}` where the unique URL goes.

### 6. Run

```bash
# Start scheduler (runs forever in foreground)
python main.py

# Or via systemd
systemctl start gmail-mailer
journalctl -u gmail-mailer -f

# Manual triggers
python main.py --send-now        # immediate send wave
python main.py --check-replies   # immediate reply check
python main.py --import-accounts # reload accounts from file
python main.py --stats           # print today's stats
```

## Database Schema

| Table        | Key Fields |
|--------------|-----------|
| `accounts`   | email, password, submail, two_fa_key, status, first_login_completed, daily_sent, total_sent, last_sent_date, exhausted_date, cookies_json, profile_dir |
| `recipients` | email, subject, language, status (pending→first_sent→replied→second_sent), assigned_account, unique_link, is_bad |
| `logs`       | ts, level, account, recipient, message |

## Telegram Notifications

| Event | Message |
|-------|---------|
| Scheduler started | 🟢 Gmail Mailer started |
| Account blocked | 🚫 Account BLOCKED: email |
| Account exhausted | ⚠️ Account EXHAUSTED: email |
| < 5 accounts left | ⚠️ Only N active accounts remaining |
| Daily stats at 23:00 | 📊 First sent / Replies / Second sent / Active accounts |
| Send wave error | ❌ Error in send wave |

## Security Considerations

- All browser contexts run through a **single residential/ISP proxy** — do not use datacenter proxies as Gmail detects them reliably.
- Sends are spaced 2–5 seconds apart per account. Do **not** remove these delays.
- Daily limit of 100/account is conservative for Gmail. Going higher risks triggering sending limits.
- Persistent profile directories mean Google sees a "known device" on subsequent logins, greatly reducing 2FA and re-verification prompts.
- Never reuse the same `profiles/` directory across two accounts.

## Replacing Exhausted Accounts

1. Append new lines to `data/accounts.txt`
2. Run `python main.py --import-accounts`
3. New accounts are ready immediately; the old exhausted ones will be auto-purged in 3 days.
