"""Shared helpers for tests that need seeded workflows, dossiers and clocks."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from rma_portal.application.workflow_catalog_sync import WorkflowCatalogSync
from rma_portal.application.workflow_sync import SyncWorkflows
from rma_portal.domain.enums import (
    NotificationClass,
    OccurrenceOrigin,
    WorkflowRulesStatus,
)
from rma_portal.infrastructure.portal.workflow_catalog import default_catalog

CATALOG = default_catalog()


class StepClock:
    """A controllable UTC clock: every call to :meth:`advance` moves it forward."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 9, 25, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


class FakeCycleLock:
    """A cross-process lock double that can be held by "another process"."""

    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.attempts = 0

    @contextmanager
    def try_acquire(self):
        self.attempts += 1
        yield self.acquired


def seed_workflows(uow_factory, only: Iterable[str] | None = None, **classes: NotificationClass):
    """Create every catalogued workflow; keep only ``only`` enabled when given.

    ``classes`` overrides the notification class per workflow key, exactly like an
    administrator would.
    """
    WorkflowCatalogSync(uow_factory, CATALOG).sync()
    keep = set(only) if only is not None else None
    with uow_factory() as uow:
        account = uow.portal_accounts.get_default()
        for workflow in uow.workflows.list_for_account(account.id):
            enabled = keep is None or workflow.key in keep
            uow.workflows.update_admin_config(
                workflow.id,
                enabled=enabled,
                notification_class=classes.get(workflow.key),
            )
        uow.commit()


def make_sync(factory, uow_factory, **kwargs) -> SyncWorkflows:
    return SyncWorkflows(factory, uow_factory, CATALOG, **kwargs)


def set_rules(uow_factory, key: str, rules: WorkflowRulesStatus) -> None:
    with uow_factory() as uow:
        account = uow.portal_accounts.get_default()
        workflow = uow.workflows.get_by_key(account.id, key)
        uow.workflows.update_admin_config(workflow.id, rules_status=rules)
        uow.commit()


def set_enabled(uow_factory, key: str, enabled: bool) -> None:
    with uow_factory() as uow:
        account = uow.portal_accounts.get_default()
        workflow = uow.workflows.get_by_key(account.id, key)
        uow.workflows.update_admin_config(workflow.id, enabled=enabled)
        uow.commit()


def seed_member(
    uow_factory,
    account_id: int,
    workflow_key: str,
    record_id: str,
    *,
    active: bool = True,
    origin: OccurrenceOrigin = OccurrenceOrigin.BASELINE,
    now: datetime | None = None,
    **fields: str,
) -> tuple[int, int]:
    """Insert a dossier + membership + occurrence + work row directly.

    Returns ``(dossier_id, membership_id)``. Used where a test needs pre-existing state
    without running a synchronization first.
    """
    now = now or datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    common = {
        "dossier_number": f"D-{record_id}",
        "insured_name": "Assuré Test",
        "portal_status": "En cours",
        **fields,
    }
    with uow_factory() as uow:
        workflow = uow.workflows.get_by_key(account_id, workflow_key)
        dossier, _, _ = uow.dossiers.upsert_from_workflow_row(
            account_id, record_id, f"#q/view-dossier-details/{record_id}/", common, now
        )
        membership = uow.workflow_memberships.create(
            workflow_id=workflow.id,
            dossier_id=dossier.id,
            seen_at=now,
            captured_fields=common,
            fingerprint="seed",
        )
        uow.workflow_work.create_initial(membership.id, now)
        uow.workflow_occurrences.create(
            membership_id=membership.id,
            workflow_id=workflow.id,
            dossier_id=dossier.id,
            occurrence_number=1,
            origin=origin,
            detected_at=now,
        )
        if not active:
            uow.workflow_memberships.deactivate(membership.id)
            uow.dossiers.set_active(dossier.id, False)
        uow.commit()
        return dossier.id, membership.id
