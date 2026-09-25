"""The workflow catalog must match the approved V2 plan exactly."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from rma_portal.application.workflow_catalog_sync import WorkflowCatalogSync
from rma_portal.domain.enums import NotificationClass, WorkflowRulesStatus
from rma_portal.domain.workflow_definition import (
    FieldKind,
    FieldSource,
    FieldSpec,
    WorkflowDefinition,
)
from rma_portal.infrastructure.portal.workflow_catalog import (
    COMMON_FIELD_KEYS,
    SHARED_DETAIL_FIELDS,
    StaticWorkflowCatalog,
    default_catalog,
)

PLAN = Path(__file__).parents[3] / "docs" / "rma-platform-v2-implementation-plan.md"

# Exact captured `field_219` (procedure) option values.
PROCEDURE_VALUES = {
    "agreement_garage": "5ed644a2faf17c0015d8c367",
    "agreement_normal": "5eebcdf9c791c6001505bd70",
    "agreement_appreciation": "6039196ec1fce4001c27ab66",
    "agreement_collegial_cid": "5f3e5a57696b8700158842e6",
    "agreement_hifad": "5ee405e42e70690015f4dcb0",
}

EMPTY_CAPTURED = {
    "agreement_appreciation",
    "agreement_hifad",
    "collegial_second_agreed",
    "collegial_second_completed",
    "reserves_pending",
}


def _plan_rows() -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not re.fullmatch(r"`[a-z_0-9]+`", cells[0]):
            continue
        rows[cells[0].strip("`")] = {
            "route": cells[2],
            "view": cells[3],
            "class": cells[4],
        }
    return rows


@pytest.fixture(scope="module")
def catalog() -> StaticWorkflowCatalog:
    return default_catalog()


def test_catalog_registers_every_workflow_of_the_plan_and_nothing_else(catalog):
    plan = _plan_rows()
    assert len(plan) == 21
    assert {d.key for d in catalog.definitions()} == set(plan)


def test_route_view_filter_and_class_match_the_plan_table(catalog):
    plan = _plan_rows()
    for definition in catalog.definitions():
        row = plan[definition.key]
        route_cell = re.findall(r"`([^`]+)`", row["route"])
        if route_cell and route_cell[0].startswith("#"):  # second agreement: prose in the plan
            assert definition.route == route_cell[0], definition.key
        view_cell = re.findall(r"`([^`]+)`", row["view"])
        assert definition.view_id == view_cell[0], definition.key
        filter_cells = [c for c in view_cell[1:] if c.startswith("field_")]
        if filter_cells:
            field_class, value = filter_cells[0].split("=")
            assert definition.filter is not None, definition.key
            assert (definition.filter.field_class, definition.filter.value) == (field_class, value)
        else:
            garage = definition.key == "agreement_garage"
            assert (definition.filter is None) or garage
        assert definition.notification_class.value == row["class"], definition.key


def test_procedure_filters_use_the_exact_captured_option_values(catalog):
    filtered = {d.key: d for d in catalog.definitions() if d.filter is not None}
    assert set(filtered) == set(PROCEDURE_VALUES)
    for key, value in PROCEDURE_VALUES.items():
        spec = filtered[key].filter
        assert spec.value == value
        assert spec.field_class == "field_219"
        assert spec.control_selector == "#kn-conn-1-field_219"
        assert filtered[key].view_id == "view_1874"


def test_second_agreement_route_is_the_captured_scene_116_route(catalog):
    definition = catalog.get("second_agreement_pending")
    assert definition.route == "#dossiers-en-instance-2-accord-pec2/"
    assert (definition.scene_id, definition.view_id) == ("scene_116", "view_149")


def test_rules_status_only_garage_is_confirmed_and_informational_classes(catalog):
    for definition in catalog.definitions():
        expected = (
            WorkflowRulesStatus.CONFIRMED
            if definition.key == "agreement_garage"
            else WorkflowRulesStatus.CAPTURE_DERIVED
        )
        assert definition.rules_status is expected, definition.key
    informational = {
        d.key for d in catalog.definitions() if d.notification_class is NotificationClass.INFORMATIONAL
    }
    assert informational == {"collegial_second_completed", "agreement_validated"}


def test_empty_captured_workflows_are_registered_with_full_column_contracts(catalog):
    for key in EMPTY_CAPTURED:
        definition = catalog.get(key)
        assert definition is not None
        assert len(definition.list_fields) >= 8, key
        assert any(spec.css_class == "field_1" for spec in definition.list_fields)


def test_every_definition_is_internally_consistent(catalog):
    for definition in catalog.definitions():
        classes = [s.css_class for s in definition.list_fields]
        assert len(classes) == len(set(classes)), f"{definition.key}: duplicated column class"
        assert re.fullmatch(r"view_\d+", definition.view_id)
        assert definition.root_selector == f"#{definition.view_id}"
        assert definition.list_fields[0].css_class == "field_1"
        for spec in definition.fields:
            assert re.fullmatch(r"field_\d+", spec.css_class), (definition.key, spec.key)
            if spec.key in COMMON_FIELD_KEYS:
                assert spec.source is FieldSource.LIST
        for key in definition.primary_date:
            assert definition.spec(key).date, (definition.key, key)
        for key in definition.detail_required:
            assert definition.spec(key).source is FieldSource.DETAIL


def test_material_and_alert_fields_follow_field_kinds(catalog):
    definition = catalog.get("agreement_garage")
    assert "dossier_number" not in definition.material_fields  # identity
    assert "observation_count" in definition.material_fields  # context: activity only
    assert "observation_count" not in definition.alert_fields
    assert {"portal_status", "agreement"} <= definition.alert_fields
    # Detail-page fields never drive list change detection.
    assert "date_envoi_devis_garage" not in definition.material_fields


def test_garage_keeps_v1_quote_date_refresh_every_poll(catalog):
    garage = catalog.get("agreement_garage")
    assert garage.primary_date == ("date_envoi_devis_garage",)
    assert garage.detail_required == ("date_envoi_devis_garage",)
    assert garage.detail_refresh_seconds == 0
    assert catalog.get("agreement_normal").detail_refresh_seconds > 0


def test_shared_detail_fields_include_the_five_v1_dates():
    keys = {spec.key for spec in SHARED_DETAIL_FIELDS}
    assert {
        "date_creation",
        "date_premiere_fin_prevue",
        "date_fin_travaux_prevue",
        "date_envoi_devis_garage",
        "date_photos_avant",
    } <= keys
    classes = {spec.key: spec.css_class for spec in SHARED_DETAIL_FIELDS}
    assert classes["date_envoi_devis_garage"] == "field_114"
    assert classes["date_avis_dommage"] == "field_852"


def test_field_kind_semantics():
    assert not FieldKind.IDENTITY.material
    assert FieldKind.CONTEXT.material and not FieldKind.CONTEXT.alerting
    assert all(k.alerting for k in (FieldKind.STATUS, FieldKind.DATE, FieldKind.ACTION))


def test_definition_rejects_duplicate_or_unknown_field_keys():
    base = {
        "key": "x",
        "name": "X",
        "category": "C",
        "route": "#x/",
        "view_id": "view_1",
        "notification_class": NotificationClass.ACTION,
        "rules_status": WorkflowRulesStatus.CAPTURE_DERIVED,
        "sort_order": 1,
    }
    field = FieldSpec("a", "field_1", "A")
    with pytest.raises(ValueError, match="duplicate"):
        WorkflowDefinition(**base, fields=(field, field))
    with pytest.raises(ValueError, match="unknown"):
        WorkflowDefinition(**base, fields=(field,), primary_date=("missing",))


def test_catalog_rejects_duplicate_workflow_keys(catalog):
    first = catalog.definitions()[0]
    with pytest.raises(ValueError, match="duplicate"):
        StaticWorkflowCatalog([first, first])


# --- database seeding -------------------------------------------------------------------------


def test_catalog_sync_creates_every_workflow_enabled_and_is_idempotent(uow_factory, portal_account_id):
    sync = WorkflowCatalogSync(uow_factory, default_catalog())

    first = sync.sync()
    second = sync.sync()

    assert (first.created, first.refreshed) == (21, 0)
    assert (second.created, second.refreshed) == (0, 21)
    with uow_factory() as uow:
        workflows = uow.workflows.list_for_account(portal_account_id)
        assert len(workflows) == 21
        assert all(w.enabled for w in workflows)
        garage = uow.workflows.get_by_key(portal_account_id, "agreement_garage")
        assert garage.rules_status is WorkflowRulesStatus.CONFIRMED
        assert garage.filter_value == PROCEDURE_VALUES["agreement_garage"]
        assert garage.primary_date_key == "date_envoi_devis_garage"
        hifad = uow.workflows.get_by_key(portal_account_id, "hifad_search")
        assert hifad.filter_field is None


def test_catalog_sync_never_overrides_administrator_decisions(uow_factory, portal_account_id):
    sync = WorkflowCatalogSync(uow_factory, default_catalog())
    sync.sync()
    with uow_factory() as uow:
        photos = uow.workflows.get_by_key(portal_account_id, "photos_pending")
        uow.workflows.update_admin_config(
            photos.id,
            enabled=False,
            rules_status=WorkflowRulesStatus.CONFIRMED,
            notification_class=NotificationClass.INFORMATIONAL,
        )
        uow.commit()

    sync.sync()

    with uow_factory() as uow:
        photos = uow.workflows.get_by_key(portal_account_id, "photos_pending")
        assert photos.enabled is False
        assert photos.rules_status is WorkflowRulesStatus.CONFIRMED
        assert photos.notification_class is NotificationClass.INFORMATIONAL


def test_catalog_sync_without_an_account_is_a_no_op(uow_factory):
    result = WorkflowCatalogSync(uow_factory, default_catalog()).sync()
    assert (result.created, result.refreshed) == (0, 0)
