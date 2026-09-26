# RMA Portal — agency Ubuntu server: staged production deployment

Target: the shared agency server (Ubuntu 24.04, x86_64, LAN `192.168.1.32`, Docker 29 / Compose 5,
separate `/data` filesystem). It already runs **Wexia, Shexpert, Supabase and RustDesk**. RMA must be
isolated, staged, reversible, and unable to modify any existing workload.

This runbook is a **review-first plan**. Nothing in it has been executed on the server.
The VM workflow (`compose.prod.yaml`, `docs/ubuntu-production-runbook.md`) is unchanged and remains the
validation environment; the agency server uses the separate, standalone `compose.agency.yaml`.

## 0. Rules that apply to every stage

| Rule | Consequence |
|---|---|
| Only RMA resources are ever named | Every mutating command names an RMA service (`db`, `api`, `web`, `browser`, `worker`) or container `rma-portal-*`. There is no “all containers” command. |
| No generic start | Nothing here starts the whole stack at once. Each service starts alone with `--no-deps`, in the order of the stages. |
| Production never builds or pulls | Images arrive as a verified offline bundle. Every service has `pull_policy: never`; the file has no `build:`. |
| Existing workloads are read-only to us | Their unhealthy/restarting containers are recorded as a **pre-existing condition** and never repaired. Never read another container’s environment or another application’s secrets (do not run a bare `docker inspect` on them; the runbook only uses `--format` on `rma-portal-*` containers). |
| Nothing global changes | No Docker daemon setting or restart, no firewall change, no web-server change, no package install/upgrade/removal, no host restart, no pruning of images/volumes/networks/build cache, no deletion of any volume or network, no privileged container, host networking or Docker-socket mount. |
| Data is never deleted | Rollbacks stop services; they never delete `/data/rma-portal` or any volume, and never use a “remove volumes” flag. |
| Stop on doubt | Each stage has an explicit **Stop** condition. Do not continue past a failed check; do not “fix forward” on the server. |

## 1. Final topology and resource limits

One Compose project **`rma-portal`**, one private network **`rma-portal-net`**, containers `rma-portal-*`.
No dependency on any other project, container or network.

| Service | Container | Image | Published | CPUs | Memory (limit / reservation) | PIDs | shm | Restart | Health check |
|---|---|---|---|---|---|---|---|---|---|
| `db` | `rma-portal-db` | `rma-portal-postgres:<V>` | **none** | 1 | 2 GiB / 512 MiB | 256 | — | unless-stopped | `pg_isready` |
| `migrate` (one-shot) | run with `run --rm` | `rma-portal:<V>` | none | 1 | 1 GiB / 256 MiB | 256 | — | no | exit code |
| `api` | `rma-portal-api` | `rma-portal:<V>` | **none** (`8765` internal) | 1 | 1 GiB / 256 MiB | 256 | — | unless-stopped | `curl /api/v1/health` |
| `worker` | `rma-portal-worker` | `rma-portal:<V>` | none | 2 | 3 GiB / 1 GiB | 512 | 1 GiB | unless-stopped | `rma-portal worker-health` |
| `web` | `rma-portal-web` | `rma-portal-web:<V>` | `192.168.1.32:8480 → 8080` | 0.5 | 256 MiB / 64 MiB | 128 | — | unless-stopped | `wget /healthz` |
| `browser` | `rma-portal-browser` | `rma-portal:<V>` | `192.168.1.32:6081 → 6080` | 2 | 3 GiB / 1 GiB | 512 | 1 GiB | unless-stopped | noVNC/Xvfb/control script |

- `<V>` = the 12-character git commit of the bundle (`RMA_VERSION`, mandatory, no default).
- `memswap_limit` equals the memory limit for every service: an RMA container can never use swap and slow down the host.
- Every service: `cap_drop: [ALL]`, `no-new-privileges`, `init: true`, and bounded `json-file` logs (`10m × 5`).
  Extra capabilities only where the validated images already need them: `db` (`CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID`,
  the official entrypoint starts as root then drops to `postgres`) and `web` (`CHOWN, SETGID, SETUID`, nginx).
- `api` and `web` are read-only filesystems with `tmpfs` for `/tmp` (and nginx cache/run). `worker` and `browser` are not read-only:
  Firefox/Xvfb/openbox write under `$HOME` and `/tmp`; this is exactly the validated VM behaviour and is unchanged.
- The limits are **starting ceilings** (32 threads, 62 GiB host, ~40 GiB free). Camoufox needs headroom: watch
  `docker stats --no-stream rma-portal-worker rma-portal-browser` during Stage 13; if a service is OOM-killed, raise that one
  limit through a reviewed change to `compose.agency.yaml` — never by removing limits.
- Ollama is **not** part of the stack and `host.docker.internal` is not mapped. AI is off (`RMA_OLLAMA_ENABLED=false`).

## 2. Persistent storage (all under `/data/rma-portal`)

Every persistent byte is an explicit bind mount (`create_host_path: false`, so Docker can never create a root-owned directory
by itself). Nothing persistent lives under `/var/lib/docker`; image layers stay in Docker’s existing root and its data-root is not modified.

UIDs were **determined, not guessed**, by running `id` inside the pinned images and reading the Dockerfile: the `postgres:16-alpine` image
runs PostgreSQL as `postgres` **70:70**; the runtime image (`docker/prod/Dockerfile`, `RMA_UID=10001`) runs API, worker, migration and browser as
`rma` **10001:10001**; the web image mounts nothing. `1000:1000` is `ubuntu`, the operator who owns `/data`.

| Host directory | Owner | Mode | Mounted in | At | Sensitive |
|---|---|---|---|---|---|
| `/data/rma-portal` (root) | 1000:1000 | 0750 | — | — | — |
| `postgres` | **70:70** | **0700** | `db` | `/var/lib/postgresql/data` | database |
| `app-data` | 10001:10001 | 0750 | `migrate`, `api`, `worker` | `/var/lib/rma-portal` | app data |
| `logs` | 10001:10001 | 0750 | `migrate`, `api`, `worker` | `/var/log/rma-portal` | logs |
| `browser-profile` | 10001:10001 | **0700** | `worker`, `browser` | `/var/lib/rma-poc/profile` | cookies/profile |
| `session-state` | 10001:10001 | **0700** | `worker`, `browser` | `/var/lib/rma-poc/state` | **OmegaFlow session** |
| `backups` | 1000:1000 | **0700** | (host only) | — | dumps, optional session |
| `releases` | 1000:1000 | 0750 | (host only) | — | deployment files |
| `image-bundles` | 1000:1000 | 0750 | (host only) | — | image tarballs |
| `config` | 1000:1000 | **0700** | (host only) | — | **`.env` (secrets)** |

`scripts/agency/prepare-storage.sh` creates exactly this, non-recursively; it never touches `/data` itself. `sudo` is needed only for `--apply`
(a `chown` to uid 70/10001) and for read-only `du`/`ls` of the 0700 directories. Sensitive directories are never world-readable.

## 3. Network exposure

| What | Bind | Reachable by |
|---|---|---|
| Employee portal | `192.168.1.32:8480` | office LAN → **`http://192.168.1.32:8480`** |
| Administrator noVNC | `192.168.1.32:6081` | office LAN; VNC password required; link shown only to RMA administrators |
| PostgreSQL, FastAPI | none | only inside `rma-portal-net` |

An RMA administrator uses **Se connecter / Reconnecter** in the portal. The authenticated
API asks the browser container to start Camoufox over an internal token-protected endpoint,
then opens `http://192.168.1.32:6081/vnc.html?autoconnect=1&resize=scale`. No terminal or SSH
knowledge is required and the Docker socket is never mounted.

- Nothing binds `0.0.0.0` and nothing binds the Tailscale address (`100.89.63.25`); `RMA_WEB_BIND_IP` has no default so an empty value fails loudly.
- Docker publishes ports through its own packet-filter chain, which bypasses the host firewall’s normal rules; the **bind address is the control**.
  The firewall and the web server on the host are **not** modified by this deployment. If the Tailscale node advertises the LAN subnet, tailnet
  devices may reach `192.168.1.32:8480`; review that separately.
- `RMA_COOKIE_SECURE=false` for the initial trusted-LAN HTTP pilot. **HTTPS (and `RMA_COOKIE_SECURE=true`) is a later, separately approved change.**

## 4. Secrets and environment

`.env.agency.example` lists names and safe defaults only. The real file is `/data/rma-portal/config/.env`, mode **0600**, never committed.

| Secret | Notes |
|---|---|
| `POSTGRES_PASSWORD` | Consumed by PostgreSQL **only when it first initialises an empty data directory** (Stage 5); changing it later does not change the database. Charset `[A-Za-z0-9._~-]` (it is embedded in a URL). |
| `RMA_SESSION_SECRET` | Signs employee cookies. **Keep it identical across upgrades.** Changing it logs every user out. |
| `RMA_VNC_PASSWORD` | noVNC/VNC uses **only the first 8 characters**; anything longer is silently ignored. Generated as exactly 8 random hex characters and required from the LAN viewer. |
| `RMA_BROWSER_CONTROL_TOKEN` | Internal API-to-browser secret. Never sent to a browser and never published as a host port. Keep it identical across ordinary upgrades. |

Secrets are generated **on the server** and never printed (Stage 3). `preflight.sh` refuses empty or placeholder secrets, tests them with `grep -q` only,
and never prints a value. `RMA_OLLAMA_ENABLED` must be `false` for the first deployment; `RMA_POLL_INTERVAL_SECONDS=3600`.

## 5. Immutable images and the offline bundle

Tags are derived from the clean commit that contains this hardening: `RMA_VERSION` = first 12 characters of `git rev-parse HEAD`.
`rma-portal:<V>`, `rma-portal-web:<V>` and `rma-portal-postgres:<V>` always share one version; there is no `latest` and no `2.0.0`.
`rma-portal-postgres:<V>` is the **exact pinned `postgres:16-alpine@sha256:…`** image of `compose.prod.yaml`, re-tagged (same image ID) because
`docker load` does not keep registry digests.

Build on the **validated Ubuntu VM** (never on the server), from a clean checkout of the deployment commit:

```bash
scripts/agency/export-images.sh --output-dir /path/to/existing/dir
```

It refuses a dirty tree (tracked, staged or untracked changes), builds the two images, saves the three images into one tar and writes:

| File | Content |
|---|---|
| `rma-portal-images-<V>.tar` | the three images (`docker save`) |
| `rma-portal-images-<V>.tar.sha256` | `sha256sum -c` compatible checksum |
| `rma-portal-images-<V>.manifest.json` | schema 2: full git commit, tags, per image the **`archive_config_id`** (authoritative) plus informational `source_engine_id` / `source_repo_digests`, creation time (UTC), `linux/amd64`, archive name/size/checksum |
| `rma-portal-deploy-<V>.tar.gz` (+ `.sha256`) | `git archive` of the tracked deployment files only: `compose.agency.yaml`, `.env.agency.example`, `scripts/agency/`, this runbook |

It contains no `.env`, password, cookie, database data or session state (images are built from tracked files with the existing ignore rules; the
deployment archive is a `git archive`). Existing output files are never overwritten.

**Which image ID is authoritative.** `docker save` writes each image's *config object*, and `docker load` registers the SHA-256 of that config as the image `.Id`.
The exporting engine's own `.Id` can instead be an OCI manifest-list or registry-manifest digest, which does **not** survive a save/load round trip. The exporter therefore derives
each `archive_config_id` from the finished tar (reading `manifest.json` and the referenced config member directly, extracting nothing, hashing the config bytes) and stores the
source engine ID/digests only as informational fields. `verify-bundle.sh` re-derives them from the tar and refuses a bundle whose tags or configs do not match; `load-images.sh` and
`preflight.sh --require-images` compare `docker image inspect` IDs with `archive_config_id` only. Bundles with an older manifest schema are rejected: export a new versioned bundle.

Server side (all read-only until `--apply`):

```bash
scripts/agency/verify-bundle.sh /data/rma-portal/image-bundles/rma-portal-images-<V>.tar   # checksum + tar image configs + manifest + architecture
scripts/agency/load-images.sh   /data/rma-portal/image-bundles/rma-portal-images-<V>.tar   # dry-run: verify, list what would load
scripts/agency/load-images.sh   /data/rma-portal/image-bundles/rma-portal-images-<V>.tar --apply   # verify again, then docker load
```

The loader verifies the checksum first in both modes, aborts on any mismatch, refuses to run if a same-named image has a different ID, never deletes or
replaces an unrelated image, never prunes, never pulls or builds, and confirms every loaded image ID equals the manifest's `archive_config_id`.

## 6. Staged first deployment

**Conventions.** `<V>` is the 12-character version; replace it literally. From Stage 3 on, commands run from the release directory and use the
explicit environment file (no shell function, no hidden variables):

```bash
cd /data/rma-portal/releases/rma-portal-deploy-<V>
# every compose command below:  docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml <...>
```

Two read-only helpers are used repeatedly. **RMA view** (only `rma-portal-*`):

```bash
docker ps -a --filter "name=^rma-portal-" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

**Others unchanged?** (identity of every non-RMA container: ID, name, image, creation time — a recreate changes it). Baseline is stored in Stage 2:

```bash
docker ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.CreatedAt}}' | grep -v ' rma-portal-' | sort \
  | diff - /data/rma-portal/releases/snapshots/before-identity.txt && echo "NON-RMA CONTAINERS UNCHANGED"
```

Any difference must be explained by that application’s owners (RMA cannot cause it: no RMA command targets them). Statuses of the already-restarting
Shexpert containers change constantly and are compared by identity, not by status.

---

### Stage 0 — Approval gate (no mutation)

| | |
|---|---|
| Command | none; review only |
| Confirm | (1) the final VM reader cycle is **21 COMPLETE, 0 FAILED**; (2) the deployment branch is clean and its commit is the one in the manifest (`git status --porcelain` empty on the VM); (3) `scripts/agency/verify-bundle.sh <bundle.tar>` on the VM prints *verified* and the manifest commit/tags/image IDs match; (4) the current server audit is the one in this document (hostname, 24.04, x86_64, Docker 29.7.2 / Compose 5.5.0, `/data` 1.3 TB free, ports 8480 and 6081 free); (5) no RMA collisions (Stage 1 proves it); (6) write down on the VM the counts you will compare with later: `select count(*) from information_schema.tables where table_schema='public'`, `select version_num from alembic_version`, `select count(*), count(*) filter (where enabled) from workflows`. |
| Affects | nothing |
| Stop | any item unconfirmed, or anyone has not approved |
| Rollback | not applicable |

### Stage 1 — Read-only preflight

Copy `rma-portal-deploy-<V>.tar.gz` and its `.sha256` to the `ubuntu` home directory (`scp` from the VM), verify and unpack it into a **new** directory in that home
(the only file placement before Stage 2; it is outside every workload and outside `/data`):

```bash
cd ~ && sha256sum -c rma-portal-deploy-<V>.tar.gz.sha256 && mkdir rma-preflight && tar -xzf rma-portal-deploy-<V>.tar.gz -C rma-preflight
bash ~/rma-preflight/rma-portal-deploy-<V>/scripts/agency/preflight.sh --pre-config
```

| | |
|---|---|
| Expected | `== Result: 0 blocker(s)`. Warnings are expected: live-restore off, root filesystem at 96 %, and the **pre-existing restarting/unhealthy** Shexpert/Wexia containers (listed by name, never touched). `[ OK ]` for: amd64, Docker daemon reachable, `/data` on its own filesystem with ≥ 50 GiB, root has room to load images, ports 8480 and 6081 free, no `rma-portal-*` container/network, `/data/rma-portal` absent |
| Affects | nothing. The script creates, writes, pulls, builds, starts, stops, restarts and prunes nothing, reads no secret and prints none |
| Stop | **any `[FAIL]`.** Do not continue. Warnings about pre-existing unhealthy containers are recorded in the deployment log, not repaired |
| Rollback | none needed (`rm -r ~/rma-preflight` may be done by you, by exact path, whenever you like) |

### Stage 2 — Prepare storage

```bash
bash ~/rma-preflight/rma-portal-deploy-<V>/scripts/agency/prepare-storage.sh          # dry-run: review every line
sudo bash ~/rma-preflight/rma-portal-deploy-<V>/scripts/agency/prepare-storage.sh --apply                   # only after approval
```

| | |
|---|---|
| Expected (dry-run) | 30 planned lines: `mkdir/chown/chmod` for `/data/rma-portal` and the nine subdirectories exactly as in §2; nothing on `/data` itself |
| Expected (`--apply`) | `verified:` for all ten directories, then “No container was started; no secret was created.” Re-running prints “(none: everything already in place)” |
| Affects | creates `/data/rma-portal/*` only. Refuses `/`, `/data`, `/var`, `/var/lib/docker`, empty, relative, non-canonical or symlinked roots; refuses unexpected entries; never recurses; refuses to change ownership of a non-empty directory |
| Verify | `stat -c '%u:%g %a %n' /data/rma-portal /data/rma-portal/*` matches §2; then save the baseline: `mkdir /data/rma-portal/releases/snapshots && docker ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.CreatedAt}}' \| grep -v ' rma-portal-' \| sort > /data/rma-portal/releases/snapshots/before-identity.txt` and keep `docker ps -a --format '{{.Names}} {{.Status}}' \| sort` in the deployment log |
| Stop | any refusal, any mismatch |
| Rollback | leave the (empty) directories; removal of any RMA directory is outside this runbook and needs explicit approval |

### Stage 3 — Install configuration (no service starts)

```bash
# 3a. files: bundle + deployment archive are copied (scp) into /data/rma-portal/image-bundles, then:
cd /data/rma-portal/image-bundles && sha256sum -c rma-portal-deploy-<V>.tar.gz.sha256
tar -xzf rma-portal-deploy-<V>.tar.gz -C /data/rma-portal/releases        # creates releases/rma-portal-deploy-<V>/

# 3b. secrets, generated here, never printed. noclobber makes an existing .env impossible to overwrite.
cd /data/rma-portal/releases/rma-portal-deploy-<V>
( set -o noclobber; umask 077
  { grep -v -E '^(RMA_VERSION|POSTGRES_PASSWORD|RMA_SESSION_SECRET|RMA_VNC_PASSWORD|RMA_BROWSER_CONTROL_TOKEN)=' .env.agency.example
    echo "RMA_VERSION=<V>"
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
    echo "RMA_SESSION_SECRET=$(openssl rand -hex 32)"
    echo "RMA_VNC_PASSWORD=$(openssl rand -hex 4)"
    echo "RMA_BROWSER_CONTROL_TOKEN=$(openssl rand -hex 32)"
  } > /data/rma-portal/config/.env )
stat -c '%U:%G %a %n' /data/rma-portal/config/.env                          # must be ubuntu:ubuntu 600

# 3c. validate — resolves the stack without starting anything
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml config -q && echo CONFIG OK
bash scripts/agency/preflight.sh --storage-prepared
```

| | |
|---|---|
| Expected | `.env` is `ubuntu:ubuntu 600`; `CONFIG OK`; preflight has 0 blockers; the image lines are `[WARN] image not loaded yet` (expected before Stage 4) |
| Affects | writes the release directory and `config/.env` only. **Do not print or paste `.env`.** Never commit it. Back up `RMA_SESSION_SECRET` in the agency’s password manager if policy requires; keep it unchanged from now on |
| Stop | `.env` not 0600; `config -q` error; any preflight `[FAIL]` (placeholder/short secret, wrong bind IP, AI enabled, storage mismatch) |
| Rollback | nothing is running. A wrong `.env` is fixed by editing it by hand (it can’t be overwritten by the command above); keep the secrets |

### Stage 4 — Verify and load images (no pull, no build)

```bash
cd /data/rma-portal/releases/rma-portal-deploy-<V>
bash scripts/agency/verify-bundle.sh /data/rma-portal/image-bundles/rma-portal-images-<V>.tar
bash scripts/agency/load-images.sh   /data/rma-portal/image-bundles/rma-portal-images-<V>.tar            # dry-run
bash scripts/agency/load-images.sh   /data/rma-portal/image-bundles/rma-portal-images-<V>.tar --apply    # after approval
bash scripts/agency/preflight.sh --storage-prepared --require-images \
     --manifest /data/rma-portal/image-bundles/rma-portal-images-<V>.manifest.json \
     --bundle   /data/rma-portal/image-bundles/rma-portal-images-<V>.tar
```

| | |
|---|---|
| Expected | `checksum … OK`; manifest printed; dry-run lists `would load` for the three tags; after `--apply` three `OK  <ref> sha256:<id>` lines equal to the manifest's `archive_config_id`; preflight 0 blockers with three `image ID matches the manifest` |
| Affects | adds exactly `rma-portal:<V>`, `rma-portal-web:<V>`, `rma-portal-postgres:<V>` to the local image store (layers under Docker’s existing root; ~2–3 GiB). No other image is modified or removed; no pruning afterwards |
| Stop | checksum or ID mismatch, architecture mismatch, root free space below the preflight threshold, a `CONFLICT` line |
| Rollback | none (images are inert). Never delete images to “clean up”; unused RMA images may stay until an explicit, separately approved cleanup |

### Stage 5 — Start PostgreSQL only

```bash
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml up -d --no-deps db
```

| | |
|---|---|
| Expected | `Network rma-portal-net Created`, `Container rma-portal-db Started` (creating its own network is the only network action) |
| Verify | RMA view shows **only** `rma-portal-db`; `docker inspect --format '{{.State.Health.Status}}' rma-portal-db` becomes `healthy` within ~30 s; `docker inspect --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' rma-portal-db` shows `/data/rma-portal/postgres -> /var/lib/postgresql/data`; `docker exec rma-portal-db ls /var/lib/postgresql/data` lists `PG_VERSION`; `stat -c '%u:%g' /data/rma-portal/postgres` is `70:70`; “Others unchanged?” prints `NON-RMA CONTAINERS UNCHANGED`; `docker port rma-portal-db` prints nothing |
| Affects | creates `rma-portal-net` and `rma-portal-db`; initialises PostgreSQL in `/data/rma-portal/postgres` |
| Stop | not healthy after 2 minutes (read `docker logs --tail 50 rma-portal-db`), any other RMA container present, any non-RMA difference |
| Rollback | `docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml stop db` (data kept; never remove the directory; a half-initialised data directory is investigated, not deleted) |

### Stage 6 — Migration only

```bash
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml run --rm --no-deps migrate ; echo "exit=$?"
```

| | |
|---|---|
| Expected | `Migrations appliquées : PostgreSQL (workflows créés : N, actualisés : 0)` and `exit=0`; the one-shot container is removed by `--rm` |
| Verify | `docker exec rma-portal-db psql -U rma -d rma -Atc "select count(*) from information_schema.tables where table_schema='public'"` and `select version_num from alembic_version` and `select count(*), count(*) filter (where enabled) from workflows` equal the values recorded on the VM in Stage 0; `rma-portal-db` still `healthy`; RMA view shows no `api`, `worker`, `web`, `browser` |
| Affects | schema and workflow catalog in the RMA database only (additive migrations) |
| Stop | non-zero exit, count mismatch |
| Rollback | stop only `db` if needed. **Application rollback does not reverse a migration.** Database restore requires a verified RMA backup and explicit confirmation (§8) |

### Stage 7 — API only

```bash
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml up -d --no-deps api
```

| | |
|---|---|
| Verify | `docker exec rma-portal-api curl -fsS --max-time 4 http://127.0.0.1:8765/api/v1/health` returns `{"status":"ok"}`-style JSON; health becomes `healthy`; `docker port rma-portal-api` prints nothing and the RMA view shows no host mapping; `ss -ltn \| grep 8765` prints nothing |
| Affects | creates `rma-portal-api` (reads/writes `app-data`, `logs`) |
| Stop | unhealthy after 2 minutes (`docker logs --tail 50 rma-portal-api`); any host port |
| Rollback | `… stop api` |

### Stage 8 — Web only

```bash
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml up -d --no-deps web
```

| | |
|---|---|
| Verify | `docker port rma-portal-web` → exactly `8080/tcp -> 192.168.1.32:8480`; `ss -ltn \| grep -E ':8480\b'` shows only `192.168.1.32:8480`; `curl -fsS http://192.168.1.32:8480/healthz` succeeds on the server; `curl -m 3 http://127.0.0.1:8480/` is refused (not bound to loopback); one authorised LAN PC opens `http://192.168.1.32:8480` and sees the login page; `rma-portal-browser` and `rma-portal-worker` are absent from the RMA view |
| Affects | creates `rma-portal-web`; publishes 8480 on the LAN address only |
| Stop | any other bind address (`0.0.0.0`, `100.89.63.25`, `127.0.0.1`), no answer from the LAN PC |
| Rollback | `… stop web` |

### Stage 9 — Create the administrator (one-shot, interactive)

```bash
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml run --rm --no-deps api create-admin
```

Type the username, display name and password when prompted (10 characters minimum; entered without echo, confirmed twice). **Never pass `--password`** — it would
land in shell history and the process list.

| | |
|---|---|
| What it writes | one row in `users`: lower-cased `username`, `display_name`, `password_hash` (a salted hash; the password itself is never stored or logged), `role='ADMIN'`, `active=true`, `created_at`. It also marks any pre-existing notifications as read for that user (none exist yet). It creates no other row and touches nothing else |
| Verify | “Administrateur '<name>' créé avec succès.”; `docker exec rma-portal-db psql -U rma -d rma -Atc "select username, role, active from users"` (never select the hash column); log in through `http://192.168.1.32:8480` |
| Stop | a duplicate-user or weak-password message (re-run with a valid choice) |
| Rollback | leave the row. Changing or removing it is a separate, explicitly approved database action |

### Stage 10 — Browser only

```bash
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml up -d --no-deps browser
```

| | |
|---|---|
| Verify | `docker port rma-portal-browser` → `6080/tcp -> 192.168.1.32:6081`; `ss -ltn \| grep -E ':6081\b'` shows only `192.168.1.32:6081`; from an administrator account click **Se connecter / Reconnecter**, enter the VNC password, and confirm Camoufox opens; the worker is still absent |
| Affects | creates `rma-portal-browser` (Xvfb + noVNC + Camoufox); writes `browser-profile` only when used |
| Stop | 6081 bound to `0.0.0.0`, a public or Tailscale address; employee accounts can trigger connection; the button opens noVNC without first starting Camoufox |
| Rollback | `… stop browser` |

### Stage 11 — Capture the OmegaFlow session

```bash
# Normal path: an administrator clicks "Se connecter / Reconnecter" in the portal.
# Console fallback only:
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml exec browser python -m rma_poc login --timeout 900
```

The administrator logs in **manually** in the noVNC window (including any validation screen). RMA never asks for or stores the OmegaFlow password: it only saves the
browser session after positive authentication.

| | |
|---|---|
| Expected | `RESULT=CAPTURED` |
| Verify | `docker exec rma-portal-browser python -m rma_poc status` (count-only summary); `docker exec rma-portal-browser ls -l /var/lib/rma-poc/state /var/lib/rma-poc/profile` shows `omegaflow-session-state.json` and the profile owned by `rma`; on the host these are `/data/rma-portal/session-state` and `/data/rma-portal/browser-profile` (`stat` shows `10001:10001 700`); the worker is still absent |
| Affects | writes the session file (cookies — sensitive) and the browser profile under `/data/rma-portal` |
| Stop | anything other than `CAPTURED` (`PROFILE_BUSY`, timeout, `AUTH_REQUIRED`): retry; do not start the worker |
| Rollback | `… stop browser`. The captured session stays on disk (0700). Discarding it needs explicit approval |

### Stage 12 — Worker last

Only when every earlier stage passed and “Others unchanged?” is still clean.

```bash
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml up -d --no-deps worker
```

**Important (verified in `WorkerLoop`):** the worker’s first scheduled cycle is due immediately, so a cycle starts within seconds of the worker starting, and then
every `RMA_POLL_INTERVAL_SECONDS` (3600). Do not intervene; that first cycle is the baseline and is verified in Stage 13.

| | |
|---|---|
| Verify | exactly one `rma-portal-worker` (`docker ps --filter "name=^rma-portal-worker$" -q \| wc -l` = 1); `healthy` after ≈ 45–60 s; `docker logs rma-portal-worker 2>&1 \| grep -E 'worker started interval_s=3600'`; polling 3600, AI disabled (`docker exec rma-portal-worker sh -c 'echo $RMA_PORTAL_OLLAMA_ENABLED $RMA_PORTAL_POLL_INTERVAL_SECONDS'` → `false 3600`; this prints two non-secret values from an RMA container only); “Others unchanged?” clean |
| Affects | creates `rma-portal-worker`; it starts reading OmegaFlow (read-only: PUT/PATCH/DELETE and record POSTs are blocked in the browser context) |
| Stop | more than one worker, unhealthy after 3 minutes, any `blocked non-read-only` log line |
| Rollback | `… stop worker` (a cycle in progress is safe to interrupt: the next start recovers) |

### Stage 13 — Verify the first cycle, then one controlled synchronization

Watch the automatic first cycle finish (typically minutes):

```bash
docker logs --since 30m rma-portal-worker 2>&1 | grep -E 'stage=(synchronization|workflow_sync)'
```

Then, as the administrator in the portal, press **Actualiser maintenant** once (the API queues one manual cycle; the worker runs it; the advisory lock makes an overlapping
request skip instead of running twice). Verify with SQL:

```bash
docker exec rma-portal-db psql -U rma -d rma -c "select id, trigger::text, status::text, workflows_total, workflows_complete, workflows_partial, workflows_auth_required, workflows_failed from sync_runs order by id desc limit 3"
docker exec rma-portal-db psql -U rma -d rma -c "select w.key, p.status::text, p.baseline, p.rows_seen, p.pages_seen, p.details_failed, p.notifications_created from workflow_poll_runs p join workflows w on w.id=p.workflow_id where p.sync_run_id=(select min(id) from sync_runs) order by w.key"
docker exec rma-portal-db psql -U rma -d rma -Atc "select count(*) from notifications"
docker logs rma-portal-worker 2>&1 | grep -c 'blocked non-read-only OmegaFlow request'
```

| | |
|---|---|
| Expected | first run: **21 enabled workflows, 21 COMPLETE, 0 FAILED / PARTIAL / AUTH_REQUIRED**, every row `baseline = t`, `notifications_created = 0` (no employee unread alerts at first baseline; `count(*) from notifications` = 0 for baseline rows); `rows_seen`/`pages_seen` are credible against the OmegaFlow pagination totals (compare each queue’s count in the portal with OmegaFlow); the manual run shows `changed`/`created` ≈ 0; **the blocked-write count is `0`**; the worker returns `healthy` after each cycle |
| Storage growth | before/after: `sudo du -sh /data/rma-portal/* 2>/dev/null` (read-only; some directories are 0700) and `df -h /data`; record the numbers |
| Affects | inserts sync/poll/dossier rows; no write to OmegaFlow |
| Stop | any count ≠ 21, any FAILED/PARTIAL/AUTH_REQUIRED (re-capture the session: Stage 11), any alert at baseline, any blocked write (**stop the worker at once**: `… stop worker`, then investigate) |
| Rollback | `… stop worker`. Data stays; a bad first baseline is analysed, not deleted |

### Stage 14 — Acceptance and polling change

Acceptance: Stages 5–13 all passed, “Others unchanged?” clean, `rma-portal-*` all healthy for a full hour of scheduled cycles, one manual backup (§8) verified by a
restore rehearsal, storage growth within expectation. **Only then** change polling:

```bash
cd /data/rma-portal/releases/rma-portal-deploy-<V>
cp -p /data/rma-portal/config/.env /data/rma-portal/config/.env.before-poll-change     # same owner and 0600; keep private
sed -i 's/^RMA_POLL_INTERVAL_SECONDS=.*/RMA_POLL_INTERVAL_SECONDS=300/' /data/rma-portal/config/.env
docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml up -d --no-deps worker    # recreates only the worker
```

| | |
|---|---|
| Verify | `docker exec rma-portal-worker sh -c 'echo $RMA_PORTAL_POLL_INTERVAL_SECONDS'` → `300`; watch two cycles; **do not enable AI in the same change** (AI is a later, separately approved change: connect the agency Ollama, set `RMA_OLLAMA_*`, validate) |
| Stop | any acceptance criterion unmet, any blocked write, any non-RMA difference |
| Rollback | restore the saved file (`cp -p …/.env.before-poll-change …/.env`) or set `3600`, and recreate only the worker with the same command |

## 7. Normal upgrade (later)

New bundle → Stage 0 checks → the upgrade preflight (run from the new release directory; `--existing-rma` accepts only genuine `rma-portal` containers, network and bind mounts, with web still on `192.168.1.32:8480` and browser on `192.168.1.32:6081`, and stays read-only): `bash scripts/agency/preflight.sh --storage-prepared --existing-rma` (the initial-deployment stages keep the strict default mode) → Stage 4 (`load-images`, dry-run then `--apply`) → manual backup (§8) → set `RMA_VERSION=<new V>` (edit the one line; **keep `RMA_SESSION_SECRET`
unchanged**) → `run --rm --no-deps migrate` → repeat the start commands of Stages 7, 8, 10 and 12 in that order (each recreates only its own service), verifying each. Changing the session secret logs everyone out.

## 8. Rollback, backups and restore

**Rollback of an application version** (never deletes data, never touches another project, never restarts Docker):

1. Keep the database and session state where they are.
2. Stop only the affected RMA services, worker first: `docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml stop worker` (then `web`, `api`, `browser` as needed).
3. Set `RMA_VERSION=<previous V>` in `/data/rma-portal/config/.env` (the previous bundle’s images must still be loaded; do not remove old images) and use the previous release directory’s `compose.agency.yaml`.
4. Restart only what you stopped, one by one, with the Stage 7, 8, 10 and 12 commands (for example `… up -d --no-deps api`).
5. **An application rollback does not reverse a database migration.** Migrations are additive; if the older version cannot run against the migrated schema, restore only from a verified RMA backup, and only with explicit confirmation.

**Manual backup** (RMA database only; no scheduler is installed):

```bash
cd /data/rma-portal/releases/rma-portal-deploy-<V>
bash scripts/agency/backup.sh                    # database only
bash scripts/agency/backup.sh --with-session     # + captured OmegaFlow session: SENSITIVE (cookies), 0600, never copy it off the server unencrypted
```

Each run writes into `/data/rma-portal/backups` (0700): `rma-<stamp>.dump` (PostgreSQL custom format, verified with `pg_restore --list`), `.dump.sha256`, `.manifest`
(no secret, never `.env`) and, last, the atomic marker `rma-<stamp>.complete` (a set without it is incomplete). Retention (`BACKUP_RETENTION`, default 14 complete
sets) deletes only `rma-*` files inside that directory. It installs no cron job or systemd timer, and **automatic backups must not be scheduled until a restore has been rehearsed**.

**Isolated restore rehearsal** (touches nothing live):

```bash
bash scripts/agency/restore-rehearsal.sh /data/rma-portal/backups/rma-<stamp>.dump
```

It verifies the marker and SHA-256, starts one temporary `rma-portal-restore-rehearsal` container (no network, no ports, no bind mount — tmpfs data, dropped capabilities,
bounded resources, image `rma-portal-postgres:<V>`), restores the dump, prints table/`alembic`/workflow/user counts and removes only that container.

**Real restore** (documented, not scripted; needs an RMA-specific backup that passed the rehearsal **and** explicit human confirmation): stop `worker`, `web`, `api`; take a fresh backup;
`docker exec -i rma-portal-db pg_restore -U rma -d rma --clean --if-exists --no-owner --no-privileges --exit-on-error < /data/rma-portal/backups/rma-<stamp>.dump`;
run the migration one-shot (Stage 6) if the dump is older than the code; restart `api`, `web`, `worker` one by one.

## 9. Everyday operations (RMA only)

| Need | Command |
|---|---|
| See only RMA containers | `docker ps -a --filter "name=^rma-portal-" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'` or `docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml ps` |
| RMA logs only | `docker logs --since 1h --tail 200 rma-portal-worker` (or `-api`, `-web`, `-browser`, `-db`); worker cycles: `… 2>&1 \| grep -E 'stage=(synchronization\|workflow_sync)'` |
| RMA disk usage | `du -sh /data/rma-portal/* 2>/dev/null` (use `sudo` for the 0700 directories; read-only), `df -h /data`, `docker image ls 'rma-portal*'` |
| Stop / restart one service | `docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml stop worker` / `… restart worker` (name exactly one service) |
| Record before/after | `docker ps -a --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.CreatedAt}}' \| sort > <file>` before, and again after; `diff` the two |
| Prove other projects unchanged | the “Others unchanged?” command above; plus `docker compose ls -a` (project list and their `running(n)` counts must be as at Stage 2) |
| Verify published ports | `docker port rma-portal-web` → `8080/tcp -> 192.168.1.32:8480`; `docker port rma-portal-browser` → `6080/tcp -> 192.168.1.32:6081`; `docker port rma-portal-api` / `-db` → nothing; `ss -ltn \| grep -E ':(8480\|6081)\b'` |
| Verify bind-mount locations | `docker inspect --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' rma-portal-worker` (all `bind`, all under `/data/rma-portal`) |
| Worker synchronization summaries | the two SQL queries of Stage 13; `docker logs rma-portal-worker 2>&1 \| grep 'stage=synchronization'` |
| Confirm blocked write attempts stay zero | `docker logs rma-portal-worker 2>&1 \| grep -c 'blocked non-read-only OmegaFlow request'` → `0` (also for `rma-portal-browser`) |
| Resource use | `docker stats --no-stream rma-portal-db rma-portal-api rma-portal-worker rma-portal-web rma-portal-browser` (names given explicitly) |

Never use a command that selects all containers for a mutation, and never `docker inspect` a non-RMA container.

## 10. Assumptions to confirm live (not verifiable offline)

1. The pinned bases and Camoufox build fetch successfully on the VM build (already validated for `compose.prod.yaml`) and the bundle loads on Docker 29.7.2 with `overlay2`.
2. `docker load` restores the tags `rma-portal-postgres:<V>` etc. with the manifest's `archive_config_id` values (the loader verifies this).
3. The `ubuntu` user can run `docker` without `sudo`; `openssl`, `python3`, `ss`, `sha256sum` exist (preflight checks the last three).
4. `192.168.1.32` is assigned to the server NIC (preflight checks) and 8480/6081 are free at deployment time.
5. 3 GiB per Camoufox container and 2 CPUs are enough for a full 21-workflow cycle; if a cycle is OOM-killed, raise that service’s limit by reviewed change.
6. `pg_isready`, `psql`, `pg_dump`, `pg_restore` behave under the `--user`/tmpfs flags of the restore rehearsal exactly as on the VM (rehearse before relying on it).
7. The worker’s first cycle starting immediately at Stage 12 is acceptable (it is the code’s behaviour and is verified in Stage 13).
8. The pre-existing restarting/unhealthy Shexpert/Wexia containers are stable enough that the “Others unchanged?” identity check is meaningful (statuses are excluded from it on purpose).
