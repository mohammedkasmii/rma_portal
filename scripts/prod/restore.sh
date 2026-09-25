#!/usr/bin/env bash
# Restore the RMA database from a backup made by backup.sh.
#
#   scripts/prod/restore.sh backups/rma-20260925T120000Z.dump [--yes]
#
# Stops the API, worker and web UI, replaces the database contents (pg_restore --clean),
# re-applies the migrations (so an older dump is upgraded) and starts everything again.
# This is destructive: the current database is overwritten. Nothing outside the rma-portal
# compose project is touched.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

dump=""
assume_yes=0
for arg in "$@"; do
    case "$arg" in
        --yes) assume_yes=1 ;;
        -h|--help) sed -n "2,10p" "${BASH_SOURCE[0]}"; exit 0 ;;
        -*) die "unknown argument: $arg" ;;
        *) dump="$arg" ;;
    esac
done
[ -n "$dump" ] || die "usage: restore.sh <backup.dump> [--yes]"
[ -f "$dump" ] || die "backup file not found: $dump"
if [ -f "$dump.sha256" ]; then
    (cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256") || die "checksum mismatch: refusing to restore"
fi

require_stack_config
if [ "$assume_yes" -ne 1 ]; then
    echo "This OVERWRITES the current RMA database with: $dump"
    read -r -p "Type RESTORE to continue: " answer
    [ "$answer" = "RESTORE" ] || die "aborted"
fi

echo "== stopping api, worker and web"
compose stop web api worker
compose up -d db
compose exec -T db pg_restore --list <"$dump" >/dev/null || die "not a valid pg_dump custom-format file"

echo "== restoring"
compose exec -T db pg_restore -U rma -d rma --clean --if-exists --no-owner --no-privileges --exit-on-error <"$dump"

echo "== migrating (upgrades an older dump) and restarting"
compose run --rm migrate
compose up -d
echo "restore complete; run scripts/prod/check.sh"
