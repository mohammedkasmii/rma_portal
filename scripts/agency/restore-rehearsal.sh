#!/usr/bin/env bash
# Rehearse a restore in a THROW-AWAY, fully isolated PostgreSQL container. Touches nothing live.
#
#   scripts/agency/restore-rehearsal.sh /data/rma-portal/backups/rma-<stamp>.dump [--env-file F]
#
# Verifies the completion marker and SHA-256, then starts ONE temporary container named
# rma-portal-restore-rehearsal: network none, no published port, no bind mount (its data
# directory is a tmpfs), dropped capabilities, bounded CPU/memory/PIDs, image
# rma-portal-postgres:<RMA_VERSION> (already loaded, never pulled). It restores the dump
# into it, prints table counts, and removes that one container (and its tmpfs) on exit.
# The live database, its data directory and every other container are never touched.
# This is the check to pass BEFORE any automatic backup is scheduled.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

ENV_FILE="$RMA_DEFAULT_STORAGE_ROOT/config/.env"
DUMP=""
while [ $# -gt 0 ]; do
    case "$1" in
        --env-file) ENV_FILE="${2:?}"; shift ;;
        -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*) die "unknown argument: $1" ;;
        *) DUMP="$1" ;;
    esac
    shift
done
[ -n "$DUMP" ] || die "usage: restore-rehearsal.sh <rma-stamp.dump>"
[ -f "$DUMP" ] || die "dump not found: $DUMP"
base="${DUMP%.dump}"
[ -f "$base.complete" ] || die "no completion marker: the backup set is incomplete"
(cd "$(dirname "$DUMP")" && sha256sum -c "$(basename "$DUMP").sha256") || die "checksum mismatch: refusing to restore"

VERSION="$(env_value "$ENV_FILE" RMA_VERSION)"
printf '%s' "$VERSION" | grep -qE '^[0-9a-f]{12}$' || die "RMA_VERSION in $ENV_FILE must be a 12-character commit"
IMAGE="rma-portal-postgres:$VERSION"
NAME="rma-portal-restore-rehearsal"
docker image inspect "$IMAGE" >/dev/null 2>&1 || die "image $IMAGE is not loaded (nothing is pulled)"
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then die "$NAME already exists; remove it yourself after checking it is yours"; fi

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== starting $NAME (isolated: no network, no ports, tmpfs data)"
docker run -d --name "$NAME" --pull never --network none --user 70:70 \
    --cap-drop ALL --security-opt no-new-privileges:true \
    --cpus 1 --memory 2g --memory-swap 2g --pids-limit 256 \
    --tmpfs /var/lib/postgresql/data:rw,uid=70,gid=70,mode=0700 \
    --tmpfs /var/run/postgresql:rw,uid=70,gid=70,mode=0755 --tmpfs /tmp \
    -e POSTGRES_USER=rma -e POSTGRES_DB=rma -e POSTGRES_HOST_AUTH_METHOD=trust \
    "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
    if docker exec "$NAME" pg_isready -U rma -d rma >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
done
[ "${ready:-0}" -eq 1 ] || die "the rehearsal database did not become ready"

echo "== restoring $(basename "$DUMP")"
docker exec -i "$NAME" pg_restore -U rma -d rma --no-owner --no-privileges --exit-on-error <"$DUMP"

echo "== restored contents"
docker exec "$NAME" psql -U rma -d rma -Atc "select 'tables: '||count(*) from information_schema.tables where table_schema='public'"
docker exec "$NAME" psql -U rma -d rma -Atc "select 'alembic head: '||version_num from alembic_version"
docker exec "$NAME" psql -U rma -d rma -Atc "select 'workflows: '||count(*) from workflows"
docker exec "$NAME" psql -U rma -d rma -Atc "select 'users: '||count(*) from users"
echo "== rehearsal succeeded; removing only $NAME"
