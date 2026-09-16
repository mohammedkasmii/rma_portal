# Architecture

RMA Portal is a SOLID modular monolith: the employee UI, the 5-minute
scheduler, the read-only OmegaFlow adapter and the SQLite persistence layer
all run in one Python process (Uvicorn, one worker). This keeps
installation on a single agency PC simple while keeping the boundaries
between layers explicit and independently testable.

## Layers (dependencies point inward)

1. **`domain`** — immutable business entities (`domain/models.py`), enums
   (`domain/enums.py`) and the pure synchronization policy
   (`domain/sync_rules.py`). No FastAPI, SQLAlchemy, Camoufox or filesystem
   imports are allowed here; `sync_rules.reconcile()` is a plain function
   from "what was seen this poll" + "what we knew before" to "what changes",
   fully testable without a database or a browser.
2. **`application`** — use cases and `Protocol` interfaces
   (`application/ports.py`, `application/dto.py`): `SyncAgreementQueue`
   (the sync lifecycle), `AccountService` (local users), `DossierService`
   (dashboard, acknowledgement, work status, notes). These depend only on
   protocols, never on SQLAlchemy or Camoufox directly.
3. **`infrastructure`** — concrete implementations: SQLAlchemy repositories
   and engine setup (`infrastructure/db`), Argon2 hashing and session
   cookies (`infrastructure/security`), the Camoufox OmegaFlow reader and
   HTML parser (`infrastructure/portal`), and the background poller
   (`infrastructure/scheduler`).
4. **`web`** — FastAPI routes, Jinja2 templates and HTMX fragments
   (`web/routes`, `web/templates`). Routes call into `application` services
   only; they never touch SQLAlchemy or Camoufox directly.
5. **`bootstrap`** — the sole composition root (`bootstrap.py`). It is the
   only module that constructs SQLAlchemy engines, the Camoufox reader
   factory and wires them into the use cases above.

This means a future direct-API reader (see `docs/omegaflow-contract.md`'s
"future API candidates") or a second RMA queue can be added by writing a new
`PortalReader`/`PortalReaderFactory` pair and registering it in
`bootstrap.py` — nothing in `SyncAgreementQueue`, `domain.sync_rules` or the
web layer needs to change.

## Read-only OmegaFlow adapter

`CamoufoxPortalReader` (in `infrastructure/portal/camoufox_reader.py`) may
navigate, read (`GET`) and complete the required session/filter traffic. It
installs a Playwright network route (`_read_only_route`) that blocks
`PUT`/`PATCH`/`DELETE` to `omegaflow.ma`/`knack.com` and `POST` requests
whose path contains `/records`, and it never clicks a submit, validation,
agreement or upload control. See section 7 of the original product spec and
`docs/omegaflow-contract.md` for the exact contract.

Authentication is established out-of-band: `Configurer_Session_RMA.bat`
opens a **visible** persistent Camoufox profile so an administrator can log
in and pass session validation by hand. The application never reads,
requests or stores the OmegaFlow password — only the resulting browser
profile (cookies/local storage inside Firefox's own profile directory)
persists, under `%LOCALAPPDATA%\RMAPortal\browser-profile`.

Both the poller and the session-configuration script acquire the same
cross-process file lock (`infrastructure/portal/profile_lock.py`) before
touching that profile directory, so a scheduled poll safely skips (instead
of racing or crashing) while an administrator is configuring the session,
and a manual refresh can never run concurrently with the scheduled poll
(`SyncAgreementQueue` also serializes itself in-process — see below).

## Synchronization lifecycle

`SyncAgreementQueue.execute()` (in `application/sync_service.py`) is the
only entry point for a poll, used identically by the 5-minute scheduler and
the manual "Actualiser maintenant" button. An `asyncio.Lock` plus an
in-flight `Future` ensure a second concurrent call is coalesced onto the
result of the poll already running, rather than starting a duplicate one.

One poll:

1. Opens the portal reader (may raise `BrowserProfileLockedError`, handled
   as a safe skip with no database writes at all).
2. Reads the full "Garage agréé" queue, paginating until Knack's own page
   count is exhausted, deduplicating rows by their stable `tr[id]`.
3. Loads the previously known dossier state for the account and calls the
   pure `domain.sync_rules.reconcile()` to decide: which record IDs are
   brand new, which are known-active (just refresh), which are known but
   *inactive* and have reappeared (a new notification "occurrence"), and —
   only for a `COMPLETE` poll — which known-active dossiers were absent this
   time (incrementing `missing_complete_polls`, deactivating at 2
   consecutive absences).
4. Persists that outcome transactionally per dossier, then fetches detail
   pages for every dossier that is new, reactivated, has a changed
   `portal_status`, is still missing "Date envoi devis garage" (V1's one
   *required* detail field -- see below), or whose previous detail fetch
   failed or never ran (a dossier is queued for at most one detail fetch
   per poll even if it matches more than one of these reasons). A detail
   failure is recorded on the dossier and retried on the next poll; it
   never removes the list-level detection or its notification. An
   unchanged, already-complete dossier whose quote date is already known
   is never re-fetched.

   "Date envoi devis garage" gets this special treatment -- refetched every
   poll while blank, even with `portal_status` unchanged and
   `detail_complete=True` from a prior successful read -- because it is the
   one field the business actually depends on; a garage legitimately
   hasn't sent a quote yet is exactly the case where re-checking pays off.
   The other four (optional) detail dates do **not** get this treatment:
   retrying every dossier forever merely because an optional date is
   blank would be pure waste, so they only refresh via a `portal_status`
   change or the normal failed/never-run retry above.
5. Records a `poll_runs` row and updates `portal_accounts` (`last_poll_at`,
   `last_success_at` only on `COMPLETE`, `session_status`, `last_error`).

**Failure handling**: a `poll_runs` row (and `portal_accounts.last_poll_at`)
is only ever created *after* the portal reader's browser/profile context has
actually opened. `BrowserProfileLockedError` (session configuration owns the
profile) is therefore a safe skip that leaves **no** `poll_runs` row at all.
Any other failure to launch or enter the reader — a Camoufox/Playwright
error, not a domain-level `PortalAuthRequiredError`/`PortalReadError` — gets
exactly one `poll_runs` row, created and finished as `FAILED` together in
one transaction, with `portal_accounts.session_status` set to `ERROR` and a
short technical message in `last_error`; the existing dataset is untouched.
`SyncAgreementQueue.execute()` never raises for this case, so the scheduler
loop keeps running on its next interval and the manual refresh endpoint
re-renders the dashboard (showing that error banner) instead of a 500.

**Baseline**: the first `COMPLETE` poll for an account stores every
currently-listed dossier and raises **zero** notifications (this is
`is_baseline_poll` in `sync_rules.ReconciliationResult`); `PARTIAL` polls
before baseline behave the same way (no notifications) but do not, by
themselves, mark the baseline complete. After baseline, any never-seen
record ID is a real "Nouveau" notification, and a previously-inactive
record ID reappearing raises another notification occurrence.

**Absence bookkeeping is COMPLETE-poll-only**: `PARTIAL`, `AUTH_REQUIRED`
and `FAILED` polls never touch `missing_complete_polls` and never
deactivate a dossier — a partial or failed read must never look like mass
disappearance. Rows that *were* seen during a `PARTIAL` poll are still
refreshed and can still be newly created, since that is real positive
signal even when pagination did not fully complete.

## Notifications and per-employee unread state

A `notifications` row is the shared, portal-wide fact that a dossier
newly appeared or reappeared. `notification_reads` is what makes "unread"
per-employee: a notification is unread for a user until a row exists for
that `(notification_id, user_id)` pair. Opening a dossier's detail page is
the explicit acknowledgement — it inserts `notification_reads` for that
dossier's notifications for the current user only, so acknowledging in one
employee's session never touches another employee's unread state. Creating
a new local user immediately inserts `notification_reads` for every
existing notification for that user, so a new employee starts with zero
inherited unread notifications.

Work status (`dossier_work`, optimistic-locked via `version`) and notes
(`dossier_notes`) are shared across every employee by design — only "read"
state is per-employee.

## Storage

SQLite runs in WAL mode with `foreign_keys=ON` (set on every connection in
`infrastructure/db/session.py`). SQLAlchemy is confined to
`infrastructure.db`, Camoufox to `infrastructure.portal`, and FastAPI to
`web`/`bootstrap`. All runtime data (database, browser profile, logs,
session secret) lives under `%LOCALAPPDATA%\RMAPortal`, never inside the
source checkout.

## Local authentication

Argon2 password hashes, a signed (not encrypted, but tamper-proof)
timestamped cookie (`infrastructure/security/sessions.py`) carrying only the
user id, a 12-hour max age, `HttpOnly` + `SameSite=Lax`, plain HTTP (the
office LAN is trusted; V1 has no TLS). Every mutating route additionally
requires a same-origin `Origin`/`Referer` header
(`web/deps.py:check_same_origin`) since there is no separate CSRF-token
infrastructure.
