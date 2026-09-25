"""Immutable description of one observable OmegaFlow queue.

Pure data: no browser, HTTP or database imports. The concrete catalog (with
the captured routes, view ids and column classes) lives in
``infrastructure.portal.workflow_catalog``; the reader, the parser and the
reconciliation policy all consume the same :class:`WorkflowDefinition`, so a
queue is described exactly once.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from rma_portal.domain.enums import NotificationClass, WorkflowRulesStatus

COMMON_FIELD_KEYS = frozenset(
    {
        "dossier_number",
        "insured_name",
        "procedure",
        "registration",
        "garage",
        "portal_status",
        "city",
        "estimate_amount_raw",
        "observation_count",
        "agreement_login",
    }
)
"""Field keys that map to shared ``Dossier`` columns: every queue that renders one of them
refreshes the same dossier identity, so a dossier is stored once however many queues list it."""


class FieldSource(enum.StrEnum):
    LIST = "LIST"
    """A ``td.field_N`` cell of the queue table."""

    DETAIL = "DETAIL"
    """A ``.field_N .kn-detail-body`` value on the shared dossier detail page."""


class FieldKind(enum.StrEnum):
    """What a captured field means for change detection.

    ======== ========= =========
    kind     material  alerting
    ======== ========= =========
    IDENTITY no        no
    CONTEXT  yes       no
    STATUS   yes       yes
    DATE     yes       yes
    ACTION   yes       yes
    ======== ========= =========

    A *material* change produces an activity event; an *alerting* change may
    also produce an unread alert, but only in an ``ACTION`` workflow.
    """

    IDENTITY = "IDENTITY"
    CONTEXT = "CONTEXT"
    STATUS = "STATUS"
    DATE = "DATE"
    ACTION = "ACTION"

    @property
    def material(self) -> bool:
        return self is not FieldKind.IDENTITY

    @property
    def alerting(self) -> bool:
        return self in (FieldKind.STATUS, FieldKind.DATE, FieldKind.ACTION)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One captured column or detail value."""

    key: str
    """Stable application key (snake case), unique inside a definition."""

    css_class: str
    """Captured Knack field class, for example ``field_77``."""

    label: str
    """French label shown to employees."""

    source: FieldSource = FieldSource.LIST
    kind: FieldKind = FieldKind.CONTEXT
    date: bool = False
    """True when the text is a ``dd/mm/yyyy`` date that can drive display and sorting."""


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """A captured read-only filter applied through the view's search form."""

    field_class: str
    """Field class of the filtered control, for example ``field_219``."""

    operator: str
    value: str
    """The exact captured option value."""

    label: str
    """The option's French label, used to describe the workflow."""

    control_selector: str
    """Captured selector of the (Chosen-hidden) native ``<select>``."""


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    key: str
    name: str
    category: str
    route: str
    view_id: str
    notification_class: NotificationClass
    rules_status: WorkflowRulesStatus
    sort_order: int
    fields: tuple[FieldSpec, ...]
    primary_date: tuple[str, ...] = ()
    """Field keys tried in order for the primary display/sort date. When none
    holds a parseable date the detection time is used."""

    detail_required: tuple[str, ...] = ()
    """Detail field keys this workflow needs. A dossier whose value is still
    blank is re-read, at most once per ``detail_refresh_seconds``."""

    detail_refresh_seconds: int = 3600
    scene_id: str | None = None
    """Captured Knack scene id (documentation and API-contract evidence)."""

    follows: tuple[str, ...] = ()
    """Keys of the upstream workflows this queue is the documented next stage of. A dossier
    that appears here adds a transition event to its upstream membership; it never
    closes the upstream work, which employees keep control of."""

    filter: FilterSpec | None = None
    submit_search_on_open: bool = False
    """Technical: a search-first Knack view renders its search form but no table until the form
    is submitted. The reader submits it once, unchanged, right after opening the view, then
    waits for the table header. Ignored when ``filter`` is set (the filter submits the form)."""

    catalog_version: int = 1
    evidence: str = ""
    """Which capture established this contract (never customer data)."""

    material_fields: frozenset[str] = field(init=False)
    alert_fields: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        keys = [spec.key for spec in self.fields]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.key}: duplicate field keys")
        known = set(keys)
        for name, referenced in (
            ("primary_date", self.primary_date),
            ("detail_required", self.detail_required),
        ):
            missing = [key for key in referenced if key not in known]
            if missing:
                raise ValueError(f"{self.key}: {name} references unknown fields {missing}")
        list_material = {
            spec.key
            for spec in self.fields
            if spec.source is FieldSource.LIST and spec.kind.material
        }
        list_alert = {
            spec.key
            for spec in self.fields
            if spec.source is FieldSource.LIST and spec.kind.alerting
        }
        object.__setattr__(self, "material_fields", frozenset(list_material))
        object.__setattr__(self, "alert_fields", frozenset(list_alert))

    @property
    def root_selector(self) -> str:
        return f"#{self.view_id}"

    @property
    def list_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.fields if spec.source is FieldSource.LIST)

    @property
    def detail_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.fields if spec.source is FieldSource.DETAIL)

    def spec(self, key: str) -> FieldSpec:
        for spec in self.fields:
            if spec.key == key:
                return spec
        raise KeyError(key)

    @property
    def requires_detail(self) -> bool:
        return bool(self.detail_required)
