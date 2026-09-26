#!/usr/bin/env bash
# STRICTLY READ-ONLY preflight for the RMA deployment on the shared agency server.
#
#   scripts/agency/preflight.sh [--env-file F] [--compose-file F] [--storage-prepared]
#                               [--require-images] [--manifest M] [--bundle TAR] [--allow-ai]
#                               [--pre-config]
#
# It creates, writes, pulls, builds, starts, stops, restarts and prunes NOTHING, reads no
# other application's secrets or environment, and prints no secret value: RMA secrets are
# only tested with `grep -q` (presence / placeholder / length / charset), never captured.
# Existing containers are only listed by name and status.
#
# Exit code: 0 = no blocker (warnings allowed), 1 = at least one blocker.
# Pre-existing restarting/unhealthy containers of OTHER projects are WARNINGS: recorded,
# never repaired, never a reason to touch them.
#
# Stage usage: Stage 1 with --pre-config (no env file exists yet), after Stage 2 add --storage-prepared, after Stage 4 add
# --require-images --manifest <bundle manifest>.
set -uo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ENV_FILE="$RMA_DEFAULT_STORAGE_ROOT/config/.env"
COMPOSE_FILE="$HERE/../../compose.agency.yaml"
PRE_CONFIG=0; STORAGE_PREPARED=0; REQUIRE_IMAGES=0; MANIFEST=""; BUNDLE=""; ALLOW_AI=0
while [ $# -gt 0 ]; do
    case "$1" in
        --env-file) ENV_FILE="${2:?}"; shift ;;
        --compose-file) COMPOSE_FILE="${2:?}"; shift ;;
        --storage-prepared) STORAGE_PREPARED=1 ;;
        --require-images) REQUIRE_IMAGES=1 ;;
        --manifest) MANIFEST="${2:?}"; shift ;;
        --bundle) BUNDLE="${2:?}"; shift ;;
        --allow-ai) ALLOW_AI=1 ;;
        --pre-config) PRE_CONFIG=1 ;;
        -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

FAILS=0; WARNS=0
version=""; web_ip=""; novnc_ip=""
ok()   { echo "  [ OK ] $*"; }
warn() { echo "  [WARN] $*"; WARNS=$((WARNS + 1)); }
fail() { echo "  [FAIL] $*"; FAILS=$((FAILS + 1)); }
section() { echo; echo "== $*"; }
kib() { awk -v k="$1" 'BEGIN { printf "%.1f GiB", k / 1048576 }'; }

ROOT="$RMA_DEFAULT_STORAGE_ROOT"
echo "RMA agency preflight (read-only) -- $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---- host -------------------------------------------------------------------------------------------------
section "Host"
echo "  hostname : $(hostname)"
echo "  OS       : $( (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || uname -sr)"
arch="$(uname -m)"
if [ "$arch" = "x86_64" ]; then ok "architecture $arch (linux/amd64)"; else fail "architecture $arch: the bundle is linux/amd64"; fi
command -v python3 >/dev/null 2>&1 && ok "python3 available" || fail "python3 is required by the audit helpers"

# ---- docker -------------------------------------------------------------------------------------------------
section "Docker"
if ! command -v docker >/dev/null 2>&1; then
    fail "docker CLI not found"
elif ! docker_ver="$(docker version --format '{{.Server.Version}}' 2>/dev/null)" || [ -z "$docker_ver" ]; then
    fail "docker daemon is not reachable by this user"
else
    ok "docker daemon reachable, Engine $docker_ver"
    echo "  Compose  : $(docker compose version --short 2>/dev/null || echo unavailable)"
    docker compose version >/dev/null 2>&1 || fail "docker compose plugin missing"
    DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)"
    echo "  root dir : $DOCKER_ROOT   storage driver: $(docker info --format '{{.Driver}}' 2>/dev/null)"
    live="$(docker info --format '{{.LiveRestoreEnabled}}' 2>/dev/null)"
    echo "  live-restore: $live"
    if [ "$live" = "false" ]; then warn "live-restore is off: a Docker restart would stop every container; this deployment never restarts Docker"; fi
fi

# ---- disk -------------------------------------------------------------------------------------------------------
section "Disk space and inodes"
df_field() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {print $'"$2"'}'; }
dfi_field() { df -Pi "$1" 2>/dev/null | awk 'NR==2 {print $'"$2"'}'; }
need_root_kib=$((10 * 1048576))
if [ -n "$BUNDLE" ] && [ -f "$BUNDLE" ]; then need_root_kib=$(( $(stat -c %s "$BUNDLE") / 1024 * 3 )); fi
min_data_gib="${RMA_MIN_DATA_FREE_GIB:-50}"
for path in / /data; do
    if [ ! -d "$path" ]; then fail "$path does not exist"; continue; fi
    avail="$(df_field "$path" 4)"; usedpct="$(df_field "$path" 5)"; ifree="$(dfi_field "$path" 4)"
    echo "  $path: available $(kib "$avail"), use $usedpct, free inodes $ifree, source $(findmnt -no SOURCE,FSTYPE -T "$path" 2>/dev/null || echo unknown)"
    [ "${ifree:-0}" -gt 100000 ] 2>/dev/null && ok "$path: enough free inodes" || warn "$path: fewer than 100000 free inodes"
done
avail_root="$(df_field / 4)"
if [ "${avail_root:-0}" -ge "$need_root_kib" ]; then ok "root filesystem has room to load images (need $(kib "$need_root_kib"), 3x bundle or 10 GiB)"; else fail "root filesystem too small to load images: need $(kib "$need_root_kib"), have $(kib "${avail_root:-0}")"; fi
case "$(df_field / 5)" in 9[0-9]%|100%) warn "root filesystem is at $(df_field / 5) (pre-existing); RMA keeps ALL persistent data on /data" ;; esac
if [ -d /data ]; then
    if [ "$(stat -c %d /data)" = "$(stat -c %d /)" ]; then fail "/data is on the root filesystem (expected a separate filesystem)"; else ok "/data is a separate filesystem"; fi
    avail_data="$(df_field /data 4)"
    if [ "${avail_data:-0}" -ge $((min_data_gib * 1048576)) ]; then ok "/data has >= ${min_data_gib} GiB free"; else fail "/data has only $(kib "${avail_data:-0}") free (< ${min_data_gib} GiB)"; fi
    owner="$(stat -c '%u:%g %a' /data)"; echo "  /data owner/mode: $owner"
fi

# ---- existing workloads (names and status only) ----------------------------------------------------
section "Existing Docker workloads (read-only listing)"
if command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1; then
    echo "  Compose projects:"; docker compose ls -a 2>/dev/null | sed 's/^/    /'
    total="$(docker ps -a -q | wc -l)"
    listing="$(docker ps -a --format '{{.Names}}|{{.Status}}|{{.Label "com.docker.compose.project"}}')"
    restarting="$(printf '%s\n' "$listing" | awk -F'|' '$2 ~ /^Restarting/ {print $1" ("$3")"}')"
    unhealthy="$(printf '%s\n' "$listing" | awk -F'|' '$2 ~ /\(unhealthy\)/ {print $1" ("$3")"}')"
    echo "  containers total: $total, restarting: $(printf '%s' "$restarting" | grep -c . || true), unhealthy: $(printf '%s' "$unhealthy" | grep -c . || true)"
    [ -z "$restarting" ] || { warn "PRE-EXISTING restarting containers (recorded, never touched):"; printf '%s\n' "$restarting" | sed 's/^/           /'; }
    [ -z "$unhealthy" ] || { warn "PRE-EXISTING unhealthy containers (recorded, never touched):"; printf '%s\n' "$unhealthy" | sed 's/^/           /'; }
    for name in "${RMA_CONTAINERS[@]}"; do
        if printf '%s\n' "$listing" | cut -d'|' -f1 | grep -qx "$name"; then fail "container name collision: $name already exists"; fi
    done
    if docker compose ls -a --format json 2>/dev/null | grep -q '"Name":"rma-portal"'; then fail "a Compose project named rma-portal already exists"; fi
    if docker network ls --format '{{.Name}}' | grep -qx "$RMA_NETWORK"; then fail "network name collision: $RMA_NETWORK already exists"; else ok "no rma-portal container or network collision"; fi
fi

# ---- ports -------------------------------------------------------------------------------------------------------
section "Ports"
web_port=8480; novnc_port=6081
if [ -f "$ENV_FILE" ]; then
    web_port="$(env_value "$ENV_FILE" RMA_WEB_PORT 8480)"; novnc_port="$(env_value "$ENV_FILE" RMA_NOVNC_PORT 6081)"
fi
if command -v ss >/dev/null 2>&1; then
    for port in "$web_port" "$novnc_port"; do
        if ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -qE ":${port}\$"; then fail "port $port is already listening on this host"; else ok "port $port is free"; fi
    done
else
    fail "ss is not available: cannot verify that ports $web_port and $novnc_port are free"
fi

# ---- env file -------------------------------------------------------------------------------------------------
section "Environment file (names and shapes only; no value is printed)"
if [ "$PRE_CONFIG" -eq 1 ]; then
    echo "  skipped (--pre-config): the env file is created in Stage 3"
elif [ ! -f "$ENV_FILE" ]; then
    fail "env file not found: $ENV_FILE"
else
    stat_line="$(stat -c '%u:%g %a' "$ENV_FILE")"
    echo "  $ENV_FILE  owner/mode: $stat_line"
    [ "${stat_line##* }" = "600" ] && ok "env file mode is 0600" || fail "env file mode must be 0600"
    [ "$(stat -c %u "$ENV_FILE")" = "$(id -u)" ] && ok "env file is owned by the current user" || fail "env file must be owned by the operator running the deployment"
    secret_check() {  # KEY MIN_LEN
        local key="$1" min="$2"
        if ! grep -qE "^${key}=.+" "$ENV_FILE"; then fail "$key is missing or empty"; return; fi
        if grep -qiE "^${key}=.*(change-?me|placeholder|example|replace|todo|xxxx|password123)" "$ENV_FILE"; then fail "$key still holds a placeholder"; return; fi
        if ! grep -qE "^${key}=[A-Za-z0-9._~-]{${min},}\$" "$ENV_FILE"; then fail "$key must be >= $min characters of [A-Za-z0-9._~-] (URL/compose-safe)"; return; fi
        ok "$key is set ($min+ safe characters)"
    }
    secret_check POSTGRES_PASSWORD 16
    secret_check RMA_SESSION_SECRET 32
    secret_check RMA_VNC_PASSWORD 8
    version="$(env_value "$ENV_FILE" RMA_VERSION)"
    if printf '%s' "$version" | grep -qE '^[0-9a-f]{12}$'; then ok "RMA_VERSION=$version is a 12-character commit tag"; else fail "RMA_VERSION must be a 12-character git commit tag (got '${version:-empty}')"; fi
    storage="$(env_value "$ENV_FILE" RMA_STORAGE_ROOT)"
    if [ "$storage" = "$ROOT" ]; then ok "RMA_STORAGE_ROOT=$storage"; else fail "RMA_STORAGE_ROOT must be $ROOT (got '${storage:-empty}')"; fi
    web_ip="$(env_value "$ENV_FILE" RMA_WEB_BIND_IP)"
    novnc_ip="$(env_value "$ENV_FILE" RMA_NOVNC_BIND_IP)"
    if printf '%s' "$web_ip" | grep -qE '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)[0-9.]+$'; then
        ok "RMA_WEB_BIND_IP=$web_ip is a private LAN address"
        if command -v ip >/dev/null 2>&1 && ! ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | grep -qx "$web_ip"; then fail "$web_ip is not assigned to this host"; fi
    else
        fail "RMA_WEB_BIND_IP must be the private LAN address (never 0.0.0.0, loopback or Tailscale 100.x); got '${web_ip:-empty}'"
    fi
    [ "$novnc_ip" = "127.0.0.1" ] && ok "RMA_NOVNC_BIND_IP=127.0.0.1" || fail "RMA_NOVNC_BIND_IP must be 127.0.0.1 (got '${novnc_ip:-empty}')"
    ai="$(env_value "$ENV_FILE" RMA_OLLAMA_ENABLED false)"
    if [ "$ai" = "false" ]; then ok "RMA_OLLAMA_ENABLED=false (no AI in the initial deployment)"; elif [ "$ALLOW_AI" -eq 1 ]; then warn "AI enabled (--allow-ai)"; else fail "RMA_OLLAMA_ENABLED must be false for the initial deployment"; fi
    [ "$(env_value "$ENV_FILE" RMA_COOKIE_SECURE false)" = "false" ] && ok "RMA_COOKIE_SECURE=false (trusted-LAN HTTP pilot)" || warn "RMA_COOKIE_SECURE is not false: correct only when the portal is served over HTTPS"
    echo "  RMA_POLL_INTERVAL_SECONDS=$(env_value "$ENV_FILE" RMA_POLL_INTERVAL_SECONDS 3600)"
fi

# ---- resolved compose -----------------------------------------------------------------------------------
section "Resolved Compose configuration"
image_refs=()
if [ "$PRE_CONFIG" -eq 1 ]; then
    echo "  skipped (--pre-config)"
elif [ -f "$ENV_FILE" ] && [ -f "$COMPOSE_FILE" ] && command -v docker >/dev/null 2>&1; then
    if cfg_err="$(compose_clean --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config -q 2>&1 >/dev/null)"; then
        ok "compose config resolves (project rma-portal)"
    else
        fail "compose config does not resolve: $(printf '%s' "$cfg_err" | sed 's/=.*//' | head -2 | tr '
' ' ')"
    fi
    ai_flag=""; [ "$ALLOW_AI" -eq 1 ] && ai_flag="--allow-ai"
    audit="$(compose_clean --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --format json 2>/dev/null         | python3 -B "$HERE/compose_audit.py" --storage-root "$ROOT" --web-ip "$web_ip" --web-port "$web_port"             --novnc-ip "$novnc_ip" --novnc-port "$novnc_port" $ai_flag)" || true
    while IFS= read -r line; do
        case "$line" in
            OK:*) ok "${line#OK: }" ;;
            WARN:*) warn "${line#WARN: }" ;;
            FAIL:*) fail "${line#FAIL: }" ;;
            IMAGE\ *) image_refs+=("${line#IMAGE }") ;;
        esac
    done <<<"${audit:-}"
    [ -n "${audit:-}" ] || fail "compose audit produced no result"
else
    fail "cannot audit compose: need the env file, $COMPOSE_FILE and docker"
fi

# ---- images ----------------------------------------------------------------------------------------------------
section "Images (never pulled or built)"
if [ "$PRE_CONFIG" -eq 1 ]; then
    echo "  skipped (--pre-config): images are loaded in Stage 4"
elif [ "${#image_refs[@]}" -gt 0 ]; then
    for ref in "${image_refs[@]}"; do
        if id="$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null)"; then
            ok "$ref -> $id ($(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$ref"))"
            [ "$(docker image inspect --format '{{.Architecture}}' "$ref")" = "amd64" ] || fail "$ref is not amd64"
            rev="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$ref" 2>/dev/null)"
            if [ -n "$rev" ] && [ "${rev#"$version"}" = "$rev" ]; then fail "$ref revision label $rev does not match RMA_VERSION"; fi
            if [ -n "$MANIFEST" ] && [ -f "$MANIFEST" ]; then
                want="$(python3 -B "$HERE/manifest.py" images "$MANIFEST" | awk -v r="$ref" '$1 == r {print $2}')"
                if [ -z "$want" ]; then fail "$ref is not listed in the manifest"; elif [ "$want" = "$id" ]; then ok "$ref image ID matches the manifest"; else fail "$ref image ID differs from the manifest"; fi
            fi
        elif [ "$REQUIRE_IMAGES" -eq 1 ]; then
            fail "image not loaded: $ref"
        else
            warn "image not loaded yet: $ref (expected before Stage 4)"
        fi
    done
else
    warn "no image list available (compose audit failed)"
fi

# ---- storage --------------------------------------------------------------------------------------------------
section "Storage paths"
case "${DOCKER_ROOT:-/var/lib/docker}" in "$ROOT"|"$ROOT"/*) fail "$ROOT overlaps Docker's root" ;; esac
case "$ROOT" in /var/lib/docker*) fail "storage must not be under /var/lib/docker" ;; esac
if [ "$STORAGE_PREPARED" -eq 0 ]; then
    if [ -e "$ROOT" ]; then fail "storage path collision: $ROOT already exists (use --storage-prepared after Stage 2)"; else ok "$ROOT does not exist yet (Stage 2 creates it)"; fi
else
    if [ ! -d "$ROOT" ]; then fail "$ROOT is missing: run Stage 2"; else
        got="$(stat -c '%u:%g %a' "$ROOT")"; [ "$got" = "1000:1000 750" ] && ok "$ROOT 1000:1000 0750" || fail "$ROOT is $got, expected 1000:1000 750"
        for row in "${RMA_STORAGE_LAYOUT[@]}"; do
            read -r name uid gid mode <<<"$row"
            if [ ! -d "$ROOT/$name" ]; then fail "$ROOT/$name is missing"; continue; fi
            got="$(stat -c '%u:%g %a' "$ROOT/$name")"
            [ "$got" = "$uid:$gid ${mode#0}" ] && ok "$ROOT/$name $uid:$gid $mode" || fail "$ROOT/$name is $got, expected $uid:$gid ${mode#0}"
        done
    fi
fi

echo
echo "== Result: $FAILS blocker(s), $WARNS warning(s)"
[ "$FAILS" -eq 0 ] || { echo "Do not continue: fix every [FAIL] first."; exit 1; }
echo "No blocker. Review every [WARN]; pre-existing unhealthy containers of other projects are recorded, not repaired."
