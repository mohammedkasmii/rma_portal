#!/usr/bin/env bash
# Shared helpers for the agency-server scripts. Source it; never run it directly.
# Everything targets the `rma-portal` project only: nothing here selects "all containers".

RMA_PROJECT="rma-portal"
RMA_NETWORK="rma-portal-net"
RMA_DEFAULT_STORAGE_ROOT="/data/rma-portal"
RMA_CONTAINERS=(rma-portal-db rma-portal-migrate rma-portal-api rma-portal-worker rma-portal-web rma-portal-browser)
RMA_SERVICES=(db migrate api worker web browser)

# name uid gid mode -- the single source of truth for the storage layout (the runbook lists
# the same table). uid/gid are the users baked into the images, verified with `id`:
# postgres 70:70 in postgres:16-alpine, rma 10001:10001 in the runtime image
# (docker/prod/Dockerfile, RMA_UID=10001). 1000:1000 is the `ubuntu` operator that owns /data
# and runs these scripts.
RMA_STORAGE_LAYOUT=(
    "postgres 70 70 0700"
    "app-data 10001 10001 0750"
    "logs 10001 10001 0750"
    "browser-profile 10001 10001 0700"
    "session-state 10001 10001 0700"
    "backups 1000 1000 0700"
    "releases 1000 1000 0750"
    "image-bundles 1000 1000 0750"
    "config 1000 1000 0700"
)
RMA_ROOT_OWNER="1000 1000 0750"

die() { echo "error: $*" >&2; exit 1; }
note() { echo "$*"; }

# Read one KEY=value from an env file without sourcing it. Never echo the result of a secret key.
env_value() {
    local file="$1" key="$2" default="${3:-}" line
    line="$(grep -E "^${key}=" "$file" 2>/dev/null | tail -n 1 || true)"
    if [ -n "$line" ]; then printf '%s\n' "${line#*=}"; else printf '%s\n' "$default"; fi
}

# Compose runs must not inherit RMA_*/POSTGRES_*/COMPOSE_* from the operator's shell: the
# env file is the only source of truth.
compose_clean() {
    (
        for v in $(compgen -e | grep -E '^(RMA_|POSTGRES_|COMPOSE_|OMEGAFLOW_)' || true); do unset "$v"; done
        docker compose "$@"
    )
}
