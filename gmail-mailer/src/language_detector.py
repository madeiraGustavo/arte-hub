"""
Language detector — determines the email language from the subject line.

Priority:
  1. Keyword dictionary match (fast, deterministic)
  2. langdetect fallback on the full subject (probabilistic)
Default: 'en'
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)

# Each language has a list of subject keywords / phrases (case-insensitive).
# Longer / more specific phrases should come first so they match before short ones.
LANG_KEYWORDS: Dict[str, List[str]] = {
    # German gets higher-priority unambiguous keywords checked before Dutch
    "de": [
        "guten morgen", "guten tag", "guten abend", "sehr geehrte",
        "sehr geehrter", "mit freundlichen grüßen", "freundliche grüße",
        "zusammenarbeit", "kooperation", "angebot", "anfrage",
        "vielen dank", "mein ", " meine ", " unser ", " unsere ",
        "liebe ", "lieber ", "ich möchte", "wir möchten",
    ],
    "nl": [
        "goedemorgen", "goedemiddag", "goedenavond",
        "met vriendelijke groet", "vriendelijke groet",
        "beste ", "geachte ", "hallo ", "dankjewel", "bedankt",
        "samenwerking", "aanbieding", "dag ",
    ],
    "fr": [
        "bonjour", "bonsoir", "salut", "madame", "monsieur", "cher ", "chère ",
        "cordialement", "merci", "proposition", "collaboration", "offre",
        "renseignement", "à bientôt", "je souhaite", "nous souhaitons",
    ],
    "en": [
        "hello", "hi ", "dear ", "good morning", "good afternoon",
        "good evening", "kind regards", "best regards", "sincerely",
        "partnership", "inquiry", "proposal", "collaboration", "thank you",
    ],
}

# Order matters: check DE before NL (both use "hallo"), FR before EN
_LANG_ORDER = ["de", "nl", "fr", "en"]


def detect_language(subject: str) -> str:
    """
    Return ISO-639-1 code: 'nl', 'fr', 'de', or 'en'.
    """
    if not subject:
        return "en"

    lower = subject.lower()

    for lang in _LANG_ORDER:
        for kw in LANG_KEYWORDS[lang]:
            if kw in lower:
                logger.debug("Language '%s' detected via keyword '%s'", lang, kw)
                return lang

    # Fallback: langdetect
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0
        detected = detect(subject)
        # Map to supported codes
        mapping = {"nl": "nl", "fr": "fr", "de": "de", "en": "en",
                   "af": "nl",   # Afrikaans → Dutch fallback
                   "lb": "de"}   # Luxembourgish → German fallback
        lang = mapping.get(detected, "en")
        logger.debug("Language '%s' detected via langdetect (raw=%s)", lang, detected)
        return lang
    except Exception as exc:
        logger.debug("langdetect failed (%s), defaulting to 'en'", exc)
        return "en"
