# Ubuntu Docker browser PoC (Phase 0)

Feasibility only: Camoufox + Xvfb + openbox + x11vnc + noVNC + persistent
OmegaFlow session state, in one isolated container. It does not touch the
Windows app, the Garage agréé reader, or any other stack (incl. `wexia-*`).

| Item | Value |
|---|---|
| Compose project / service / container | `rma-poc` / `rma-poc-browser` / `rma-poc-browser` |
| Network | `rma-poc-net` |
| Volumes | `rma-poc-profile` (Firefox profile), `rma-poc-state` (session-state JSON, lock, `logs/poc.log`) |
| Published port | **6080 only** (noVNC). VNC :5900 is loopback-inside-container. |
| noVNC URL | `http://192.168.1.180:6080/vnc.html` |
| Runtime user | `pocuser` (uid 10001); no privileged mode, no host network, `cap_drop: ALL` |
| Base image | `ubuntu:26.04` pinned by digest; Python 3.14 + deps from the repo `uv.lock` |

The noVNC desktop is **password protected**: set `POC_VNC_PASSWORD` (only the
first 8 characters count in VNC auth). It is never stored in the repo or image.

## Commands (run in the Ubuntu VM, repo root, branch `ubuntu-docker-poc`)

```bash
git pull
export POC_VNC_PASSWORD='choose-a-password'      # or put it in an untracked .env
C="docker compose -f compose.poc.yaml"
```

**1. Build**
```bash
$C build 2>&1 | tee /tmp/rma-poc-build.log
grep -A3 "camoufox" /tmp/rma-poc-build.log | tail -8    # browser build actually fetched
```

**2. Start noVNC**
```bash
$C up -d
# Windows browser: http://192.168.1.180:6080/vnc.html  (enter the VNC password)
```

**3. Inspect health** (wait for `healthy`, ~20 s)
```bash
docker inspect --format '{{.State.Health.Status}}' rma-poc-browser
$C ps
$C exec rma-poc-browser python -m rma_poc status      # count-only; no state yet is expected
```

**4. Launch visible login** (foreground; the window appears in noVNC; log in manually)
```bash
$C exec rma-poc-browser python -m rma_poc login --timeout 900
```

**5. Capture the session** – automatic. Once `#view_1874` is visible on 3
consecutive polls the command captures cookies + localStorage + sessionStorage
(OmegaFlow origin), writes atomically (temp file + `os.replace` + fsync), closes
the browser (bounded), and prints `RESULT=CAPTURED`. Confirm:
```bash
$C exec rma-poc-browser python -m rma_poc status
```

**6. Restart / recreate the container**
```bash
$C restart
$C up -d --force-recreate
```

**7. Headless verification**
```bash
$C exec -T rma-poc-browser python -m rma_poc verify; echo "exit=$?"
# stricter: throw-away profile, proves the state file alone is enough
$C exec -T rma-poc-browser python -m rma_poc verify --fresh-profile; echo "exit=$?"
```

**8. Restart the VM, verify again** (container has `restart: unless-stopped`)
```bash
sudo reboot
# after reconnecting:
systemctl is-enabled docker
docker inspect --format '{{.State.Health.Status}}' rma-poc-browser
export POC_VNC_PASSWORD='...'; cd <repo>; C="docker compose -f compose.poc.yaml"
$C exec -T rma-poc-browser python -m rma_poc verify; echo "exit=$?"
```

**9. Sanitized logs** (counts/outcomes only; no values, HTML, screenshots)
```bash
$C logs --tail 100
$C exec rma-poc-browser tail -n 200 /var/lib/rma-poc/state/logs/poc.log
```

**10. Stop, keep volumes**
```bash
$C stop          # or: $C down   (both keep the named volumes)
```

**11. Reset everything (deletes the session and profile – intentional only)**
```bash
$C down --volumes
docker volume ls --filter name=rma-poc            # should list nothing
```

## Exit codes / `RESULT=` line

| Code | Result | Commands |
|---|---|---|
| 0 | `CAPTURED` / `READY` | login / verify |
| 10 | `AUTH_REQUIRED` (login form, session revalidation, or no saved state) | verify, status |
| 13 | `LOGIN_CANCELLED` (window closed first; saved state untouched) | login |
| 20 | `TIMEOUT` | login, verify |
| 21 | `BROWSER_ERROR` | login, verify |
| 22 | `CAPTURE_FAILED` (saved state untouched) | login |
| 30 | `PROFILE_BUSY` (another command holds the profile lock) | login, verify |
| 2 | `USAGE` (e.g. no `DISPLAY`) | login |

## Behaviour notes

- **Reused from the app:** `session_state` (capture/save/load/restore),
  `is_authenticated_view_present` (`#view_1874`), `detect_auth_required`,
  `CamoufoxPortalReader._read_only_route` (blocks PUT/PATCH/DELETE and
  `POST …/records` to OmegaFlow/Knack), the profile lock, and the same Camoufox
  launch options (`os="windows"` by default; override with `POC_CAMOUFOX_OS`).
- **Atomicity:** capture is refused (state untouched) if the view vanished
  or the snapshot is empty; failed/cancelled/timed-out login never writes.
- **Bounded:** login has `--timeout`; verify has `--timeout`; browser start (90 s) and
  close (30 s) are bounded, and a stalled close SIGKILLs only the command's own
  process subtree. Nothing relies on a browser close event alone.
- **Logging:** stage names, exit reasons, exception *types*, and counts only.
- **Config env (compose):** `POC_OMEGAFLOW_BASE_URL`, `POC_OMEGAFLOW_START_ROUTE`,
  `POC_LOCALE`, `POC_TIMEZONE`, `POC_SCREEN`.
- **Freeze the browser build:** after the first build, put the version printed
  by `camoufox version` into the `CAMOUFOX_BROWSER_SPEC` build arg in
  `docker/poc-browser/Dockerfile` (empty = newest build the locked
  camoufox 0.5.6 / playwright 1.62 pair accepts, minimum `beta.30`).

## Troubleshooting

- `docker compose ... up` says `POC_VNC_PASSWORD` is required → export it first.
- Health stays `starting`/`unhealthy` → `$C logs --tail 50` (Xvfb/x11vnc/websockify lines).
- Browser fails to launch (sandbox/userns) → note the exception type from
  `poc.log` and report it; do **not** switch to `privileged`.
