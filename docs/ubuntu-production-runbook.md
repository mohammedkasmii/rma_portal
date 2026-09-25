# RMA Platform V2 -- Ubuntu production runbook

Deploys the V2 stack (`compose.prod.yaml`) on the agency's Ubuntu VM. It never touches the
existing Wexia containers, networks, volumes or ports, and it does not reuse the Phase 0
`rma-poc` stack (both can run side by side).

## 1. What runs

```text
Employee browser ── :8480 ──► web (nginx: React static files + /api proxy)
                                   └──► api (FastAPI, JSON /api/v1)  ──► db (PostgreSQL 16)
Administrator ──── :6081 ──► browser (Xvfb + openbox + x11vnc + noVNC, visible Camoufox login)
                              shares two volumes with ▼
                           worker (scheduler: one headless Camoufox context per cycle,
                                   PostgreSQL advisory lock, outbox, heartbeat) ──► db
Ollama (optional, on the VM host, NOT in this stack) ◄── api / worker (local URL only)
```

| Service | Container | Purpose | Published port |
|---|---|---|---|
| `db` | `rma-portal-db` | PostgreSQL 16 (named volume `rma-portal-pgdata`) | none |
| `migrate` | `rma-portal-migrate` | one-shot: Alembic `upgrade head` + workflow catalog | none |
| `api` | `rma-portal-api` | FastAPI; scheduler disabled (`RMA_PORTAL_RUN_SCHEDULER=false`) | none |
| `worker` | `rma-portal-worker` | polling, on-demand cycles, outbox, AI jobs | none |
| `web` | `rma-portal-web` | nginx + React build | **8480** (`RMA_WEB_PORT`) |
| `browser` | `rma-portal-browser` | noVNC desktop for the OmegaFlow login | **6081** (`RMA_NOVNC_PORT`) |

Named volumes (all prefixed `rma-portal-`): `pgdata`, `data` (application data), `logs`,
`browser-profile`, `session-state`. Network: `rma-portal-net`. Compose project: `rma-portal`.

The API and the worker are the **same image and version** built from this repository. Only
one synchronization cycle runs at a time across all processes: the worker takes a PostgreSQL
advisory lock and skips its turn if another cycle holds it (a crashed holder releases it
automatically).

## 2. Prerequisites (run on the VM)

```bash
docker --version && docker compose version          # Docker Engine + compose v2
docker ps --format '{{.Names}}\t{{.Ports}}'          # note what already runs; leave wexia-* alone
ss -ltn | grep -E ':(8480|6081)\b' || echo "ports free"   # choose others in .env if taken
df -h /var/lib/docker                                # a few GB free (browser image is large)
```

## 3. First deployment

```bash
git fetch origin && git switch multi-workflow-v2 && git pull --ff-only
cp .env.example .env && chmod 600 .env
# Edit .env: POSTGRES_PASSWORD, RMA_SESSION_SECRET (openssl rand -hex 32), RMA_VNC_PASSWORD,
# and the public addresses (RMA_NOVNC_URL, RMA_PUBLIC_ORIGIN) of this VM.
docker compose -f compose.prod.yaml config -q && echo "compose OK"

docker compose -f compose.prod.yaml build           # first build downloads the pinned browser
docker compose -f compose.prod.yaml up -d
scripts/prod/check.sh                               # every service healthy, catalog seeded
```

Create the first administrator, then open `http://<vm>:8480`:

```bash
docker compose -f compose.prod.yaml run --rm --no-deps api create-admin
```

### Connect OmegaFlow (real credentials required)

```bash
scripts/prod/connect-session.sh
# open the printed noVNC URL, enter RMA_VNC_PASSWORD, log in to OmegaFlow by hand
# (finish any "Validate your session" screen); the command ends with RESULT=CAPTURED
```

The worker reuses the captured session on its next cycle (at most `RMA_POLL_INTERVAL_SECONDS`,
or immediately after the **Actualiser maintenant** button). The first complete read of **each
workflow** is its silent baseline: no employee alert is created for what is already in a queue.
Re-run `connect-session.sh` whenever the dashboard shows "Reconnexion requise".

## 4. Cutover from the Windows installation (SQLite)

Keep the Windows service stopped for the final copy so no poll writes during the import.

```bash
# from the Windows PC (PowerShell): copy the database file to the VM
scp "$env:LOCALAPPDATA\RMAPortal\rma_portal.sqlite3" user@vm:/home/user/rma_portal.sqlite3

# on the VM
docker compose -f compose.prod.yaml stop worker api web
docker compose -f compose.prod.yaml run --rm --no-deps \
    -v /home/user/rma_portal.sqlite3:/import/rma_portal.sqlite3:ro \
    --entrypoint rma-portal api import-sqlite --source /import/rma_portal.sqlite3
docker compose -f compose.prod.yaml up -d
```

The import is **idempotent** (run it twice safely), never modifies the source file, keeps primary
keys, and reports inserted/updated/skipped rows per table. Dossiers, notes, per-employee read
state, users and the Garage agréé work status all carry over; the V2 migration first backfills
occurrences and workflow work status on a scratch copy of the source. Employees keep their
existing passwords. The browser session is **not** copied: run `connect-session.sh` again.

## 5. Daily operations

| Task | Command |
|---|---|
| Health of everything | `scripts/prod/check.sh` |
| Follow logs | `docker compose -f compose.prod.yaml logs -f --tail 100 api worker` |
| Worker/cycle timings | `docker compose -f compose.prod.yaml logs worker \| grep -E 'stage=(workflow_sync\|synchronization)'` |
| Restart one service | `docker compose -f compose.prod.yaml restart worker` |
| Trigger a cycle now | UI **Actualiser maintenant** (API queues it; the worker runs it within seconds) |
| Application logs on disk | volume `rma-portal-logs` (`/var/log/rma-portal`, rotated) |
| Stop everything, keep data | `docker compose -f compose.prod.yaml stop` |

Administrators manage workflows in **Administration → Workflows**: disable a broken queue (the
others keep working), promote a rule from *À valider sur site* to *Confirmé* once the agency
validated it, or switch a queue between ACTION / INFORMATIONAL / SILENT.

### Update to a new version

```bash
scripts/prod/backup.sh                       # always first
git pull --ff-only
docker compose -f compose.prod.yaml build
docker compose -f compose.prod.yaml up -d    # `migrate` runs before api and worker start
scripts/prod/check.sh
```

Migrations are additive. To roll back the application, check out the previous revision and
rebuild; restore the backup only if a migration must be undone.

### Backups and restore

```bash
scripts/prod/backup.sh                  # verified pg_dump (custom format) + sha256, 14 kept
scripts/prod/backup.sh --with-session   # also the captured session (contains cookies: keep private)
# daily at 02:30 (crontab -e):
# 30 2 * * * cd /path/to/rma_portal && scripts/prod/backup.sh >> backups/backup.log 2>&1

scripts/prod/restore.sh backups/rma-<timestamp>.dump      # asks you to type RESTORE
```

Copy `backups/` off the VM regularly. A restore stops api/worker/web, replaces the database,
re-applies migrations and restarts. After restoring on a new VM, run `connect-session.sh`.

## 6. Optional local AI (Ollama)

Ollama stays a separate service on the VM. In `.env`:

```text
RMA_OLLAMA_ENABLED=true
RMA_OLLAMA_BASE_URL=http://host.docker.internal:11434
RMA_OLLAMA_MODEL=qwen3:8b
RMA_OLLAMA_TIMEOUT_SECONDS=45
```

then `docker compose -f compose.prod.yaml up -d api worker`. The adapter refuses any non-local
URL and sends only minimal, non-identifying facts; if Ollama is stopped, slow or answers badly
the portal keeps working and the assistant panel shows a neutral message. Check
`GET /api/v1/ai/status` (or the panel's "hors ligne" hint).

## 7. Security notes

- PostgreSQL is only reachable inside `rma-portal-net`; only the web UI and the noVNC desktop
  are published. Restrict both ports to the office LAN in the VM firewall (`ufw`).
- Set `RMA_COOKIE_SECURE=true` only when the UI is served over HTTPS (terminate TLS in front
  of the `web` service); the session cookie is always HTTP-only and same-site.
- `.env`, `backups/` and the `rma-portal-session-state` volume hold secrets: keep them private.
- Automated traffic to OmegaFlow is read-only (PUT/PATCH/DELETE and record POSTs are blocked in
  the browser context). To confirm on site, review `docker compose logs worker | grep blocked`:
  it must stay empty.

## 7b. Never touch

`wexia-*` containers, networks, volumes and ports; the Phase 0 `rma-poc-*` stack (unless you
choose to stop it after the cutover); `docker system prune` / `docker volume prune` on this host.
None of the scripts in `scripts/prod/` does any of that.

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| `compose config` complains about a variable | the three secrets in `.env` are required; `docker compose -f compose.prod.yaml config -q` |
| `migrate` exits non-zero | `docker compose -f compose.prod.yaml logs migrate` (database password, DB not healthy) |
| api never becomes healthy | `logs api`; the API waits for `migrate` to finish successfully |
| Dashboard: "Reconnexion requise" | `scripts/prod/connect-session.sh` |
| `RESULT=PROFILE_BUSY` | the worker is mid-cycle; wait a minute and retry |
| A workflow shows FAILED/PARTIAL | Administration → Santé des workflows (last error); disable it if it is broken, others continue |
| Employees are logged out after an update | `RMA_SESSION_SECRET` changed |
| Cross-origin 403 on login/mutations behind another proxy | set `RMA_PUBLIC_ORIGIN` to the exact URL employees use |
| Worker unhealthy | `docker compose -f compose.prod.yaml logs worker`; heartbeat file `/var/lib/rma-portal/worker.heartbeat` |

## 9. Live validation with real credentials (first day)

Nothing below could be verified offline; see the checklist at the end of the final report and
work through it in this order:

1. `check.sh` all green; `connect-session.sh` → `RESULT=CAPTURED`.
2. First cycle: each **enabled** workflow ends COMPLETE and marked baseline (zero alerts).
3. Compare each queue's row count in RMA Portal with the OmegaFlow page (all pages).
4. For every *À valider sur site* workflow: confirm the arrival rule, the date shown and whether
   the staff want it enabled; promote or disable in the admin page.
5. Empty queues (EXPC 2ème expert, Réserves en cours, Appréciation, Hifad filter) stay empty;
   when a real row appears, confirm columns and the detail link.
6. Watch one full hour of cycles (`grep stage= worker log`) for timing; adjust
   `RMA_POLL_INTERVAL_SECONDS` / `RMA_MAX_DETAIL_READS_PER_CYCLE` if needed.
7. Kill the worker container during a cycle and confirm the next cycle recovers; restart the VM
   and confirm the session is still valid without re-login.
