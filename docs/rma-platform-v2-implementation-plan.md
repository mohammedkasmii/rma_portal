# RMA Platform V2 implementation plan

## Decision

The captures are sufficient to implement a multi-workflow operational inbox without waiting for
employee interviews. The reliable business event common to every captured pending page is queue
membership itself. Therefore V2 uses first appearance in a queue as the notification trigger. A
captured date is displayed and used for sorting when its meaning is known, but no date is treated
as a deadline until the agency confirms an SLA.

Workflow rules use three states: `UNCONFIRMED` (catalogued but inactive), `CAPTURE_DERIVED`
(enabled with the conservative membership policy in this document), and `CONFIRMED` (validated by
the agency). Tomorrow's live validation can promote or correct a rule without changing the event
engine.

This policy is conservative: it detects work that employees currently discover by opening queue
pages, while avoiding invented deadline, completion, or escalation rules.

## Notification policy

### Event types

1. `WORKFLOW_ITEM_NEW`: a stable OmegaFlow record appears after that workflow's first complete
   baseline. It creates an unread notification for every active employee that existed at detection
   time.
2. `WORKFLOW_ITEM_RETURNED`: a membership was inactive after two complete omissions and later
   reappears. It starts a new occurrence and creates a new unread notification.
3. `WORKFLOW_ITEM_CHANGED`: a material field changes while the membership is active. It is shown
   in the activity feed. It creates an unread badge only for workflows configured as `ACTION` and
   only when a key status, key date, or action-availability field changed.
4. `WORKFLOW_ITEM_COMPLETED`: a dossier first appears in a captured completion/reference queue.
   It is an informational activity and does not increase the actionable-new counter.
5. `SESSION_AUTH_REQUIRED`, `WORKFLOW_POLL_PARTIAL`, and `WORKFLOW_POLL_FAILED`: operational alerts
   shown to administrators. They never modify dossier membership.

### Invariants

- The first complete poll of each workflow establishes its own baseline and creates no employee
  notifications.
- Partial, failed, or authentication-required polls never increment absence counters.
- Two consecutive complete omissions deactivate only that workflow membership.
- Opening or acknowledging an occurrence marks it read only for the acting employee.
- A new employee starts with all historical occurrences read.
- Shared work status belongs to the workflow membership. The same dossier can be `DONE` in one
  queue and `TO_DO` in another.
- Notes remain shared at dossier level and may optionally reference a workflow membership.
- Downstream appearance never silently closes upstream work. It adds a transition/activity event;
  employees retain control of shared work status.
- Queue age may be displayed. No overdue notification is emitted without a confirmed SLA.
- No OmegaFlow write request is allowed.

### Notification classes

- `ACTION`: new, returned, and material changes can create employee unread alerts.
- `INFORMATIONAL`: arrivals and changes appear in history and summaries without an actionable badge.
- `SILENT`: synchronized for search/detail context only.

## Workflow catalog

All definitions below come from the four clean ScriptScrap captures dated 23 September 2026.
Empty captured queues remain valid: they establish the DOM and response contract and can begin
producing memberships when OmegaFlow later contains records.

| Key | French label | Route | View | Class | Primary display/sort evidence |
|---|---|---|---|---|---|
| `agreement_garage` | Instance d'accord - Garage agree | `#dossiers-en-instance-accord/` | `view_1874` | ACTION | `field_114` Date envoi devis garage |
| `agreement_normal` | Instance d'accord - Procedure normale | same | `view_1874` + `field_219=5eebcdf9c791c6001505bd70` | ACTION | quote/detail dates, then detection time |
| `agreement_appreciation` | Instance d'accord - Appreciation | same | `view_1874` + `field_219=6039196ec1fce4001c27ab66` | ACTION | quote/detail dates, then detection time |
| `agreement_collegial_cid` | Instance d'accord - Expertise Collegiale CID | same | `view_1874` + `field_219=5f3e5a57696b8700158842e6` | ACTION | quote/detail dates, then detection time |
| `agreement_hifad` | Instance d'accord - Hifad | same | `view_1874` + `field_219=5ee405e42e70690015f4dcb0` | ACTION | quote/detail dates, then detection time |
| `hifad_search` | Dossiers Hifad | `#recherche-dossiers-hifad/` | `view_1867` | ACTION | captured `field_107` raw date, then detection time |
| `estimate_pending` | Dossiers en instance de devis | `#dossiers-en-instance-devis-photos/` | `view_655` | ACTION | `field_107` mission date |
| `photos_pending` | Dossiers en instance Photos | `#dossiers-en-instance-photos/` | `view_787` | ACTION | `field_652` Date envoi, then photo dates |
| `fft_garage_pending` | Dossiers en instance de FFT - Garage agree | `#dossiers-en-instance-fft2/` | `view_90` | ACTION | `field_133` Date envoi facture, then `field_132` |
| `second_agreement_pending` | Dossiers en instance 2eme Accord | captured route for `scene_116` | `view_149` | ACTION | `field_157`, then `field_158` |
| `report_pending` | Dossiers en instance rapport | `#dossiers-en-instance-rapport/` | `view_1857` | ACTION | detection time; expose report/status fields |
| `collegial_first_expert` | Expertise Collegiale - 1er expert | `#dossiers-expertise-collegiale/` | `view_1186` | ACTION | `field_852`, then `field_853`, then detection |
| `collegial_first_agreed` | EXPC 1er expert avec accord | `#dossiers-expcoll-exp1-accord/` | `view_2051` | ACTION | agreement/report state, then detection time |
| `collegial_second_agreed` | EXPC 2eme expert - avec accord | `#dossiers-expcol-exp2-avec-accord` | `view_2054` | ACTION | `field_869` Date envoi, then detection time |
| `collegial_second_completed` | EXPC 2eme expert - completes | `#dossiers-expc-exp2-complts` | `view_2059` | INFORMATIONAL | detection time |
| `agreement_validated` | Dossiers avec accord / valides | `#dossiers-avec-accord2` | `view_82` | INFORMATIONAL | detection time |
| `reformed` | Dossiers Reformes | `#dossiers-rforme` | `view_829` | ACTION | detection time; expose nature/report fields |
| `deficiency_cancel_appreciation` | Carence - annulation/appreciation | `#dossiers-carence-/` | `view_1378` | ACTION | mission/photo/agreement dates |
| `deficiency_intermediary` | Carence - information intermediaire | `#dossiers-carence-/` | `view_1380` | ACTION | `field_918`, then other captured dates |
| `reserves_pending` | Reserves - en cours | `#dossiers-avec-rserve` | `view_1396` | ACTION | detection time; expose doubt status |
| `reserves_company_instruction` | Reserves - instruction compagnie | `#dossiers-avec-rserve` | `view_1421` | ACTION | detection time; expose doubt status |

`report_pending` may later be split by procedure when live data proves employees need distinct
queues. The reader already supports a filter definition, so this requires catalog data rather than
a new reader.

## Target architecture

V2 is a modular monolith deployed as several processes from one repository and one version:

```text
Employee browser
  -> reverse proxy
     -> React/TypeScript frontend
     -> FastAPI JSON API
          -> PostgreSQL

Scheduler/worker
  -> one Camoufox context
  -> enabled workflow readers, sequentially
  -> PostgreSQL reconciliation and outbox events

Administrator noVNC
  -> visible Camoufox session configuration
  -> persistent browser profile + captured state

Optional AI worker
  -> local Ollama HTTP API
  -> summaries/suggestions derived from stored deterministic facts
```

### Stack

- React, TypeScript and Vite.
- React Router, TanStack Query and TanStack Table.
- FastAPI application and domain/application/infrastructure boundaries already present.
- PostgreSQL with SQLAlchemy 2 and Alembic.
- One scheduler/worker process using a PostgreSQL advisory lock; no Redis or Celery in V2.
- Camoufox/Xvfb/noVNC using the validated Ubuntu container pattern.
- Docker Compose for the API, worker, frontend/reverse proxy, PostgreSQL, and browser desktop.
- Ollama remains an external agency service configured by URL and model name.

The API and worker import the same application layer. Browser selectors remain in infrastructure.
React never knows OmegaFlow selectors or credentials.

## Data model changes

The existing `workflows` and `workflow_memberships` tables are retained and completed.

- Add workflow notification class, catalog version, filter configuration, primary-date key, and
  last poll status.
- Add an immutable `workflow_occurrences` row for each membership occurrence. A reappearance
  creates a new occurrence instead of overwriting notification history.
- Notifications reference an occurrence and dossier, with kinds listed above.
- Move shared work state from `dossier_work` to `workflow_work`, keyed by membership. Backfill the
  current Garage agree work row to its Garage membership.
- Keep dossier notes and add nullable membership context.
- Add `workflow_poll_runs` under one parent `sync_runs` record so one failed queue cannot invalidate
  successful queues.
- Add immutable `workflow_events` containing event kind, changed field names, timestamps, and safe
  before/after fingerprints. Do not duplicate raw customer payloads.
- Add an outbox table so notifications and AI jobs are committed with reconciliation and processed
  reliably afterward.
- Add optional `ai_runs` with model, prompt version, context hash, status, timings and structured
  result. Do not store credentials or browser state.

All migrations are additive first. Existing Garage agree data remains visible throughout cutover.

## Reader and synchronization design

Define an immutable `WorkflowDefinition` registry in infrastructure:

- key, route, scene/view identifiers and expected root;
- optional filter field/operator/value;
- row selector, pagination selectors and stable record-id extractor;
- common fields and workflow-specific field map;
- detail contract and material-field set;
- notification class.

One `PortalSession` is opened per synchronization cycle. Each enabled definition is read
sequentially with independent error handling. Pagination must settle on a changed row-id set and
verify that the last page has no enabled next control. Detail reads are deduplicated by record id
across workflows. Reconciliation and poll results commit per workflow.

The first rollout should enable all ACTION workflows but baseline each independently. Completion
and reference workflows are enabled as INFORMATIONAL. Administrators can disable a broken workflow
without stopping the others.

## Employee frontend

The React UI is in French and responsive:

1. Dashboard: session health, last sync, actionable-new count, failures, workload by workflow and
   status, plus an activity panel.
2. Work inbox: grouped workflow navigation, cross-workflow search, unread/status/procedure filters,
   server pagination, and queue-specific columns.
3. Workflow page: baseline/poll health, new/returned/changed badges, queue age, primary captured
   date, and shared work status.
4. Dossier page: common details, all active and historical memberships, event timeline, shared
   notes, per-membership status, and exact OmegaFlow links.
5. Admin: users, enabled workflows, session connection, poll health, and AI configuration/health.

Opening a dossier acknowledges only the selected occurrence for the current employee. Status and
notes remain shared.

## AI boundary

AI is optional and never participates in capture correctness or notification creation.

Useful V2 features:

- a daily French workload summary by workflow;
- a dossier summary from captured structured fields and agency notes;
- explanation of why an item is highlighted, using deterministic event facts;
- suggested priority and suggested next action, clearly labelled as suggestions;
- anomaly grouping, such as repeated returns or unusually old queue age.

Define an `AIAdvisor` application port and an Ollama infrastructure adapter. Send only the minimum
stored fields needed for the requested feature. Require structured JSON output validated against a
schema. Record model/version and context hash. A timeout, invalid output, or unavailable Ollama
must leave the portal fully functional. AI cannot acknowledge notifications, change work status,
write notes, or perform OmegaFlow actions.

## Delivery sequence

1. Domain and database cutover: occurrences, membership work, workflow notifications, events,
   sync runs and safe Garage data backfill.
2. Workflow registry and synthetic parsers for every captured view.
3. One-session multi-workflow synchronizer with independent baselines and failures.
4. FastAPI JSON endpoints and compatibility endpoints for the existing Garage UI.
5. React frontend and French workflow inbox.
6. Production Docker Compose using PostgreSQL and the validated browser/noVNC image.
7. Ollama adapter and non-blocking summary/suggestion features.
8. Synthetic acceptance suite, then live agency validation of selectors, non-empty second-expert
   queues, session persistence, performance, and employee wording.

## Acceptance rules

- No existing Garage agree dossier, note, read state, or work status is lost.
- Each workflow baselines independently with zero false new alerts.
- The same dossier can be active in multiple workflows without identity collision.
- New and returned occurrences are independently unread per employee.
- Partial/failed reads preserve prior membership data.
- All captured pages paginate dynamically.
- Empty captured workflows complete successfully with zero rows.
- One workflow failure does not stop the remaining workflow reads.
- The UI identifies the workflow that caused every alert.
- AI disabled/unavailable produces no functional degradation.
- Network inspection confirms that automated OmegaFlow traffic is read-only.
