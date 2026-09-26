#!/usr/bin/env bash
# Manual, consistent backup of the RMA PostgreSQL database on the agency server.
#
#   scripts/agency/backup.sh [--env-file F] [--compose-file F] [--with-session]
#
# Writes ONLY inside /data/rma-portal/backups (mode 0700), for each run <stamp>:
#   rma-<stamp>.dump          pg_dump custom format, verified with pg_restore --list
#   rma-<stamp>.dump.sha256   SHA-256
#   rma-<stamp>.manifest      timestamp, RMA_VERSION, sizes, checksums (no secret, no .env)
#   rma-<stamp>.complete      atomic completion marker, created LAST; a set without it is incomplete
# --with-session also writes rma-session-<stamp>.tar.gz (+ .sha256): the captured OmegaFlow
# session cookies. SENSITIVE: whoever holds it can act as the OmegaFlow account. Never copy it
# off the server unencrypted.
# Retention (BACKUP_RETENTION newest complete sets, default 14) removes only files named
# rma-*/rma-session-* inside the backup directory. Nothing else is ever deleted.
# This script installs no cron job and no systemd timer, and reads no .env value except
# RMA_VERSION, RMA_STORAGE_ROOT and BACKUP_RETENTION.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

ENV_FILE="$RMA_DEFAULT_STORAGE_ROOT/config/.env"
COMPOSE_FILE="$HERE/../../compose.agency.yaml"
WITH_SESSION=0
while [ $# -gt 0 ]; do
    case "$1" in
        --env-file) ENV_FILE="${2:?}"; shift ;;
        --compose-file) COMPOSE_FILE="${2:?}"; shift ;;
        --with-session) WITH_SESSION=1 ;;
        -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done
[ -f "$ENV_FILE" ] || die "env file not found: $ENV_FILE"
[ -f "$COMPOSE_FILE" ] || die "compose file not found: $COMPOSE_FILE"

ROOT="$(env_value "$ENV_FILE" RMA_STORAGE_ROOT)"
[ "$ROOT" = "$RMA_DEFAULT_STORAGE_ROOT" ] || die "RMA_STORAGE_ROOT must be $RMA_DEFAULT_STORAGE_ROOT"
BACKUP_DIR="$ROOT/backups"
[ -d "$BACKUP_DIR" ] && [ ! -L "$BACKUP_DIR" ] && [ "$(realpath "$BACKUP_DIR")" = "$BACKUP_DIR" ] || die "$BACKUP_DIR is missing or not a plain directory (run Stage 2)"
RETENTION="$(env_value "$ENV_FILE" BACKUP_RETENTION 14)"
case "$RETENTION" in ''|*[!0-9]*|0) die "BACKUP_RETENTION must be a positive number" ;; esac
VERSION="$(env_value "$ENV_FILE" RMA_VERSION)"

compose() { compose_clean --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

health="$(docker inspect --format '{{.State.Health.Status}}' rma-portal-db 2>/dev/null || echo missing)"
[ "$health" = "healthy" ] || die "rma-portal-db is not healthy (status: $health)"

umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="$BACKUP_DIR/rma-$stamp"
[ ! -e "$base.complete" ] || die "a backup with this timestamp already exists"
cleanup() { rm -f -- "$base.dump.partial" "$BACKUP_DIR/rma-session-$stamp.tar.gz.partial"; }
trap cleanup EXIT

compose exec -T db pg_dump -U rma -d rma --format=custom --no-owner --no-privileges >"$base.dump.partial"
# A dump that pg_restore cannot list is not a backup.
compose exec -T db pg_restore --list <"$base.dump.partial" >/dev/null || die "the dump failed verification and was discarded"
mv "$base.dump.partial" "$base.dump"
(cd "$BACKUP_DIR" && sha256sum "rma-$stamp.dump" >"rma-$stamp.dump.sha256")
echo "database dump : $base.dump ($(stat -c %s "$base.dump") bytes)"

session_line=""
if [ "$WITH_SESSION" -eq 1 ]; then
    session="$BACKUP_DIR/rma-session-$stamp.tar.gz"
    compose run --rm --no-deps -T --entrypoint tar worker \
        -C /var/lib/rma-poc/state -cz omegaflow-session-state.json >"$session.partial" \
        || die "no captured session to back up (Stage 11 not done?)"
    mv "$session.partial" "$session"
    (cd "$BACKUP_DIR" && sha256sum "rma-session-$stamp.tar.gz" >"rma-session-$stamp.tar.gz.sha256")
    session_line="session_archive=rma-session-$stamp.tar.gz (SENSITIVE: contains OmegaFlow cookies)"
    echo "session backup: $session (SENSITIVE)"
fi

{
    echo "created_utc=$stamp"
    echo "rma_version=$VERSION"
    echo "dump=rma-$stamp.dump"
    echo "dump_bytes=$(stat -c %s "$base.dump")"
    echo "dump_sha256=$(cut -d' ' -f1 "$base.dump.sha256")"
    echo "with_session=$WITH_SESSION"
    [ -z "$session_line" ] || echo "$session_line"
} >"$base.manifest"
# Atomic completion marker: written last, after every other file is in place.
: >"$base.complete.partial" && mv "$base.complete.partial" "$base.complete"
echo "backup complete: rma-$stamp"

# Retention: strictly inside $BACKUP_DIR, strictly by the rma-<stamp> naming scheme.
mapfile -t stamps < <(cd "$BACKUP_DIR" && ls -1 rma-*.complete 2>/dev/null | sed 's/\.complete$//' | sort -r | tail -n +"$((RETENTION + 1))")
for old in "${stamps[@]}"; do
    printf '%s' "$old" | grep -qE '^rma-[0-9]{8}T[0-9]{6}Z$' || continue
    s="${old#rma-}"
    rm -f -- "$BACKUP_DIR/$old.complete"
    rm -f -- "$BACKUP_DIR/$old.dump" "$BACKUP_DIR/$old.dump.sha256" "$BACKUP_DIR/$old.manifest" \
        "$BACKUP_DIR/rma-session-$s.tar.gz" "$BACKUP_DIR/rma-session-$s.tar.gz.sha256"
    echo "retention: removed backup set $old"
done
