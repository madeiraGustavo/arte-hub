"""
Detect the language of an email campaign based on the subject line.

Priority order for disambiguation:
  1. Subject-line keyword matching (weighted scoring)
  2. Recipient domain TLD hints (optional)
  3. Default fallback → English

Supported languages: nl (Dutch), fr (French), de (German), en (English)
"""
from __future__ import annotations

import re
from typing import Optional


# Each keyword maps to (language, weight).
# Higher weight → stronger signal.
_KEYWORDS: list[tuple[str, str, int]] = [
    # ── Dutch ──────────────────────────────────────────────────────────────
    ("goedendag", "nl", 10),
    ("geachte", "nl", 10),
    ("beste", "nl", 8),
    ("dag,", "nl", 7),
    ("betreft", "nl", 8),
    ("aanbieding", "nl", 9),
    ("offerte", "nl", 9),
    ("diensten", "nl", 7),
    ("samenwerking", "nl", 8),
    ("uw", "nl", 5),
    ("heeft", "nl", 6),
    ("zijn", "nl", 4),
    ("voor", "nl", 3),
    ("van", "nl", 3),
    ("hallo", "nl", 5),   # shared with German — use lower weight

    # ── French ─────────────────────────────────────────────────────────────
    ("bonjour", "fr", 10),
    ("madame", "fr", 9),
    ("monsieur", "fr", 9),
    ("cher ", "fr", 8),
    ("chère", "fr", 8),
    ("objet:", "fr", 7),
    ("proposition", "fr", 8),
    ("offre", "fr", 7),
    ("collaboration", "fr", 7),
    ("votre", "fr", 6),
    ("notre", "fr", 5),
    ("salut", "fr", 7),
    ("bonne journée", "fr", 9),

    # ── German ─────────────────────────────────────────────────────────────
    ("guten tag", "de", 10),
    ("sehr geehrte", "de", 10),
    ("sehr geehrter", "de", 10),
    ("liebe ", "de", 7),
    ("betreff", "de", 8),
    ("angebot", "de", 9),
    ("zusammenarbeit", "de", 9),
    ("guten morgen", "de", 9),
    ("mit freundlichen", "de", 10),
    ("mfg", "de", 6),
    ("die ", "de", 4),
    ("der ", "de", 4),
    ("eine ", "de", 5),
    ("für", "de", 6),
    ("hallo", "de", 4),   # shared with Dutch — lower weight

    # ── English ────────────────────────────────────────────────────────────
    ("hello", "en", 8),
    ("hi ", "en", 6),
    ("dear ", "en", 8),
    ("subject:", "en", 6),
    ("offer", "en", 7),
    ("proposal", "en", 8),
    ("greetings", "en", 9),
    ("partnership", "en", 8),
    ("collaboration", "en", 7),
    ("opportunity", "en", 8),
    ("kindly", "en", 7),
    ("regards", "en", 9),
    ("sincerely", "en", 9),
    ("good morning", "en", 9),
    ("good afternoon", "en", 9),
]

# TLD → probable language
_TLD_HINTS: dict[str, str] = {
    ".nl": "nl",
    ".be": "nl",    # ambiguous (FR/NL), but NL is more common in our context
    ".fr": "fr",
    ".de": "de",
    ".at": "de",
    ".ch": "de",
    ".uk": "en",
    ".co.uk": "en",
    ".com": "en",
    ".io": "en",
    ".org": "en",
    ".net": "en",
    ".us": "en",
}


def detect_language(subject: str, recipient_email: Optional[str] = None) -> str:
    """
    Returns the best-matching language code: 'nl', 'fr', 'de', or 'en'.
    """
    subject_lc = subject.lower()

    scores: dict[str, int] = {"nl": 0, "fr": 0, "de": 0, "en": 0}

    for keyword, lang, weight in _KEYWORDS:
        if keyword in subject_lc:
            scores[lang] += weight

    # Tiebreaker: TLD hint
    if recipient_email:
        email_lc = recipient_email.lower()
        for tld, lang in _TLD_HINTS.items():
            if email_lc.endswith(tld):
                scores[lang] += 3
                break

    best_lang = max(scores, key=lambda k: scores[k])

    # If no keywords matched at all, default to English
    if scores[best_lang] == 0:
        return "en"

    return best_lang


def detect_languages_bulk(rows: list[dict]) -> list[dict]:
    """
    Add/overwrite the 'language' key in each row dict.
    Row must have 'subject' key; 'email' key is optional for TLD hint.
    """
    for row in rows:
        row["language"] = detect_language(
            row.get("subject", ""),
            row.get("email"),
        )
    return rows
