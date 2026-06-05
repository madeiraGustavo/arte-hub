"""
Central configuration — loaded from config.json at startup.
All module-level constants live here so changing the file is enough.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ProxyConfig:
    server: str = ""          # e.g. "http://1.2.3.4:8888"
    username: str = ""
    password: str = ""

    @property
    def playwright_proxy(self) -> Optional[dict]:
        if not self.server:
            return None
        cfg: dict = {"server": self.server}
        if self.username:
            cfg["username"] = self.username
            cfg["password"] = self.password
        return cfg


@dataclass
class TelegramConfig:
    bot_token: str = ""           # for notifications
    chat_id: str = ""
    link_bot_token: str = ""      # bot that generates unique links
    link_bot_api_url: str = ""    # endpoint, e.g. https://api.example.com/generate


@dataclass
class CampaignConfig:
    daily_limit: int = 100          # emails per account per day
    total_limit: int = 300          # after this → exhausted
    account_expiry_days: int = 3    # days after exhaustion → deleted
    min_send_delay: float = 2.0     # seconds between sends
    max_send_delay: float = 5.0
    reply_check_intervals: List[int] = field(
        default_factory=lambda: [60, 180, 360]   # minutes after morning blast
    )
    max_concurrent_senders: int = 2   # parallel browser contexts
    send_timeout_seconds: int = 30


@dataclass
class PathsConfig:
    db_path: str = "data/campaign.db"
    profiles_dir: str = "profiles"
    logs_dir: str = "logs"
    templates_dir: str = "templates"
    accounts_dir: str = "data/accounts"
    recipients_dir: str = "data/recipients"


@dataclass
class Config:
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    campaign: CampaignConfig = field(default_factory=CampaignConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)

    # If True, browser runs visible (useful for manual 2FA)
    headless: bool = True
    # If no 2fa_key: "skip" | "wait" (wait for manual input via stdin)
    missing_2fa_strategy: str = "skip"

    @classmethod
    def load(cls, path: str = "config.json") -> "Config":
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)

        proxy = ProxyConfig(**raw.get("proxy", {}))
        telegram = TelegramConfig(**raw.get("telegram", {}))
        campaign = CampaignConfig(**raw.get("campaign", {}))
        paths = PathsConfig(**raw.get("paths", {}))

        return cls(
            proxy=proxy,
            telegram=telegram,
            campaign=campaign,
            paths=paths,
            headless=raw.get("headless", True),
            missing_2fa_strategy=raw.get("missing_2fa_strategy", "skip"),
        )

    def save(self, path: str = "config.json") -> None:
        data = {
            "proxy": self.proxy.__dict__,
            "telegram": self.telegram.__dict__,
            "campaign": self.campaign.__dict__,
            "paths": self.paths.__dict__,
            "headless": self.headless,
            "missing_2fa_strategy": self.missing_2fa_strategy,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
