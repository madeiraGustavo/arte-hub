"""
Read recipient list from an Excel file.

Expected columns (1-indexed):
  Column 1 — recipient email address
  Column 2 — email subject line  (used for language detection)

Any extra columns are ignored.
The first row is treated as a header if it looks like one (no '@' in col 1).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, TypedDict

log = logging.getLogger(__name__)


class RecipientRow(TypedDict):
    email: str
    subject: str


def read_recipients(path: str | Path) -> List[RecipientRow]:
    try:
        import openpyxl  # type: ignore
    except ImportError:
        raise RuntimeError("openpyxl is required: pip install openpyxl")

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active

    rows: List[RecipientRow] = []
    skipped = 0
    first_row = True

    for row in ws.iter_rows(values_only=True):
        # Skip completely empty rows
        if not row or not row[0]:
            continue

        email_val = str(row[0]).strip()

        # Skip header row if present
        if first_row:
            first_row = False
            if "@" not in email_val:
                log.debug("Skipping header row: %s", email_val)
                continue

        subject_val = str(row[1]).strip() if len(row) > 1 and row[1] else ""

        if "@" not in email_val or "." not in email_val:
            log.debug("Skipping invalid email: %s", email_val)
            skipped += 1
            continue

        rows.append(RecipientRow(
            email=email_val.lower(),
            subject=subject_val,
        ))

    wb.close()
    log.info("Read %d recipients from %s (%d skipped)", len(rows), path.name, skipped)
    return rows
