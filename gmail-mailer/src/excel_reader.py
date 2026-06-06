"""
Excel reader — loads recipients from the daily Excel file.

Expected columns (no header row required, but header IS supported):
  Column A: recipient email address
  Column B: email subject (used for language detection)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple

import openpyxl

from .language_detector import detect_language

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load_recipients(xlsx_path: str) -> List[Tuple[str, str, str]]:
    """
    Returns a list of (email, subject, language) tuples.
    Rows with invalid emails are skipped.
    """
    path = Path(xlsx_path)
    if not path.exists():
        logger.error("Recipients file not found: %s", path)
        return []

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active

    results: List[Tuple[str, str, str]] = []
    skipped = 0

    for row_idx, row in enumerate(ws.iter_rows(min_row=1, values_only=True), start=1):
        if not row:
            continue

        # Support files with or without a header row
        email_raw = str(row[0]).strip() if row[0] else ""
        subject_raw = str(row[1]).strip() if len(row) > 1 and row[1] else ""

        # Skip header-like rows
        if email_raw.lower() in ("email", "e-mail", "address", "recipient"):
            continue

        if not _EMAIL_RE.match(email_raw):
            logger.debug("Row %d: invalid email '%s' — skipping", row_idx, email_raw)
            skipped += 1
            continue

        lang = detect_language(subject_raw)
        results.append((email_raw.lower(), subject_raw, lang))

    wb.close()
    logger.info(
        "Loaded %d recipients from %s (%d skipped)", len(results), path, skipped
    )
    return results
