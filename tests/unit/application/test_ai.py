"""Advisory AI: structured results, isolation from every business action, fail-soft outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from rma_portal.application.ai import (
    DISCLAIMER,
    AdvisorService,
    AiContext,
    AiInvalidOutputError,
    AiJobHandler,
    AiTimeoutError,
    AiUnavailableError,
    AnomalyGroup,
    AnomalyGroupsResult,
    DailySummaryResult,
    DossierSummaryResult,
    HighlightExplanationResult,
    PrioritySuggestionResult,
    detect_anomalies,
    deterministic_reasons,
)
from rma_portal.application.views import ItemView
from rma_portal.application.work_service import OccurrenceNotFoundError, WorkService
from rma_portal.domain.enums import (
    NotificationClass,
    NotificationKind,
    OccurrenceOrigin,
    WorkflowRulesStatus,
    WorkStatus,
)
from rma_portal.domain.models import OutboxMessage
from rma_portal.infrastructure.portal.workflow_catalog import default_catalog
from tests.support import seed_member, seed_workflows

PHOTOS = "photos_pending"
GARAGE = "agreement_garage"
LONG_AGO = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)


class FakeAdvisor:
    """A scriptable AIAdvisor: returns valid results unless told to fail."""

    model = "fake-model:1b"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[AiContext] = []
        self.explanation_facts: list[str] | None = None
        self.anomaly_ids: list[str] | None = None

    def _record(self, context: AiContext) -> None:
        self.calls.append(context)
        if self.error is not None:
            raise self.error

    async def daily_summary(self, context):
        self._record(context)
        return DailySummaryResult(headline="Journée calme.", highlights=["3 arrivées"], attention=[])

    async def dossier_summary(self, context):
        self._record(context)
        return DossierSummaryResult(summary="Dossier en cours.", key_points=["Une file"], open_questions=[])

    async def explain_highlight(self, context):
        self._record(context)
        facts = self.explanation_facts if self.explanation_facts is not None else list(context.payload["faits"][:2])
        return HighlightExplanationResult(explanation="Il vient d'arriver.", facts_used=facts)

    async def suggest_priority(self, context):
        self._record(context)
        return PrioritySuggestionResult(priority="HAUTE", next_action="Ouvrir le dossier.", rationale="Nouveau.")

    async def group_anomalies(self, context):
        self._record(context)
        ids = self.anomaly_ids
        if ids is None:
            ids = [a["id"] for a in context.payload["anomalies"]]
        return AnomalyGroupsResult(groups=[AnomalyGroup(title="Groupe", summary="Résumé", anomaly_ids=ids)])

    async def ping(self) -> bool:
        return True


def _service(uow_factory, advisor) -> AdvisorService:
    work = WorkService(uow_factory, default_catalog(), base_url="https://omegaflow.example")
    return AdvisorService(uow_factory, work, advisor, clock=lambda: NOW)


def _seed(uow_factory, account_id, *, old: bool = False, occurrences: int = 1):
    seed_workflows(uow_factory, only=[PHOTOS, GARAGE])
    dossier_id, membership_id = seed_member(
        uow_factory,
        account_id,
        PHOTOS,
        "p1",
        insured_name="Nadia Confidentielle",
        registration="999-Z-99",
        now=LONG_AGO if old else NOW - timedelta(days=1),
    )
    with uow_factory() as uow:
        workflow = uow.workflows.get_by_key(account_id, PHOTOS)
        for number in range(2, occurrences + 1):
            uow.workflow_occurrences.create(
                membership_id=membership_id,
                workflow_id=workflow.id,
                dossier_id=dossier_id,
                occurrence_number=number,
                origin=OccurrenceOrigin.RETURNED,
                detected_at=LONG_AGO if old else NOW - timedelta(days=1),
            )
            uow.workflow_memberships.reactivate(
                membership_id, seen_at=NOW, captured_fields={}, fingerprint="x"
            )
        occurrence = uow.workflow_occurrences.latest_for_membership(membership_id)
        uow.commit()
    return dossier_id, membership_id, occurrence.id


def _scalar(engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


def _user(uow_factory) -> int:
    from rma_portal.domain.enums import Role
    from rma_portal.domain.models import User

    with uow_factory() as uow:
        user = uow.users.create(User(None, "alice", "Alice", "h", Role.EMPLOYEE, True, NOW))
        uow.commit()
        return user.id


# --- disabled -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_feature_reports_disabled_and_writes_nothing_when_no_advisor(
    uow_factory, portal_account_id, engine
):
    dossier_id, membership_id, occurrence_id = _seed(uow_factory, portal_account_id)
    user = _user(uow_factory)
    service = _service(uow_factory, None)

    outcomes = [
        await service.daily_summary(user),
        await service.dossier_summary(user, dossier_id),
        await service.explain_highlight(user, occurrence_id),
        await service.suggest_priority(user, membership_id),
        await service.anomalies(user),
    ]

    assert [o.status for o in outcomes] == ["DISABLED"] * 5
    assert service.enabled is False and (await service.status())["healthy"] is None
    assert _scalar(engine, "select count(*) from ai_runs") == 0


# --- structured results, audit and privacy ------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_summary_sends_only_aggregate_counts_and_audits_the_run(
    uow_factory, portal_account_id, engine
):
    _seed(uow_factory, portal_account_id)
    user = _user(uow_factory)
    advisor = FakeAdvisor()
    service = _service(uow_factory, advisor)

    outcome = await service.daily_summary(user)

    assert outcome.status == "OK" and outcome.result["headline"] == "Journée calme."
    assert outcome.disclaimer == DISCLAIMER and outcome.model == "fake-model:1b"
    (context,) = advisor.calls
    blob = str(context.payload)
    for private in ("Nadia", "Confidentielle", "999-Z-99"):
        assert private not in blob
    run = engine.connect().execute(
        text("select feature, status, model, prompt_version, length(context_hash), subject_type, result_json from ai_runs")
    ).one()
    assert tuple(run[:6]) == ("DAILY_SUMMARY", "SUCCEEDED", "fake-model:1b", "v1", 64, "global")
    assert "Journée calme." in run[6] and "nouveaux_non_lus" not in run[6]  # the answer, not the context


@pytest.mark.asyncio
async def test_dossier_summary_context_omits_identity_fields_but_keeps_agency_notes(
    uow_factory, portal_account_id
):
    dossier_id, *_ = _seed(uow_factory, portal_account_id)
    user = _user(uow_factory)
    with uow_factory() as uow:
        uow.dossier_notes.add(dossier_id, user, "Le garage rappelle demain.", NOW)
        uow.commit()
    advisor = FakeAdvisor()

    outcome = await _service(uow_factory, advisor).dossier_summary(user, dossier_id)

    assert outcome.status == "OK"
    blob = str(advisor.calls[0].payload)
    assert "Le garage rappelle demain." in blob
    for private in ("Nadia", "Confidentielle", "999-Z-99", "Garage"):
        assert private not in blob
    assert "D-p1" in blob  # the dossier number is what the answer must refer to


@pytest.mark.asyncio
async def test_identical_context_is_served_from_the_audit_cache(uow_factory, portal_account_id, engine):
    dossier_id, *_ = _seed(uow_factory, portal_account_id)
    user = _user(uow_factory)
    advisor = FakeAdvisor()
    service = _service(uow_factory, advisor)

    first = await service.dossier_summary(user, dossier_id)
    second = await service.dossier_summary(user, dossier_id)
    with uow_factory() as uow:
        uow.dossier_notes.add(dossier_id, user, "Nouvelle information.", NOW)
        uow.commit()
    third = await service.dossier_summary(user, dossier_id)

    assert (first.cached, second.cached, third.cached) == (False, True, False)
    assert len(advisor.calls) == 2  # the changed context recomputed, the identical one did not
    assert _scalar(engine, "select count(*) from ai_runs") == 2


# --- failure isolation --------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "run_status"),
    [
        (AiTimeoutError("trop lent"), "TIMEOUT", "TIMEOUT"),
        (AiUnavailableError("injoignable"), "UNAVAILABLE", "FAILED"),
        (AiInvalidOutputError("pas du JSON"), "INVALID_OUTPUT", "INVALID_OUTPUT"),
        (RuntimeError("bug d'adaptateur"), "UNAVAILABLE", "FAILED"),
        (
            ValidationError.from_exception_data("DailySummaryResult", []),
            "INVALID_OUTPUT",
            "INVALID_OUTPUT",
        ),
    ],
    ids=["timeout", "unavailable", "invalid", "crash", "schema"],
)
async def test_any_advisor_failure_is_an_outcome_never_an_exception(
    error, status, run_status, uow_factory, portal_account_id, engine
):
    dossier_id, membership_id, occurrence_id = _seed(uow_factory, portal_account_id)
    user = _user(uow_factory)
    service = _service(uow_factory, FakeAdvisor(error))

    outcomes = [
        await service.daily_summary(user),
        await service.dossier_summary(user, dossier_id),
        await service.explain_highlight(user, occurrence_id),
        await service.suggest_priority(user, membership_id),
    ]

    assert [o.status for o in outcomes] == [status] * 4
    assert all(o.result is None and o.message for o in outcomes)
    assert _scalar(engine, f"select count(*) from ai_runs where status = '{run_status}'") == 4


@pytest.mark.asyncio
async def test_a_broken_audit_table_never_fails_the_request(
    uow_factory, portal_account_id, monkeypatch
):
    _seed(uow_factory, portal_account_id)
    user = _user(uow_factory)

    def boom(self, run):
        raise RuntimeError("audit disk full")

    monkeypatch.setattr(
        "rma_portal.infrastructure.db.workflow_repositories.SqlAlchemyAiRunRepository.add", boom
    )

    outcome = await _service(uow_factory, FakeAdvisor()).daily_summary(user)

    assert outcome.status == "OK"


# --- deterministic facts, validated ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_highlight_explanation_reuses_only_the_deterministic_facts(uow_factory, portal_account_id):
    _, _, occurrence_id = _seed(uow_factory, portal_account_id, occurrences=2)
    user = _user(uow_factory)

    good = await _service(uow_factory, FakeAdvisor()).explain_highlight(user, occurrence_id)

    assert good.status == "OK" and good.facts
    assert any("apparition n°2 (RETURNED)" in fact for fact in good.facts)
    assert set(good.result["facts_used"]) <= set(good.facts)


@pytest.mark.asyncio
async def test_highlight_explanation_citing_an_invented_fact_is_rejected(uow_factory, portal_account_id):
    _, _, occurrence_id = _seed(uow_factory, portal_account_id)
    advisor = FakeAdvisor()
    advisor.explanation_facts = ["un fait inventé par le modèle"]

    bad = await _service(uow_factory, advisor).explain_highlight(_user(uow_factory), occurrence_id)

    assert bad.status == "INVALID_OUTPUT" and bad.result is None


@pytest.mark.asyncio
async def test_explaining_an_unknown_occurrence_is_reported_to_the_caller(uow_factory, portal_account_id):
    _seed(uow_factory, portal_account_id)
    service = _service(uow_factory, FakeAdvisor())

    with pytest.raises(OccurrenceNotFoundError):
        await service.explain_highlight(1, 424242)


@pytest.mark.asyncio
async def test_priority_is_a_labelled_suggestion(uow_factory, portal_account_id):
    _, membership_id, _ = _seed(uow_factory, portal_account_id)
    user = _user(uow_factory)

    outcome = await _service(uow_factory, FakeAdvisor()).suggest_priority(user, membership_id)

    assert outcome.result["priority"] in {"HAUTE", "NORMALE", "BASSE"}
    assert "Suggestion" in outcome.disclaimer and "n'effectue aucune action" in outcome.disclaimer


@pytest.mark.asyncio
async def test_anomaly_grouping_groups_what_the_rules_detected(uow_factory, portal_account_id):
    _seed(uow_factory, portal_account_id, old=True, occurrences=3)
    advisor = FakeAdvisor()

    outcome = await _service(uow_factory, advisor).anomalies(_user(uow_factory))

    kinds = {a["kind"] for a in advisor.calls[0].payload["anomalies"]}
    assert kinds == {"REPEATED_RETURN", "OLD_IN_QUEUE"}
    assert outcome.status == "OK"


@pytest.mark.asyncio
async def test_anomaly_grouping_rejects_ids_the_rules_never_detected(uow_factory, portal_account_id):
    _seed(uow_factory, portal_account_id, old=True, occurrences=3)
    advisor = FakeAdvisor()
    advisor.anomaly_ids = ["old-9999"]

    outcome = await _service(uow_factory, advisor).anomalies(_user(uow_factory))

    assert outcome.status == "INVALID_OUTPUT"


@pytest.mark.asyncio
async def test_no_anomaly_means_no_model_call(uow_factory, portal_account_id):
    _seed(uow_factory, portal_account_id)
    advisor = FakeAdvisor()

    outcome = await _service(uow_factory, advisor).anomalies(_user(uow_factory))

    assert outcome.status == "NO_DATA" and advisor.calls == []


def _item(**overrides) -> ItemView:
    values = {
        "workflow_key": "k", "workflow_name": "File test", "workflow_category": "c",
        "notification_class": NotificationClass.ACTION, "rules_status": WorkflowRulesStatus.CAPTURE_DERIVED,
        "membership_id": 1, "occurrence_id": 1, "occurrence_number": 1,
        "occurrence_origin": OccurrenceOrigin.NEW, "dossier_id": 1, "record_id": "r",
        "dossier_number": "D-1", "insured_name": "", "registration": "", "garage": "", "procedure": "",
        "portal_status": "", "city": "", "active": True, "detected_at": NOW, "primary_date": None,
        "primary_date_raw": "", "primary_date_label": None, "queue_age_days": 0,
        "work_status": WorkStatus.TO_DO, "work_version": 1, "unread": False, "unread_kind": None,
        "fields": {}, "omegaflow_url": "",
    }
    values.update(overrides)
    return ItemView(**values)


def test_anomaly_rules_are_deterministic_and_never_an_overdue_alert():
    young = [_item(membership_id=10 + i, queue_age_days=1 + i % 4, dossier_number=f"D-y{i}") for i in range(6)]
    items = [
        *young,
        _item(membership_id=1, occurrence_number=3, dossier_number="D-1"),
        _item(membership_id=2, queue_age_days=40, dossier_number="D-2"),
        _item(membership_id=3, queue_age_days=40, dossier_number="D-3", work_status=WorkStatus.DONE),
    ]

    found = detect_anomalies(items, {"File en panne": "FAILED"})

    # A finished dossier is never "old in queue"; the failing workflow is reported once.
    assert {a.id for a in found} == {"return-1", "old-2", "failing-File en panne"}
    assert all("retard" not in a.detail.lower() for a in found)


def test_deterministic_reasons_state_plain_facts_including_the_unread_alert():
    item = _item(occurrence_number=2, occurrence_origin=OccurrenceOrigin.RETURNED, queue_age_days=5, unread_kind="WORKFLOW_ITEM_RETURNED")

    facts = deterministic_reasons(item, [])

    assert "apparition n°2 (RETURNED)" in facts
    assert "dans la file depuis 5 jour(s)" in facts
    assert "alerte non lue pour cet employé" in facts
    assert not any("retard" in f for f in facts)


# --- isolation from every business action -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_every_feature_changes_nothing_but_the_audit_table(
    uow_factory, portal_account_id, engine
):
    dossier_id, membership_id, occurrence_id = _seed(uow_factory, portal_account_id, old=True, occurrences=3)
    user = _user(uow_factory)
    with uow_factory() as uow:
        workflow = uow.workflows.get_by_key(portal_account_id, PHOTOS)
        uow.notifications.create_for_occurrence(
            dossier_id=dossier_id, workflow_id=workflow.id, occurrence_id=occurrence_id,
            kind=NotificationKind.WORKFLOW_ITEM_RETURNED, detected_at=NOW,
        )
        uow.dossier_notes.add(dossier_id, user, "note", NOW)
        uow.commit()
    tables = ("notification_reads", "notifications", "workflow_work", "dossier_notes",
              "workflow_events", "outbox_messages", "workflow_memberships", "workflow_occurrences", "dossiers")

    def snapshot():
        result = {}
        with engine.connect() as conn:
            for table in tables:
                result[table] = conn.execute(text(f"select * from {table}")).fetchall()
        return result

    before = snapshot()
    service = _service(uow_factory, FakeAdvisor())

    for outcome in (
        await service.daily_summary(user),
        await service.dossier_summary(user, dossier_id),
        await service.explain_highlight(user, occurrence_id),
        await service.suggest_priority(user, membership_id),
        await service.anomalies(user),
    ):
        assert outcome.status == "OK"

    assert snapshot() == before  # not one row changed outside ai_runs
    assert _scalar(engine, "select count(*) from ai_runs") == 5


def test_the_ai_module_touches_only_the_audit_table_and_read_queries():
    """Structural guarantee: the advisor's only repository access is ``ai_runs`` (write) and
    ``queries`` (read); it names no acknowledgement, work-status, note or outbox operation."""
    import ast

    path = Path(__file__).parents[3] / "src/rma_portal/application/ai.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    uow_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "uow"
    }
    operations = {
        "acknowledge_occurrence", "upsert", "add_note", "set_work_status", "update_workflow",
        "request_sync", "create_for_occurrence", "create_initial", "deactivate", "reactivate",
        "refresh", "mark_all_existing_as_read_for_user", "update_admin_config",
    }

    assert uow_attributes <= {"ai_runs", "queries", "commit"}
    assert "ai_runs" in uow_attributes
    assert not attributes & operations


# --- outbox job -----------------------------------------------------------------------------------------


def _message(**payload) -> OutboxMessage:
    return OutboxMessage(id=1, topic="ai.job.requested", payload=payload, created_at=NOW, available_at=NOW)


@pytest.mark.asyncio
async def test_ai_jobs_are_drained_without_effect_when_the_assistant_is_off(uow_factory, portal_account_id, engine):
    _, _, occurrence_id = _seed(uow_factory, portal_account_id)
    handler = AiJobHandler(_service(uow_factory, None))

    await handler(_message(feature="HIGHLIGHT_EXPLANATION", occurrence_id=occurrence_id))

    assert _scalar(engine, "select count(*) from ai_runs") == 0


@pytest.mark.asyncio
async def test_ai_job_precomputes_an_explanation_and_survives_failures_and_bad_input(
    uow_factory, portal_account_id, engine
):
    _, _, occurrence_id = _seed(uow_factory, portal_account_id)

    await AiJobHandler(_service(uow_factory, FakeAdvisor()))(
        _message(feature="HIGHLIGHT_EXPLANATION", occurrence_id=occurrence_id)
    )
    assert _scalar(engine, "select count(*) from ai_runs where feature = 'HIGHLIGHT_EXPLANATION'") == 1

    failing = AiJobHandler(_service(uow_factory, FakeAdvisor(AiUnavailableError("down"))))
    await failing(_message(feature="HIGHLIGHT_EXPLANATION", occurrence_id=occurrence_id))  # must not raise
    await failing(_message(feature="HIGHLIGHT_EXPLANATION", occurrence_id=987654))  # unknown occurrence
    await failing(_message(feature="SOMETHING_ELSE", occurrence_id=occurrence_id))
    await failing(_message(feature="HIGHLIGHT_EXPLANATION"))
