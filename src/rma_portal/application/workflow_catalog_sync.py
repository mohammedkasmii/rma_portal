"""Keeps the ``workflows`` table aligned with the code-defined catalog.

The catalog owns a workflow's technical definition (route, view, filter,
primary date). The database owns what administrators decide: whether the
queue is enabled, its rules status and its notification class. Refreshing
the catalog therefore never re-enables a queue an administrator disabled,
and never re-classifies one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rma_portal.application.ports import UnitOfWorkFactory, WorkflowCatalog
from rma_portal.domain.models import DuplicateWorkflowError, Workflow
from rma_portal.domain.workflow_definition import WorkflowDefinition

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CatalogSyncResult:
    created: int
    refreshed: int


def _to_workflow(account_id: int, definition: WorkflowDefinition, workflow_id: int | None) -> Workflow:
    filter_spec = definition.filter
    return Workflow(
        id=workflow_id,
        portal_account_id=account_id,
        key=definition.key,
        name=definition.name,
        category=definition.category,
        route=definition.route,
        view_id=definition.view_id,
        enabled=True,
        sort_order=definition.sort_order,
        rules_status=definition.rules_status,
        baseline_completed_at=None,
        last_poll_at=None,
        last_success_at=None,
        last_error=None,
        notification_class=definition.notification_class,
        catalog_version=definition.catalog_version,
        filter_field=filter_spec.field_class if filter_spec else None,
        filter_operator=filter_spec.operator if filter_spec else None,
        filter_value=filter_spec.value if filter_spec else None,
        filter_label=filter_spec.label if filter_spec else None,
        primary_date_key=definition.primary_date[0] if definition.primary_date else None,
    )


class WorkflowCatalogSync:
    def __init__(self, uow_factory: UnitOfWorkFactory, catalog: WorkflowCatalog) -> None:
        self._uow_factory = uow_factory
        self._catalog = catalog

    def sync(self) -> CatalogSyncResult:
        with self._uow_factory() as uow:
            account = uow.portal_accounts.get_default()
            if account is None or account.id is None:
                return CatalogSyncResult(created=0, refreshed=0)
            account_id = account.id

        created = refreshed = 0
        for definition in self._catalog.definitions():
            # One transaction per workflow so a concurrent process that inserted the same
            # key first (API and worker start together) only costs that one insert.
            try:
                with self._uow_factory() as uow:
                    existing = uow.workflows.get_by_key(account_id, definition.key)
                    if existing is None:
                        uow.workflows.create(_to_workflow(account_id, definition, None))
                        created += 1
                    else:
                        uow.workflows.update_definition(
                            _to_workflow(account_id, definition, existing.id)
                        )
                        refreshed += 1
                    uow.commit()
            except DuplicateWorkflowError:
                logger.info("workflow %s was created concurrently", definition.key)
        return CatalogSyncResult(created=created, refreshed=refreshed)
