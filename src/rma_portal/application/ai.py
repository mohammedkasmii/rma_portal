"""Optional local-AI assistance, strictly advisory.

Everything here is derived from *stored deterministic facts*; the model never
participates in capture, reconciliation or notification creation. The advisor:

* only reads (through :class:`~rma_portal.application.work_service.WorkService`) and
  only writes ``ai_runs`` audit rows -- it has no path to acknowledge alerts, change
  work status, write notes or act on OmegaFlow;
* receives the minimum fields each feature needs (no insured names, registrations or
  amounts) and returns schema-validated structured JSON;
* fails soft: an unavailable, slow or misbehaving model yields an
  :class:`AdvisorOutcome` with a status, never an exception, so login,
  synchronization, notifications and the normal UI are unaffected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from rma_portal.application.ports import UnitOfWorkFactory
from rma_portal.application.views import ItemView
from rma_portal.application.work_service import (
    ItemQuery,
    MembershipNotFoundError,
    OccurrenceNotFoundError,
    WorkService,
)
from rma_portal.domain.enums import AiFeature, AiRunStatus
from rma_portal.domain.models import AiRun

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"

DISCLAIMER = (
    "Suggestion générée automatiquement à partir des données synchronisées : elle ne "
    "remplace pas le jugement de l'équipe et n'effectue aucune action dans OmegaFlow."
)


# --- errors raised by adapters (never by the service) -------------------------------------------


class AiError(Exception):
    """Base class of every advisor failure."""


class AiUnavailableError(AiError):
    """The model endpoint cannot be reached or answered with an error."""


class AiTimeoutError(AiError):
    """The model did not answer within the configured timeout."""


class AiInvalidOutputError(AiError):
    """The answer was not valid JSON for the requested schema."""


# --- structured results (validated against these schemas) ----------------------------------------


class DailySummaryResult(BaseModel):
    headline: str = Field(min_length=1, max_length=240)
    highlights: list[str] = Field(default_factory=list, max_length=8)
    attention: list[str] = Field(default_factory=list, max_length=6)


class DossierSummaryResult(BaseModel):
    summary: str = Field(min_length=1, max_length=900)
    key_points: list[str] = Field(default_factory=list, max_length=8)
    open_questions: list[str] = Field(default_factory=list, max_length=5)


class HighlightExplanationResult(BaseModel):
    explanation: str = Field(min_length=1, max_length=600)
    facts_used: list[str] = Field(default_factory=list, max_length=10)


class PrioritySuggestionResult(BaseModel):
    priority: Literal["HAUTE", "NORMALE", "BASSE"]
    next_action: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=500)


class AnomalyGroup(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=400)
    anomaly_ids: list[str] = Field(default_factory=list, max_length=50)


class AnomalyGroupsResult(BaseModel):
    groups: list[AnomalyGroup] = Field(default_factory=list, max_length=10)


RESULT_MODELS: dict[AiFeature, type[BaseModel]] = {
    AiFeature.DAILY_SUMMARY: DailySummaryResult,
    AiFeature.DOSSIER_SUMMARY: DossierSummaryResult,
    AiFeature.HIGHLIGHT_EXPLANATION: HighlightExplanationResult,
    AiFeature.PRIORITY_SUGGESTION: PrioritySuggestionResult,
    AiFeature.ANOMALY_GROUPING: AnomalyGroupsResult,
}


# --- deterministic anomaly detection --------------------------------------------------------------

_MIN_AGE_DAYS = 14
_LONE_ITEM_AGE_DAYS = 30
_REPEATED_RETURNS = 3


@dataclass(frozen=True, slots=True)
class Anomaly:
    id: str
    kind: Literal["REPEATED_RETURN", "OLD_IN_QUEUE", "WORKFLOW_FAILING"]
    workflow: str
    detail: str
    dossier_id: int | None = None

    def payload(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "workflow": self.workflow, "detail": self.detail}


def detect_anomalies(
    items: Sequence[ItemView], failing_workflows: Mapping[str, str] | None = None
) -> list[Anomaly]:
    """Deterministic anomalies the model may then group and phrase.

    * a dossier that came back to the same queue three or more times;
    * a dossier waiting much longer than its queue's typical age (and at least two weeks)
      without being finished -- an *observation*, never an overdue alert: no SLA exists;
    * a workflow whose last poll did not complete.
    """
    anomalies: list[Anomaly] = []
    ages_by_workflow: dict[str, list[int]] = {}
    for item in items:
        ages_by_workflow.setdefault(item.workflow_key, []).append(item.queue_age_days)

    def baseline(item: ItemView) -> int:
        """The queue's typical age *without* this dossier; a lone dossier has no baseline."""
        others = list(ages_by_workflow[item.workflow_key])
        others.remove(item.queue_age_days)
        if not others:
            return _LONE_ITEM_AGE_DAYS // 2
        return sorted(others)[len(others) // 2]

    for item in items:
        if item.occurrence_number >= _REPEATED_RETURNS:
            anomalies.append(
                Anomaly(
                    id=f"return-{item.membership_id}",
                    kind="REPEATED_RETURN",
                    workflow=item.workflow_name,
                    detail=f"dossier {item.dossier_number}: {item.occurrence_number} apparitions dans la file",
                    dossier_id=item.dossier_id,
                )
            )
        typical = baseline(item)
        if item.queue_age_days >= max(_MIN_AGE_DAYS, 2 * typical) and item.work_status.value != "DONE":
            anomalies.append(
                Anomaly(
                    id=f"old-{item.membership_id}",
                    kind="OLD_IN_QUEUE",
                    workflow=item.workflow_name,
                    detail=(
                        f"dossier {item.dossier_number}: dans la file depuis {item.queue_age_days} jours "
                        f"(âge habituel de la file : {typical})"
                    ),
                    dossier_id=item.dossier_id,
                )
            )
    for workflow, status in sorted((failing_workflows or {}).items()):
        anomalies.append(
            Anomaly(
                id=f"failing-{workflow}",
                kind="WORKFLOW_FAILING",
                workflow=workflow,
                detail=f"dernière synchronisation : {status}",
            )
        )
    return anomalies


# --- contexts: the minimum stored facts each feature may send --------------------------------------


@dataclass(frozen=True, slots=True)
class AiContext:
    feature: AiFeature
    payload: Mapping[str, Any]
    """JSON-safe facts. Never contains names, plates, amounts, credentials or browser state."""

    def digest(self) -> str:
        blob = json.dumps(
            {"feature": self.feature.value, "prompt": PROMPT_VERSION, "payload": self.payload},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(blob.encode()).hexdigest()


class AIAdvisor(Protocol):
    """Port implemented by the optional Ollama adapter.

    Every method receives an :class:`AiContext` and returns the schema-validated
    result model of its feature, or raises an :class:`AiError`. Implementations
    must not send data anywhere except their configured local endpoint.
    """

    @property
    def model(self) -> str: ...

    async def daily_summary(self, context: AiContext) -> DailySummaryResult: ...

    async def dossier_summary(self, context: AiContext) -> DossierSummaryResult: ...

    async def explain_highlight(self, context: AiContext) -> HighlightExplanationResult: ...

    async def suggest_priority(self, context: AiContext) -> PrioritySuggestionResult: ...

    async def group_anomalies(self, context: AiContext) -> AnomalyGroupsResult: ...

    async def ping(self) -> bool: ...


# --- service ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdvisorOutcome:
    """The only thing callers ever see: a status, never an exception."""

    feature: AiFeature
    status: Literal["OK", "DISABLED", "UNAVAILABLE", "TIMEOUT", "INVALID_OUTPUT", "NO_DATA"]
    result: Mapping[str, Any] | None = None
    model: str | None = None
    generated_at: datetime | None = None
    cached: bool = False
    message: str | None = None
    disclaimer: str = DISCLAIMER
    facts: list[str] = field(default_factory=list)
    """Deterministic facts the answer is based on (shown next to it so it can be checked)."""


_MESSAGES = {
    "UNAVAILABLE": "L'assistant local est indisponible pour le moment.",
    "TIMEOUT": "L'assistant local n'a pas répondu à temps.",
    "INVALID_OUTPUT": "L'assistant local a renvoyé une réponse inexploitable.",
    "DISABLED": "L'assistant local n'est pas activé.",
}


class AdvisorService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        work_service: WorkService,
        advisor: AIAdvisor | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uow_factory = uow_factory
        self._work = work_service
        self._advisor = advisor
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self._advisor is not None

    @property
    def model(self) -> str | None:
        return self._advisor.model if self._advisor else None

    async def status(self) -> dict[str, Any]:
        healthy = None
        if self._advisor is not None:
            try:
                healthy = await self._advisor.ping()
            except Exception:  # noqa: BLE001 - health is informational only
                healthy = False
        return {"enabled": self.enabled, "model": self.model, "healthy": healthy}

    # --- features ------------------------------------------------------------------------------

    async def daily_summary(self, user_id: int) -> AdvisorOutcome:
        feature = AiFeature.DAILY_SUMMARY
        if self._advisor is None:
            return self._disabled(feature)
        dashboard = self._work.dashboard(user_id)
        payload = {
            "date": self._clock().date().isoformat(),
            "workflows": [
                {
                    "nom": w.name,
                    "categorie": w.category,
                    "classe": w.notification_class.value,
                    "regles_a_valider": w.needs_site_validation,
                    "actifs": w.active_count,
                    "nouveaux_non_lus": w.unread_new,
                    "modifies_non_lus": w.unread_changed,
                    "statuts": w.by_work_status,
                    "derniere_synchro": w.last_poll_status.value if w.last_poll_status else None,
                    "dernier_passage": (
                        {
                            "nouveaux": w.last_run.created,
                            "revenus": w.last_run.returned,
                            "modifies": w.last_run.changed,
                            "sortis": w.last_run.left,
                        }
                        if w.last_run
                        else None
                    ),
                }
                for w in dashboard.workflows
                if w.enabled
            ],
            "session": dashboard.session.state,
        }
        return await self._run(
            AiContext(feature, payload),
            "global",
            None,
            lambda a, c: a.daily_summary(c),
            facts=[f"{w['nom']}: {w['actifs']} actifs" for w in payload["workflows"]][:12],
        )

    async def dossier_summary(self, user_id: int, dossier_id: int) -> AdvisorOutcome:
        feature = AiFeature.DOSSIER_SUMMARY
        if self._advisor is None:
            return self._disabled(feature)
        view = self._work.dossier(user_id, dossier_id)
        payload = {
            "dossier": view.dossier.dossier_number,
            "procedure": view.dossier.procedure,
            "statut_portail": view.dossier.portal_status,
            "dates": [{"libelle": d.label, "valeur": d.value} for d in view.dossier.dates],
            "files": [
                {
                    "workflow": m.workflow_name,
                    "actif": m.active,
                    "statut_travail": m.work_status.value,
                    "apparitions": len(m.occurrences),
                    "date_principale": m.primary_date_raw or None,
                    "champs": {f.label: f.value for f in m.fields if f.value},
                }
                for m in view.memberships
            ],
            "evenements": [
                {
                    "type": e.kind,
                    "classe": e.notification_class.value,
                    "quand": e.detected_at.date().isoformat(),
                    "workflow": e.workflow_name,
                    "champs_modifies": e.changed_labels,
                }
                for e in view.events[:20]
            ],
            "notes": [{"quand": n.created_at.date().isoformat(), "texte": n.body} for n in view.notes[:10]],
        }
        return await self._run(
            AiContext(feature, payload),
            "dossier",
            dossier_id,
            lambda a, c: a.dossier_summary(c),
            facts=[f"{m['workflow']} ({m['statut_travail']})" for m in payload["files"]],
        )

    async def explain_highlight(self, user_id: int, occurrence_id: int) -> AdvisorOutcome:
        feature = AiFeature.HIGHLIGHT_EXPLANATION
        if self._advisor is None:
            return self._disabled(feature)
        item = self._work.item_for_occurrence(user_id, occurrence_id)
        if item is None:
            raise OccurrenceNotFoundError(occurrence_id)
        events = [e for e in self._work.dossier_events(item.dossier_id) if e.occurrence_id == occurrence_id]
        facts = deterministic_reasons(item, events)
        payload = {"faits": facts}
        return await self._run(
            AiContext(feature, payload),
            "occurrence",
            occurrence_id,
            lambda a, c: a.explain_highlight(c),
            facts=facts,
            validate=lambda result: _facts_subset(result, facts),
        )

    async def suggest_priority(self, user_id: int, membership_id: int) -> AdvisorOutcome:
        feature = AiFeature.PRIORITY_SUGGESTION
        if self._advisor is None:
            return self._disabled(feature)
        item = self._work.item_for_membership(user_id, membership_id)
        if item is None:
            raise MembershipNotFoundError(membership_id)
        payload = {
            "workflow": item.workflow_name,
            "classe": item.notification_class.value,
            "statut_travail": item.work_status.value,
            "jours_dans_la_file": item.queue_age_days,
            "apparitions": item.occurrence_number,
            "non_lu": item.unread_kind,
            "date_principale": item.primary_date_raw or None,
            "statut_portail": item.portal_status,
        }
        facts = [f"{k}: {v}" for k, v in payload.items() if v not in (None, "")]
        return await self._run(
            AiContext(feature, payload),
            "membership",
            membership_id,
            lambda a, c: a.suggest_priority(c),
            facts=facts,
        )

    async def anomalies(self, user_id: int) -> AdvisorOutcome:
        feature = AiFeature.ANOMALY_GROUPING
        if self._advisor is None:
            return self._disabled(feature)
        page = self._work.items(user_id, ItemQuery(page_size=100))
        items = list(page.items)
        if page.total > len(items):
            # Bounded on purpose: the assistant only groups what the first page shows.
            logger.info("anomaly grouping considers %d of %d items", len(items), page.total)
        health = self._work.workflows(user_id)
        failing = {
            w.name: w.last_poll_status.value
            for w in health
            if w.enabled and w.last_poll_status and w.last_poll_status.value != "COMPLETE"
        }
        found = detect_anomalies(items, failing)
        if not found:
            return AdvisorOutcome(
                feature=feature,
                status="NO_DATA",
                message="Aucune anomalie détectée.",
                result={"groups": []},
            )
        payload = {"anomalies": [a.payload() for a in found]}
        known = {a.id for a in found}
        return await self._run(
            AiContext(feature, payload),
            "global",
            None,
            lambda a, c: a.group_anomalies(c),
            facts=[a.detail for a in found][:15],
            validate=lambda result: _anomaly_ids_subset(result, known),
        )

    # --- machinery -------------------------------------------------------------------------------------

    def _disabled(self, feature: AiFeature) -> AdvisorOutcome:
        return AdvisorOutcome(feature=feature, status="DISABLED", message=_MESSAGES["DISABLED"])

    async def _run(
        self,
        context: AiContext,
        subject_type: str,
        subject_id: int | None,
        call: Callable[[AIAdvisor, AiContext], Any],
        *,
        facts: list[str],
        validate: Callable[[BaseModel], bool] | None = None,
    ) -> AdvisorOutcome:
        advisor = self._advisor
        assert advisor is not None
        digest = context.digest()
        feature = context.feature

        cached = self._cached(feature, subject_type, subject_id, digest)
        if cached is not None:
            return AdvisorOutcome(
                feature=feature,
                status="OK",
                result=cached.result,
                model=cached.model,
                generated_at=cached.started_at,
                cached=True,
                facts=facts,
            )

        started = time.perf_counter()
        started_at = self._clock()
        status = AiRunStatus.SUCCEEDED
        error: str | None = None
        result: BaseModel | None = None
        try:
            result = await call(advisor, context)
            if validate is not None and not validate(result):
                raise AiInvalidOutputError("la réponse cite des éléments absents du contexte")
        except AiTimeoutError as exc:
            status, error = AiRunStatus.TIMEOUT, str(exc)
        except (AiInvalidOutputError, ValidationError) as exc:
            status, error = AiRunStatus.INVALID_OUTPUT, str(exc)
        except AiError as exc:
            status, error = AiRunStatus.FAILED, str(exc)
        except Exception as exc:  # noqa: BLE001 - an adapter bug must never reach the caller
            logger.warning("AI advisor crashed", exc_info=True)
            status, error = AiRunStatus.FAILED, f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.perf_counter() - started) * 1000)

        payload = result.model_dump() if result is not None and status is AiRunStatus.SUCCEEDED else None
        self._audit(
            AiRun(
                id=None,
                feature=feature,
                subject_type=subject_type,
                subject_id=subject_id,
                model=advisor.model,
                prompt_version=PROMPT_VERSION,
                context_hash=digest,
                status=status,
                started_at=started_at,
                duration_ms=duration_ms,
                result=payload,
                error=error,
            )
        )
        if status is AiRunStatus.SUCCEEDED:
            return AdvisorOutcome(
                feature=feature,
                status="OK",
                result=payload,
                model=advisor.model,
                generated_at=started_at,
                facts=facts,
            )
        label = {
            AiRunStatus.TIMEOUT: "TIMEOUT",
            AiRunStatus.INVALID_OUTPUT: "INVALID_OUTPUT",
        }.get(status, "UNAVAILABLE")
        return AdvisorOutcome(
            feature=feature,
            status=label,  # type: ignore[arg-type]
            model=advisor.model,
            generated_at=started_at,
            message=_MESSAGES[label],
            facts=facts,
        )

    def _cached(
        self, feature: AiFeature, subject_type: str, subject_id: int | None, digest: str
    ) -> AiRun | None:
        try:
            with self._uow_factory() as uow:
                latest = uow.ai_runs.latest_successful(feature, subject_type, subject_id)
        except Exception:  # noqa: BLE001 - the audit table must never block the advisor
            logger.warning("AI cache lookup failed", exc_info=True)
            return None
        if latest is not None and latest.context_hash == digest and latest.result is not None:
            return latest
        return None

    def _audit(self, run: AiRun) -> None:
        try:
            with self._uow_factory() as uow:
                uow.ai_runs.add(run)
                uow.commit()
        except Exception:  # noqa: BLE001 - losing an audit row must not fail the request
            logger.warning("AI audit row could not be written", exc_info=True)


class AiJobHandler:
    """Outbox handler for ``ai.job.requested``: pre-computes highlight explanations.

    Never raises for an AI problem (the service already reports it as an outcome),
    and simply drains its messages when the assistant is off, so a disabled or
    broken model can never back up the outbox or affect alerts.
    """

    def __init__(self, advisor_service: AdvisorService) -> None:
        self._advisor = advisor_service

    async def __call__(self, message) -> None:
        if not self._advisor.enabled:
            return
        payload = message.payload
        occurrence_id = payload.get("occurrence_id")
        if payload.get("feature") != AiFeature.HIGHLIGHT_EXPLANATION.value or not isinstance(
            occurrence_id, int
        ):
            return
        try:
            await self._advisor.explain_highlight(0, occurrence_id)
        except OccurrenceNotFoundError:
            return


def deterministic_reasons(item: ItemView, events: Sequence[Any]) -> list[str]:
    """Why an item is highlighted, as plain facts (no model involved)."""
    facts = [
        f"file : {item.workflow_name} ({item.notification_class.value})",
        f"apparition n°{item.occurrence_number} ({item.occurrence_origin.value})",
        f"dans la file depuis {item.queue_age_days} jour(s)",
        f"statut de travail : {item.work_status.value}",
    ]
    labels = {
        "WORKFLOW_ITEM_NEW": "nouvel arrivé dans la file",
        "WORKFLOW_ITEM_RETURNED": "revenu dans la file après une sortie",
        "WORKFLOW_ITEM_CHANGED": "un champ important a changé",
        "WORKFLOW_ITEM_COMPLETED": "apparu dans une file de suivi (information)",
        "WORKFLOW_ITEM_TRANSITION": "apparu dans l'étape suivante",
        "WORKFLOW_ITEM_LEFT": "sorti de la file",
    }
    for event in events:
        text = labels.get(event.kind, event.kind)
        if event.changed_labels:
            text += " : " + ", ".join(event.changed_labels)
        facts.append(text)
    if item.unread_kind:
        facts.append("alerte non lue pour cet employé")
    return facts


def _facts_subset(result: BaseModel, facts: list[str]) -> bool:
    used = getattr(result, "facts_used", [])
    return all(fact in facts for fact in used)


def _anomaly_ids_subset(result: BaseModel, known: set[str]) -> bool:
    groups = getattr(result, "groups", [])
    return all(set(g.anomaly_ids) <= known for g in groups)


__all__ = [
    "DISCLAIMER",
    "AIAdvisor",
    "AdvisorOutcome",
    "AiJobHandler",
    "AdvisorService",
    "AiContext",
    "AiError",
    "AiInvalidOutputError",
    "AiTimeoutError",
    "AiUnavailableError",
    "Anomaly",
    "detect_anomalies",
    "deterministic_reasons",
]
