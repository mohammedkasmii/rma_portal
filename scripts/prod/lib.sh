#!/usr/bin/env bash
# Shared helpers for the production scripts. Source it; never run it directly.
# Everything targets the `rma-portal` compose project only.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_ROOT/compose.prod.yaml}"

compose() {
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

die() {
    echo "error: $*" >&2
    exit 1
}

require_stack_config() {
    [ -f "$ENV_FILE" ] || die "$ENV_FILE not found (copy .env.example to .env first)"
    command -v docker >/dev/null 2>&1 || die "docker is not installed"
    compose config -q || die "compose.prod.yaml does not validate with $ENV_FILE"
}

# Read one KEY=value from the env file without sourcing it (values may contain shell characters).
env_value() {
    local key="$1" default="${2:-}" line
    line="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 || true)"
    if [ -n "$line" ]; then
        printf '%s\n' "${line#*=}"
    else
        printf '%s\n' "$default"
    fi
}
