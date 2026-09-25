"""Optional local-AI advisory endpoints.

Every route answers ``200`` with an outcome whose ``status`` says whether the
assistant produced something: a disabled, unreachable, slow or misbehaving model
is reported as data, never as an error, and nothing here can acknowledge an
alert, change a work status, write a note or act on OmegaFlow.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from rma_portal.application.ai import AdvisorOutcome, AdvisorService
from rma_portal.application.dossier_service import DossierNotFoundError
from rma_portal.application.work_service import MembershipNotFoundError, OccurrenceNotFoundError
from rma_portal.bootstrap import Application
from rma_portal.domain.enums import AiFeature
from rma_portal.domain.models import User
from rma_portal.web.api.deps import api_user, same_origin
from rma_portal.web.deps import get_application

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["assistant"])
dossier_router = APIRouter(tags=["assistant"])


class AdvisorStatusOut(BaseModel):
    enabled: bool
    model: str | None
    healthy: bool | None


class AdvisorOutcomeOut(BaseModel):
    feature: AiFeature
    status: str
    result: dict[str, Any] | None = None
    model: str | None = None
    generated_at: datetime | None = None
    cached: bool = False
    message: str | None = None
    disclaimer: str
    facts: list[str] = []


def get_advisor(app: Application = Depends(get_application)) -> AdvisorService:
    return app.advisor_service


def _out(outcome: AdvisorOutcome) -> AdvisorOutcomeOut:
    return AdvisorOutcomeOut(
        feature=outcome.feature,
        status=outcome.status,
        result=dict(outcome.result) if outcome.result is not None else None,
        model=outcome.model,
        generated_at=outcome.generated_at,
        cached=outcome.cached,
        message=outcome.message,
        disclaimer=outcome.disclaimer,
        facts=outcome.facts,
    )


async def _safely(feature: AiFeature, call) -> AdvisorOutcomeOut:
    """An assistant failure of any kind is an outcome, never an HTTP error."""
    try:
        return _out(await call())
    except (DossierNotFoundError, OccurrenceNotFoundError, MembershipNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Élément introuvable.") from None
    except Exception:  # noqa: BLE001 - the portal must stay fully usable without the assistant
        logger.warning("assistant request failed", exc_info=True)
        return AdvisorOutcomeOut(
            feature=feature,
            status="UNAVAILABLE",
            message="L'assistant local est indisponible pour le moment.",
            disclaimer=AdvisorOutcome(feature=feature, status="UNAVAILABLE").disclaimer,
        )


@router.get("/status", response_model=AdvisorStatusOut)
async def advisor_status(
    user: User = Depends(api_user), advisor: AdvisorService = Depends(get_advisor)
) -> AdvisorStatusOut:
    del user
    try:
        return AdvisorStatusOut(**await advisor.status())
    except Exception:  # noqa: BLE001
        return AdvisorStatusOut(enabled=advisor.enabled, model=advisor.model, healthy=False)


@router.post("/daily-summary", response_model=AdvisorOutcomeOut, dependencies=[same_origin])
async def daily_summary(
    user: User = Depends(api_user), advisor: AdvisorService = Depends(get_advisor)
) -> AdvisorOutcomeOut:
    return await _safely(AiFeature.DAILY_SUMMARY, lambda: advisor.daily_summary(user.id))


@router.post("/anomalies", response_model=AdvisorOutcomeOut, dependencies=[same_origin])
async def anomalies(
    user: User = Depends(api_user), advisor: AdvisorService = Depends(get_advisor)
) -> AdvisorOutcomeOut:
    return await _safely(AiFeature.ANOMALY_GROUPING, lambda: advisor.anomalies(user.id))


@dossier_router.post(
    "/dossiers/{dossier_id}/ai/summary", response_model=AdvisorOutcomeOut, dependencies=[same_origin]
)
async def dossier_summary(
    dossier_id: int, user: User = Depends(api_user), advisor: AdvisorService = Depends(get_advisor)
) -> AdvisorOutcomeOut:
    return await _safely(
        AiFeature.DOSSIER_SUMMARY, lambda: advisor.dossier_summary(user.id, dossier_id)
    )


@dossier_router.post(
    "/occurrences/{occurrence_id}/ai/explain",
    response_model=AdvisorOutcomeOut,
    dependencies=[same_origin],
)
async def explain_highlight(
    occurrence_id: int, user: User = Depends(api_user), advisor: AdvisorService = Depends(get_advisor)
) -> AdvisorOutcomeOut:
    return await _safely(
        AiFeature.HIGHLIGHT_EXPLANATION, lambda: advisor.explain_highlight(user.id, occurrence_id)
    )


@dossier_router.post(
    "/memberships/{membership_id}/ai/priority",
    response_model=AdvisorOutcomeOut,
    dependencies=[same_origin],
)
async def suggest_priority(
    membership_id: int, user: User = Depends(api_user), advisor: AdvisorService = Depends(get_advisor)
) -> AdvisorOutcomeOut:
    return await _safely(
        AiFeature.PRIORITY_SUGGESTION, lambda: advisor.suggest_priority(user.id, membership_id)
    )

