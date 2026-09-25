"""Pure HTML parsing for any catalogued workflow view.

Takes already-rendered HTML and a :class:`WorkflowDefinition`; no browser
import, so every rule is exercised against synthetic fixtures in
``tests/parser``. Selectors are derived from the definition (``#view_N`` root,
``td.field_N`` cells) plus the pagination markup captured for V1:
``.kn-page-select`` and ``.kn-change-page.kn-next`` (a disabled "next" control
gains an extra ``disabled`` class).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from bs4 import BeautifulSoup, Tag

from rma_portal.application.dto import WorkflowQueueRow, WorkflowSnapshot
from rma_portal.domain.workflow_definition import FieldSpec, WorkflowDefinition
from rma_portal.infrastructure.portal.parser import PortalMarkupError

# Trailing slash on purpose: some rows also carry a decoy "view-dossier-details7/<id>"
# observation icon link that this marker does not match.
DETAILS_HREF_MARKER = "view-dossier-details/"

PAGE_SELECT = ".kn-page-select"
NEXT_CONTROL = ".kn-change-page.kn-next"


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _details_href(row: Tag) -> str:
    for anchor in row.select("a[href]"):
        href = str(anchor.get("href", ""))
        if DETAILS_HREF_MARKER in href:
            return href.strip()
    return ""


def _root(html: str, definition: WorkflowDefinition) -> Tag:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(definition.root_selector)
    if root is None:
        raise PortalMarkupError(f"OmegaFlow list root {definition.root_selector} is missing")
    return root


def parse_workflow_page(html: str, definition: WorkflowDefinition) -> WorkflowSnapshot:
    """Parse one rendered pagination page of ``definition``'s view.

    An empty table is a valid result. A row without an exact Details link is kept
    (its identity is the row id) with an empty ``details_href`` so the dossier is
    still detected; its detail simply cannot be read.
    """
    root = _root(html, definition)
    rows: list[WorkflowQueueRow] = []
    seen: set[str] = set()
    for row in root.select("table tbody tr[id]"):
        record_id = str(row.get("id", "")).strip()
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        fields = {
            spec.key: _text(row.select_one(f".{spec.css_class}")) for spec in definition.list_fields
        }
        rows.append(
            WorkflowQueueRow(record_id=record_id, details_href=_details_href(row), fields=fields)
        )
    return WorkflowSnapshot(workflow_key=definition.key, rows=tuple(rows), pages_seen=1)


def has_table_header(html: str, definition: WorkflowDefinition) -> bool:
    """Positive evidence the view rendered, even when it has zero rows."""
    return _root(html, definition).select_one("table thead th") is not None


def parse_total_pages(html: str, definition: WorkflowDefinition) -> int:
    """The highest ``option`` value of the first ``.kn-page-select``; 1 when absent."""
    select = _root(html, definition).select_one(PAGE_SELECT)
    if select is None:
        return 1
    values: list[int] = []
    for option in select.select("option[value]"):
        try:
            values.append(int(str(option.get("value"))))
        except ValueError:
            continue
    return max(values) if values else 1


def has_enabled_next(html: str, definition: WorkflowDefinition) -> bool:
    """True when a "next" control exists and does not carry the ``disabled`` class."""
    control = _root(html, definition).select_one(NEXT_CONTROL)
    if control is None:
        return False
    classes = control.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    return "disabled" not in classes


def merge_workflow_snapshots(
    definition: WorkflowDefinition, snapshots: Sequence[WorkflowSnapshot]
) -> WorkflowSnapshot:
    """Deduplicate rows by stable record id across pagination pages."""
    rows: dict[str, WorkflowQueueRow] = {}
    for snapshot in snapshots:
        for row in snapshot.rows:
            rows[row.record_id] = row
    return WorkflowSnapshot(
        workflow_key=definition.key, rows=tuple(rows.values()), pages_seen=len(snapshots)
    )


def parse_detail_fields(html: str, fields: Iterable[FieldSpec]) -> dict[str, str]:
    """Raw text of every requested detail field present on the page.

    Fields absent from this dossier's page are omitted (not blank), so callers can
    tell "not shown for this dossier" from "shown but empty".
    """
    soup = BeautifulSoup(html, "html.parser")
    values: dict[str, str] = {}
    for spec in fields:
        node = soup.select_one(f".{spec.css_class} .kn-detail-body")
        if node is not None:
            values[spec.key] = _text(node)
    return values
