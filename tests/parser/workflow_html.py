"""Synthetic Knack-style HTML for any workflow definition.

Everything here is invented: record ids are fake, cell text is generic. It
mirrors only the *structure* confirmed from the captures (``#view_N`` root,
``td.field_N`` cells inside ``tr[id]``, an exact ``view-dossier-details/``
link next to a decoy ``view-dossier-details7/`` observation link, two
``.kn-page-select`` dropdowns and a ``.kn-change-page.kn-next`` control that
gains a ``disabled`` class on the last page).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape

from rma_portal.domain.workflow_definition import WorkflowDefinition


def synthetic_row(record_id: str, **fields: str) -> dict:
    return {"id": record_id, "fields": fields}


def _cell(css_class: str, text: str, index: int) -> str:
    return f'<td class="{css_class} cell-edit"><span class="col-{index}">{escape(text)}</span></td>'


def render_view_body(
    definition: WorkflowDefinition,
    rows: Sequence[Mapping],
    *,
    total_pages: int = 1,
    next_disabled: bool = True,
    include_header: bool = True,
    details: bool = True,
    pagination: bool | None = None,
) -> str:
    specs = definition.list_fields
    header = ""
    if include_header:
        cells = "".join(f'<th class="{spec.css_class}">{escape(spec.label)}</th>' for spec in specs)
        header = f"<thead><tr>{cells}<th>Détails</th></tr></thead>"
    body_rows = []
    for row in rows:
        record_id = row["id"]
        values = row.get("fields", {})
        cells = "".join(
            _cell(spec.css_class, values.get(spec.key, ""), index)
            for index, spec in enumerate(specs)
        )
        links = ""
        if details:
            base = definition.route.rstrip("/")
            links = (
                f'<td><a href="{base}/view-dossier-details/{record_id}/">Détails</a>'
                f'<a href="{base}/view-dossier-details7/{record_id}/">Obs</a></td>'
            )
        body_rows.append(f'<tr id="{record_id}">{cells}{links}</tr>')
    show_pagination = pagination if pagination is not None else total_pages > 1
    pager = ""
    if show_pagination:
        options = "".join(f'<option value="{n}">{n}</option>' for n in range(1, total_pages + 1))
        next_class = "kn-change-page kn-next" + (" disabled" if next_disabled else "")
        pager = (
            f'<div class="kn-pagination"><select class="kn-page-select">{options}</select>'
            f'<a class="{next_class}" href="#">Suivant</a>'
            f'<select class="kn-page-select">{options}</select></div>'
        )
    return (
        f'<div id="{definition.view_id}" class="kn-view">'
        f'<table class="kn-table knTable">{header}<tbody>{"".join(body_rows)}</tbody></table>'
        f"{pager}</div>"
    )


def render_page(*view_bodies: str) -> str:
    return f'<html><body><div id="knack-body">{"".join(view_bodies)}</div></body></html>'


def render_view(definition: WorkflowDefinition, rows: Sequence[Mapping], **kwargs) -> str:
    return render_page(render_view_body(definition, rows, **kwargs))


def render_detail(values_by_css: Mapping[str, str]) -> str:
    blocks = "".join(
        f'<div class="kn-detail {css}"><div class="kn-detail-label">x</div>'
        f'<div class="kn-detail-body"><span><span>{escape(text)}</span></span></div></div>'
        for css, text in values_by_css.items()
    )
    return f'<html><body><div class="kn-details">{blocks}</div></body></html>'
