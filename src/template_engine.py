"""
Load and render email message templates.

Template files live in templates/:
  {lang}_first.txt   — first contact email (plain text)
  {lang}_second.txt  — follow-up email with unique link

Placeholders in templates (double-brace format):
  {{recipient_email}}   — replaced with recipient address
  {{unique_link}}       — replaced with generated link (second email only)
  {{subject}}           — original subject from Excel

Templates can contain both plain text and minimal HTML.
The engine returns (subject, body) tuples.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger(__name__)

_cache: dict[str, str] = {}


def _load_template(templates_dir: str, lang: str, stage: str) -> str:
    """Load and cache template text. stage: 'first' | 'second'"""
    key = f"{lang}_{stage}"
    if key in _cache:
        return _cache[key]

    path = Path(templates_dir) / f"{key}.txt"
    if not path.exists():
        # Fall back to English
        log.warning("Template %s not found, falling back to en_%s", path.name, stage)
        path = Path(templates_dir) / f"en_{stage}.txt"

    if not path.exists():
        raise FileNotFoundError(f"Template file missing: {path}")

    text = path.read_text(encoding="utf-8")
    _cache[key] = text
    log.debug("Template loaded: %s", path.name)
    return text


def render_first_email(
    templates_dir: str,
    lang: str,
    recipient_email: str,
    subject: str,
) -> Tuple[str, str]:
    """Returns (subject_line, body)."""
    template = _load_template(templates_dir, lang, "first")
    body = _replace(template, {
        "recipient_email": recipient_email,
        "subject": subject,
    })
    subject_line = _extract_subject(body) or subject
    body = _strip_subject_line(body)
    return subject_line, body


def render_second_email(
    templates_dir: str,
    lang: str,
    recipient_email: str,
    subject: str,
    unique_link: str,
) -> Tuple[str, str]:
    """Returns (subject_line, body)."""
    template = _load_template(templates_dir, lang, "second")
    body = _replace(template, {
        "recipient_email": recipient_email,
        "subject": subject,
        "unique_link": unique_link,
    })
    subject_line = _extract_subject(body) or f"Re: {subject}"
    body = _strip_subject_line(body)
    return subject_line, body


def _replace(template: str, variables: dict) -> str:
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", value or "")
    return template


def _extract_subject(body: str) -> Optional[str]:
    """If template starts with 'SUBJECT: …', extract it."""
    match = re.match(r"^SUBJECT:\s*(.+)$", body.strip(), re.IGNORECASE | re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def _strip_subject_line(body: str) -> str:
    return re.sub(r"^SUBJECT:.*\n?", "", body.strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
