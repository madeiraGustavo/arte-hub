"""
Message loader — reads HTML templates from data/messages/{first,second}/<lang>.html
and performs variable substitution.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

SUPPORTED_LANGS = ("nl", "fr", "de", "en")


class MessageLoader:
    def __init__(self, messages_dir: str = "data/messages"):
        self.base = Path(messages_dir)
        self._cache: Dict[str, str] = {}

    def _load(self, category: str, lang: str) -> str:
        key = f"{category}_{lang}"
        if key in self._cache:
            return self._cache[key]

        path = self.base / category / f"{lang}.html"
        if not path.exists():
            # fallback to English
            logger.warning("Template not found: %s — using en", path)
            path = self.base / category / "en.html"
        if not path.exists():
            logger.error("No fallback template found for %s/%s", category, lang)
            return "<p>No message template found.</p>"

        content = path.read_text(encoding="utf-8")
        self._cache[key] = content
        return content

    def get_first_message(self, lang: str, **kwargs) -> str:
        tmpl = self._load("first", lang)
        return tmpl.format_map(kwargs) if kwargs else tmpl

    def get_second_message(self, lang: str, link: str = "", **kwargs) -> str:
        tmpl = self._load("second", lang)
        return tmpl.replace("{{LINK}}", link).format_map(kwargs) if kwargs else tmpl.replace("{{LINK}}", link)

    def get_first_subject(self, lang: str, original_subject: str = "") -> str:
        """Use the Excel subject as-is (it's already personalised)."""
        return original_subject or self._default_subject("first", lang)

    def get_second_subject(self, lang: str, original_subject: str = "") -> str:
        prefix = {"nl": "Re", "fr": "Re", "de": "Re", "en": "Re"}.get(lang, "Re")
        base = original_subject or self._default_subject("first", lang)
        if not base.startswith("Re:"):
            return f"{prefix}: {base}"
        return base

    def _default_subject(self, category: str, lang: str) -> str:
        defaults = {
            "nl": {"first": "Samenwerking voorstel", "second": "Meer informatie"},
            "fr": {"first": "Proposition de partenariat", "second": "Plus d'informations"},
            "de": {"first": "Kooperationsangebot", "second": "Weitere Informationen"},
            "en": {"first": "Partnership proposal", "second": "More information"},
        }
        return defaults.get(lang, defaults["en"]).get(category, "Hello")
