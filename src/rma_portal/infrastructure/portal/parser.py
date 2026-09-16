"""Pure HTML parsing for the captured OmegaFlow contract.

No Playwright import here: every function takes already-rendered HTML, so
these are exercised directly against synthetic fixtures in
``tests/parser`` without a browser. See docs/omegaflow-contract.md for the
selectors this module implements.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from rma_portal.application.dto import QueueRow, QueueSnapshot
from rma_portal.domain.models import DossierDates

# The details link substring includes the trailing slash on purpose: some
# rows also carry a decoy "view-dossier-details7/<id>" observation-count
# icon link, which this substring does not match.
_DETAILS_HREF_MARKER = "view-dossier-details/"

_LIST_FIELD_CLASSES = {
    "dossier_number": "field_1",
    "insured_name": "field_3",
    "procedure": "field_219",
    "registration": "field_8",
    "garage": "field_40",
    "estimate_amount_raw": "field_100",
    "portal_status": "field_77",
    "city": "field_655",
    "observation_count": "field_318",
    "agreement_login": "field_300",
}

_DETAIL_FIELD_CLASSES = {
    "date_creation": "field_107",
    "date_premiere_fin_prevue": "field_113",
    "date_fin_travaux_prevue": "field_138",
    "date_envoi_devis_garage": "field_114",
    "date_photos_avant": "field_260",
}


class PortalMarkupError(ValueError):
    """The page did not contain the markup this parser depends on."""


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _cell(row: Tag, field_class: str) -> str:
    return _text(row.select_one(f".{field_class}"))


def _details_href(row: Tag) -> str:
    for anchor in row.select("a[href]"):
        href = str(anchor.get("href", ""))
        if _DETAILS_HREF_MARKER in href:
            return href.strip()
    raise PortalMarkupError("dossier row has no exact Details link")


def parse_queue_page(html: str) -> QueueSnapshot:
    """Parse one rendered ``#view_1874`` page (one pagination page)."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("#view_1874")
    if root is None:
        raise PortalMarkupError("OmegaFlow list root #view_1874 is missing")

    rows: list[QueueRow] = []
    seen_ids: set[str] = set()
    for row in root.select("table tbody tr[id]"):
        record_id = str(row.get("id", "")).strip()
        if not record_id or record_id in seen_ids:
            continue
        seen_ids.add(record_id)
        fields = {name: _cell(row, cls) for name, cls in _LIST_FIELD_CLASSES.items()}
        rows.append(
            QueueRow(
                record_id=record_id,
                details_href=_details_href(row),
                **fields,
            )
        )

    return QueueSnapshot(rows=tuple(rows), pages_seen=1)


def parse_page_count(html: str) -> int:
    """Read the total number of pages from the ``.kn-page-select`` dropdown.

    Returns 1 when the selector is absent (a single-page result set).
    """
    soup = BeautifulSoup(html, "html.parser")
    page_select = soup.select_one("#view_1874 .kn-page-select")
    if page_select is None:
        return 1
    values = []
    for option in page_select.select("option[value]"):
        try:
            values.append(int(str(option.get("value"))))
        except ValueError:
            continue
    return max(values) if values else 1


def merge_snapshots(snapshots: list[QueueSnapshot]) -> QueueSnapshot:
    """Deduplicate rows by stable record ID across pagination pages."""
    rows_by_id: dict[str, QueueRow] = {}
    for snapshot in snapshots:
        for row in snapshot.rows:
            rows_by_id[row.record_id] = row
    return QueueSnapshot(rows=tuple(rows_by_id.values()), pages_seen=len(snapshots))


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


def parse_dossier_details(html: str, timezone_id: str) -> DossierDates:
    soup = BeautifulSoup(html, "html.parser")
    values: dict[str, object] = {}
    for attr, field_class in _DETAIL_FIELD_CLASSES.items():
        raw = _text(soup.select_one(f".{field_class} .kn-detail-body"))
        parsed, raw_clean = parse_french_date(raw, timezone_id)
        values[attr] = parsed
        values[f"{attr}_raw"] = raw_clean
    return DossierDates(**values)


_LOGIN_FORM_MARKERS = re.compile(r"type=[\"']password[\"']", re.IGNORECASE)
_SESSION_VALIDATION_MARKERS = ("refreshsession", "validate your session", "valider votre session")


def detect_auth_required(html: str) -> bool:
    """True when the login form or the session-revalidation screen is shown.

    OmegaFlow (Knack) has two distinct unauthenticated states: a real login
    form (``#email`` + a password input) and a session-revalidation panel
    (``#refreshSession`` button, no credential fields). Both must be
    treated as AUTH_REQUIRED.
    """
    lowered = html.casefold()
    if _LOGIN_FORM_MARKERS.search(html):
        return True
    return any(marker in lowered for marker in _SESSION_VALIDATION_MARKERS)
