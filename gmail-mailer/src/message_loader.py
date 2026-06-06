"""
Message loader — reads HTML templates from data/messages/{first,second}/<lang>.html
and performs variable substitution.

Supported link-insertion syntax (from BAS script):
  {{LINK}}              — replaced with the raw URL
  ||link||              — replaced with the raw URL
  ||link||>>||Text||    — replaced with <a href="URL">Text</a>
  ||link||>gen_id>||Text||  — replaced with <a href="URL">Text/ID</a>
                              where ID is the last path segment of the URL
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

SUPPORTED_LANGS = ("nl", "fr", "de", "en")


def process_link_templates(text: str, link: str) -> str:
    """
    Apply all link-substitution patterns to `text`.
    Works on both plain text and HTML bodies.
    """
    if not link:
        # Remove template markers if no link available
        text = re.sub(r"\|\|link\|\|>gen_id>\|\|.*?\|\|", "", text)
        text = re.sub(r"\|\|link\|\|>>\|\|.*?\|\|", "", text)
        text = text.replace("||link||", "").replace("{{LINK}}", "")
        return text

    # Extract ID from URL (last path segment, e.g. "abc123" from "https://x.com/pay/abc123")
    id_match = re.search(r"/([^/?#]+)/?$", link)
    link_id = id_match.group(1) if id_match else ""

    # Pattern 1: ||link||>gen_id>||Display Text||  → <a href="URL">Display Text/ID</a>
    def replace_gen_id(m):
        display = m.group(1)
        return f'<a href="{link}">{display}/{link_id}</a>'

    text = re.sub(r"\|\|link\|\|>gen_id>\|\|(.*?)\|\|", replace_gen_id, text)

    # Pattern 2: ||link||>>||Display Text||  → <a href="URL">Display Text</a>
    def replace_with_text(m):
        display = m.group(1)
        return f'<a href="{link}">{display}</a>'

    text = re.sub(r"\|\|link\|\|>>\|\|(.*?)\|\|", replace_with_text, text)

    # Pattern 3: ||link|| or {{LINK}}  → raw URL
    text = text.replace("||link||", link).replace("{{LINK}}", link)

    return text


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
            logger.warning("Template not found: %s — falling back to en", path)
            path = self.base / category / "en.html"
        if not path.exists():
            logger.error("No fallback template for %s/%s", category, lang)
            return "<p>No message template found.</p>"

        content = path.read_text(encoding="utf-8")
        self._cache[key] = content
        return content

    def get_first_message(self, lang: str, product: str = "", **kwargs) -> str:
        """
        Render the first-email template.
        Substitutes {{PRODUCT}} with the listing title from Excel column B.
        """
        tmpl = self._load("first", lang)
        result = tmpl.replace("{{PRODUCT}}", product)
        if kwargs:
            try:
                result = result.format_map(kwargs)
            except (KeyError, ValueError):
                pass
        return result

    def get_second_message(self, lang: str, link: str = "", product: str = "", **kwargs) -> str:
        """
        Render the second-email template.
        Substitutes {{LINK}} / ||link|| patterns and {{PRODUCT}}.
        """
        tmpl = self._load("second", lang)
        result = process_link_templates(tmpl, link)
        result = result.replace("{{PRODUCT}}", product)
        if kwargs:
            try:
                result = result.format_map(kwargs)
            except (KeyError, ValueError):
                pass
        return result

    def get_first_subject(self, lang: str, original_subject: str = "") -> str:
        return original_subject or self._default_subject("first", lang)

    def get_second_subject(self, lang: str, original_subject: str = "") -> str:
        base = original_subject or self._default_subject("first", lang)
        if not base.startswith("Re:") and not base.startswith("RE:"):
            return f"Re: {base}"
        return base

    def _default_subject(self, category: str, lang: str) -> str:
        defaults = {
            "nl": {"first": "Samenwerking voorstel", "second": "Meer informatie"},
            "fr": {"first": "Proposition de partenariat", "second": "Plus d'informations"},
            "de": {"first": "Kooperationsangebot", "second": "Weitere Informationen"},
            "en": {"first": "Partnership proposal", "second": "More information"},
        }
        return defaults.get(lang, defaults["en"]).get(category, "Hello")
