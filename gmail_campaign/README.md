# Gmail Campaign Automation

A production-ready two-phase email campaign system that operates from 50–150 Gmail accounts via browser automation (Playwright), with full anti-detection, SQLite state management, and Telegram integration.

---

## Architecture

```
gmail_campaign/
├── main.py                  # CLI entry point
├── config.json.example      # Configuration template
├── requirements.txt
├── src/
│   ├── config.py            # Config dataclasses loaded from config.json
│   ├── models.py            # Pure dataclasses mirroring DB rows
│   ├── database.py          # All SQLite operations (no ORM)
│   ├── account_parser.py    # Parse account .txt files (both formats)
│   ├── two_factor.py        # TOTP: pyotp (offline) + 2fa.fb.tools (fallback)
│   ├── gmail_automation.py  # Playwright Gmail: login, send, check replies
│   ├── language_detector.py # Keyword-weighted language detection
│   ├── excel_reader.py      # openpyxl Excel reader
│   ├── template_engine.py   # Load and render email templates
│   ├── telegram_notifier.py # Notifications + unique link generation
│   ├── campaign_runner.py   # Orchestration: blast, reply-check, maintenance
│   └── scheduler.py         # APScheduler daemon
├── templates/
│   ├── en_first.txt / en_second.txt
│   ├── nl_first.txt / nl_second.txt
│   ├── fr_first.txt / fr_second.txt
│   └── de_first.txt / de_second.txt
├── data/
│   ├── accounts/            # Drop .txt account files here
│   └── recipients/          # Keep Excel files here for reference
├── profiles/                # Per-account Playwright persistent profiles
└── logs/
```

---

## Data Flow

```
Excel file
    │
    ▼
import-recipients → DB (recipients table, pending)
    │
    ▼
blast (08:00)
    │  for each eligible account:
    │    login Gmail → first-login cleanup? → send first email
    │    increment DB counters → mark recipient as first_sent
    │
    ▼
check-replies (09:00, 11:00, 15:00, 19:00)
    │  for each account with first_sent recipients:
    │    login Gmail → scan inbox for replies
    │    filter auto-replies / bounces
    │    genuine reply → generate_unique_link (Telegram bot)
    │                  → send second email
    │                  → mark recipient as second_sent
    │
    ▼
maintenance (07:00)
    │  reset daily counters
    │  purge expired exhausted accounts
    │  send daily stats to Telegram
```

---

## Account File Format

Place `.txt` files in `data/accounts/`. Each line is one account:

```
# With 2FA (TOTP secret key)
john.doe@gmail.com:MyP@ss123:recovery@email.com:JBSWY3DPEHPK3PXP

# Without 2FA
jane.smith@gmail.com:AnotherPass!:backup@email.com
```

**Fields:** `email:password:submail[:2fa_key]`

The `2fa_key` is the Base32 TOTP secret — the same string shown when you set up Google Authenticator (the raw key, not the QR code). This is decoded offline by `pyotp` without any external requests.

---

## Installation (Linux VPS)

```bash
# 1. Clone / upload project
cd /opt
git clone <repo> gmail_campaign
cd gmail_campaign

# 2. Python 3.10+ virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Chromium browser for Playwright
playwright install chromium
playwright install-deps chromium

# 5. Configure
cp config.json.example config.json
nano config.json   # fill in proxy, Telegram tokens, etc.

# 6. Initialise DB and directories
python main.py setup

# 7. Import accounts
python main.py import-accounts

# 8. Import today's recipients (run daily)
python main.py import-recipients data/recipients/today.xlsx
```

---

## Running

### Manual (one-shot commands)

```bash
# Send first emails now
python main.py blast

# Check for replies and send second emails
python main.py check-replies

# Run maintenance + send Telegram stats
python main.py maintenance

# Show current stats
python main.py stats
```

### Scheduled daemon (recommended)

```bash
# Run as a background systemd service
python main.py scheduler
```

Or as a systemd unit:

```ini
# /etc/systemd/system/gmail-campaign.service
[Unit]
Description=Gmail Campaign Scheduler
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/gmail_campaign
ExecStart=/opt/gmail_campaign/.venv/bin/python main.py scheduler
Restart=always
RestartSec=30
Environment=BLAST_HOUR=8
Environment=CHECK_HOURS=9,11,15,19
Environment=MAINTENANCE_HOUR=7

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable gmail-campaign
sudo systemctl start gmail-campaign
sudo journalctl -u gmail-campaign -f
```

### Cron alternative (if no APScheduler)

```cron
0  7  * * *  cd /opt/gmail_campaign && .venv/bin/python main.py maintenance
0  8  * * *  cd /opt/gmail_campaign && .venv/bin/python main.py blast
0  9  * * *  cd /opt/gmail_campaign && .venv/bin/python main.py check-replies
0 11  * * *  cd /opt/gmail_campaign && .venv/bin/python main.py check-replies
0 15  * * *  cd /opt/gmail_campaign && .venv/bin/python main.py check-replies
0 19  * * *  cd /opt/gmail_campaign && .venv/bin/python main.py check-replies
```

---

## Anti-Detection Strategy

Each Gmail account gets:

1. **Isolated Playwright persistent profile** — separate cookies, localStorage, IndexedDB in `profiles/<email>/`. Sessions survive across runs without re-logging in.

2. **Deterministic fingerprint** — derived from the email hash so it's consistent across restarts but unique per account:
   - Screen resolution (from real-world distribution)
   - User-Agent (Chrome 123/124, Windows/macOS)
   - Timezone (European cities)
   - Language/Locale

3. **playwright-stealth** — patches `navigator.webdriver`, canvas fingerprinting, `window.chrome`, plugins list, and 50+ other JS properties that fingerprinting scripts probe.

4. **Human-like input** — character-by-character typing with random delays (40–130ms per character), random pauses between fields, occasional "thinking" pauses in body text.

5. **Random inter-send delays** — 2–5 seconds between emails, configurable.

6. **Single residential/ISP proxy** — all accounts share one proxy to keep IP consistent (required for Gmail's trust model). Do not use datacenter proxies.

7. **Session reuse** — cookies persist in the profile directory; most mornings the browser is already logged in without re-authenticating.

---

## 2FA Handling

| Scenario | Behaviour |
|---|---|
| `2fa_key` present | `pyotp` generates TOTP code offline (no external request). Waits for a fresh 30-second window before entering. |
| `2fa_key` absent, `missing_2fa_strategy = "skip"` | Account is marked `blocked`, Telegram notification sent. |
| `2fa_key` absent, `missing_2fa_strategy = "wait"` | Prints prompt to `stdin` — operator enters code manually. Useful if running interactively. |
| `pyotp` fails (malformed key) | Falls back to browser automation of `2fa.fb.tools` — opens a new tab, pastes key, extracts code. |

---

## Database Schema

```sql
accounts (
  id, email, password, submail, two_fa_key,
  status,                    -- active | blocked | exhausted
  daily_sent, total_sent,
  last_sent_date,
  date_added, date_exhausted,
  first_login_completed,     -- 0 = cleanup needed, 1 = done
  cookies_path,              -- path to saved cookies JSON
  profile_dir,               -- Playwright persistent profile directory
  notes
)

recipients (
  id, email, subject, language,
  assigned_account,          -- which Gmail account sent the first email
  first_sent_at,
  replied_at,
  unique_link,               -- generated by Telegram bot
  second_sent_at,
  status,                    -- pending | first_sent | replied | second_sent | bad
  notes
)

logs (id, ts, level, account, recipient, message)
```

---

## Telegram Notifications

| Event | Message |
|---|---|
| Account blocked/captcha | 🚫 Account blocked: `email` — reason |
| Account exhausted | ⚠️ Account exhausted — needs replacement |
| Daily stats (19:00) | 📊 First sent / replies / second sent / account counts |

---

## Replacing Exhausted Accounts

1. Add new account lines to any `.txt` file in `data/accounts/`.
2. Run `python main.py import-accounts`.
3. The new accounts enter with `status=active`, `total_sent=0` and start receiving assignments immediately.

Exhausted accounts are auto-deleted from DB after `account_expiry_days` (default: 3 days).

---

## Customising Templates

Edit files in `templates/`. Supported placeholders:

| Placeholder | Description |
|---|---|
| `{{recipient_email}}` | Recipient's email address |
| `{{subject}}` | Subject line from the Excel file |
| `{{unique_link}}` | Generated unique link (second email only) |

The first line `SUBJECT: …` in each template becomes the email subject. The rest is the body.

---

## Key Design Decisions & Recommendations

### Why sequential sends per account, not fully parallel?
Gmail's abuse detection is session-level. Two concurrent Playwright contexts logged into the same account trigger immediate suspicious-activity alerts. Across different accounts, limited parallelism (2 concurrent) is safe.

### Why persistent profiles instead of fresh browser contexts?
Persistent profiles accumulate legitimate browsing history, a real cookie jar, and cached resources — all signals of a genuine human user. Fresh contexts look like bots.

### Why pyotp over 2fa.fb.tools?
`pyotp` works offline, is instantaneous, and doesn't generate network traffic to a third-party site. The `2fa.fb.tools` path is kept as a fallback for malformed or non-standard keys.

### Sending limit recommendations
- 100 emails/day per account is conservative and within Gmail's documented limits.
- Use 2–5 second delays minimum. Shorter delays correlate with spam detection.
- Rotate account ages: mix freshly-created accounts with older ones.

### What `missing_2fa_strategy` to use on VPS
Use `"skip"` (default) on unattended VPS runs. If you have accounts without 2FA keys that you want to use interactively, briefly set `"wait"` and run `blast` from an SSH session.
