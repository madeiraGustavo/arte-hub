"""
Configuration loader — reads config/config.yaml and exposes a typed Config object.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import yaml
from dataclasses import dataclass, field


ROOT = Path(__file__).parent.parent


@dataclass
class ProxyConfig:
    server: str
    username: str = ""
    password: str = ""


@dataclass
class TelegramConfig:
    bot_token: str
    admin_chat_id: int
    link_bot_token: str
    link_bot_chat_id: int


@dataclass
class LimitsConfig:
    daily_per_account: int = 100
    total_per_account: int = 300
    days_before_account_removal: int = 3
    send_delay_min: float = 2.0
    send_delay_max: float = 5.0
    concurrent_accounts: int = 2


@dataclass
class BrowserConfig:
    headless: bool = True
    slow_mo: int = 50
    viewport_width: int = 1366
    viewport_height: int = 768
    timezone: str = "Europe/Amsterdam"


@dataclass
class FingerprintConfig:
    randomize_viewport: bool = True
    randomize_locale: bool = True
    use_stealth: bool = True


@dataclass
class PathsConfig:
    accounts_file: str = "data/accounts.txt"
    recipients_excel: str = "data/recipients.xlsx"
    profiles_dir: str = "profiles"
    db_file: str = "data/state.db"
    logs_dir: str = "logs"


@dataclass
class AppConfig:
    proxy: ProxyConfig
    telegram: TelegramConfig
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    fingerprint: FingerprintConfig = field(default_factory=FingerprintConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    reply_check_hours: List[int] = field(default_factory=lambda: [1, 3, 8])
    send_hour: int = 9
    send_minute: int = 0

    def resolve(self, base: Path = ROOT) -> "AppConfig":
        """Make all paths absolute relative to project root."""
        p = self.paths
        p.accounts_file = str(base / p.accounts_file)
        p.recipients_excel = str(base / p.recipients_excel)
        p.profiles_dir = str(base / p.profiles_dir)
        p.db_file = str(base / p.db_file)
        p.logs_dir = str(base / p.logs_dir)
        return self


def load_config(path: Optional[str] = None) -> AppConfig:
    if path is None:
        path = os.environ.get("MAILER_CONFIG", str(ROOT / "config" / "config.yaml"))

    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    proxy_raw = raw.get("proxy", {})
    telegram_raw = raw.get("telegram", {})
    limits_raw = raw.get("limits", {})
    browser_raw = raw.get("browser", {})
    fp_raw = raw.get("fingerprint", {})
    paths_raw = raw.get("paths", {})

    cfg = AppConfig(
        proxy=ProxyConfig(**proxy_raw),
        telegram=TelegramConfig(**telegram_raw),
        limits=LimitsConfig(**limits_raw),
        browser=BrowserConfig(**browser_raw),
        fingerprint=FingerprintConfig(**fp_raw),
        paths=PathsConfig(**paths_raw),
        reply_check_hours=raw.get("reply_check_hours", [1, 3, 8]),
        send_hour=raw.get("send_hour", 9),
        send_minute=raw.get("send_minute", 0),
    )
    return cfg.resolve()
