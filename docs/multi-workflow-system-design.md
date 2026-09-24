# RMA Portal multi-workflow system design

## Purpose

RMA Portal is a read-only operational inbox over OmegaFlow. It does not
replace OmegaFlow and it never performs an agreement, validation, upload or
other business write. It periodically reads the queues employees already use,
detects arrivals and changes, presents the useful fields in one place, and
lets the agency coordinate work with local statuses and notes.

The current Garage agree feature remains the first production workflow. This
foundation makes additional queues possible without encoding business rules
that employees have not confirmed yet.

## Technology decision

Keep the existing modular monolith and stack:

- Python 3.14 managed by `uv`;
- FastAPI, server-rendered Jinja2 and locally bundled HTMX;
- SQLAlchemy 2 and Alembic;
- SQLite in WAL mode;
- Camoufox/Playwright for the read-only OmegaFlow adapter;
- Uvicorn with one worker;
- Ruff and pytest.

This remains the best fit for one Windows PC serving the agency LAN. A JS
framework, Node.js, Redis, Celery, Docker and microservices would add deployment
and operations work without solving a current product requirement.

## Architectural boundaries

Dependencies continue to point inward:

1. `domain` owns workflow identity, dossier identity, queue-membership state
   and pure lifecycle rules.
2. `application` owns synchronization and employee-facing use cases and exposes
   repository/browser ports.
3. `infrastructure` implements the ports with Camoufox and SQLite.
4. `web` renders French pages and HTMX fragments.
5. `bootstrap` is the only composition root.

OmegaFlow selectors, routes and view identifiers belong to technical workflow
definitions. Deadlines, completion rules, notification policy and the business
meaning of a date belong to business policy and must only be enabled after an
employee confirms them.

## Core model

### Shared dossier

`dossiers` remains the canonical copy of identity and common details for an
OmegaFlow record. Its natural identity is `(portal_account_id, record_id)`.
Common details are read once and reused even when the dossier appears in
several queues.

### Workflow definition

`workflows` identifies one observable OmegaFlow queue or stage. It stores:

- stable application key;
- French display name and category;
- OmegaFlow route and Knack view id;
- enabled/order controls;
- whether its employee rules are confirmed;
- an independent baseline and latest polling outcome.

The first seeded definition is `agreement_garage`. Candidate definitions from
the ScriptScrap audit are not enabled merely because their selectors were
captured. They are added after the agency confirms that the queue is useful.

### Workflow membership

`workflow_memberships` represents a dossier's occurrence in a workflow. Its
identity is `(workflow_id, dossier_id)`. It owns:

- first and last detection time in that workflow;
- active/missing-poll state for that workflow;
- occurrence number for departures and reappearances;
- a fingerprint and workflow-specific captured fields for change detection;
- the time at which a material change was last detected.

This is the essential multi-workflow boundary. Dossier activity must no longer
be inferred globally once synchronization moves to the new model.

For example, the Expertise Collegiale path is modeled as related memberships:

```text
One dossier
  -> 1er expert - dossiers en cours
  -> 1er expert - a envoyer au 2eme expert
  -> 2eme expert - dossiers recus avec accord
  -> 2eme expert - dossiers completes
```

The record stays one dossier while each stage gets its own arrival, baseline,
absence and notification lifecycle.

## Future synchronization flow

The multi-workflow synchronizer will open one authenticated browser context per
poll cycle and read every enabled workflow sequentially. Reusing the browser
context avoids paying Camoufox startup cost for every queue.

For each workflow it will:

1. navigate to the workflow route and verify the expected view;
2. apply only the captured read filter, if one exists;
3. read every pagination page and deduplicate by OmegaFlow record id;
4. reconcile membership state using that workflow's independent baseline;
5. create or update the shared dossier;
6. store workflow-specific fields and detect material changes;
7. read shared detail pages only when required, deduplicated across workflows;
8. record a per-workflow result without allowing one failed queue to erase or
   invalidate another successful queue.

A partial or failed read never marks memberships absent. Two consecutive
complete omissions remain the default technical deactivation rule.

## Business policy seam

The following policies deliberately remain unimplemented until the employee
meeting:

- which appearance, status, document or date creates actionable work;
- which date controls ordering and lateness;
- the allowed duration and whether it uses calendar or working days;
- the exact completion condition;
- whether a reappearance creates a new alert;
- whether acknowledgement is personal, team-wide or tied to an explicit work
  action;
- which field changes deserve a visible alert.

These decisions will be attached to a workflow rather than hard-coded into the
browser reader. A captured queue can therefore exist in the catalog with
`UNCONFIRMED` rules without generating misleading alerts.

## Employee UI direction

The eventual UI has three levels:

1. A dashboard summarizing new, changed, overdue and in-progress work by
   workflow.
2. A workflow queue page with columns and filters appropriate to that stage.
3. One dossier page combining common details, current workflow memberships,
   change history, shared notes and links back to the exact OmegaFlow pages.

The Expertise Collegiale pages should appear as stages under one category,
while still being synchronized independently.

## Incremental delivery

### Foundation (this change)

- document the target design;
- introduce workflow and workflow-membership domain entities and ports;
- add additive database tables;
- seed the working Garage agree workflow;
- backfill a Garage agree membership for existing dossiers;
- keep the production reader, synchronization and UI on their current path.

### After employee confirmation

1. Register only approved workflow definitions and their captured parsers.
2. Change synchronization to reconcile memberships per workflow in one browser
   session.
3. Move work state and notifications from dossier scope to workflow-membership
   scope, preserving current Garage agree data.
4. Add workflow navigation and workflow-aware dossier pages.
5. Implement confirmed deadlines, change alerts and acknowledgement policy.

This staged migration preserves the working portal and avoids guessing rules
from browser traffic.
