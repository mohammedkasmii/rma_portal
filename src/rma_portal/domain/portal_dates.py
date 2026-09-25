"""Pure parsing of the French dates rendered by OmegaFlow."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_DATETIME_PATTERNS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M")
_DATE_PATTERN = "%d/%m/%Y"
_EMPTY_MARKERS = {"", "-", "—", "--"}


def parse_french_date(raw: str, timezone_id: str) -> tuple[datetime | None, str]:
    """Parse a French dd/mm/yyyy[ HH:MM[:SS]] date.

    Always returns the (whitespace-normalised) raw text alongside the
    parsed value, so a parsing failure never loses information.
    """
    clean = " ".join(raw.split())
    if clean in _EMPTY_MARKERS:
        return None, clean
    tzinfo = ZoneInfo(timezone_id)
    for pattern in _DATETIME_PATTERNS:
        try:
            return datetime.strptime(clean, pattern).replace(tzinfo=tzinfo), clean
        except ValueError:
            continue
    try:
        naive_date = datetime.strptime(clean, _DATE_PATTERN)
        return naive_date.replace(tzinfo=tzinfo), clean
    except ValueError:
        return None, clean
