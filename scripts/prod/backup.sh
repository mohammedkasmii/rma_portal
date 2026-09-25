#!/usr/bin/env bash
# Consistent PostgreSQL backup of the RMA database (custom format, verified, pruned).
#
#   scripts/prod/backup.sh                 # database only
#   scripts/prod/backup.sh --with-session  # + the captured OmegaFlow session (SENSITIVE, 0600)
#
# Never touches any container or volume outside the rma-portal project. The dump contains
# dossiers, notes and password hashes: keep the backup directory private (mode 0700).
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

with_session=0
for arg in "$@"; do
    case "$arg" in
        --with-session) with_session=1 ;;
        -h|--help) sed -n "2,9p" "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "unknown argument: $arg" ;;
    esac
done

require_stack_config
BACKUP_DIR="${BACKUP_DIR:-$(env_value BACKUP_DIR ./backups)}"
RETENTION="${BACKUP_RETENTION:-$(env_value BACKUP_RETENTION 14)}"
case "$BACKUP_DIR" in /*) ;; *) BACKUP_DIR="$REPO_ROOT/$BACKUP_DIR" ;; esac
case "$RETENTION" in ''|*[!0-9]*) die "BACKUP_RETENTION must be a number" ;; esac

umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="$BACKUP_DIR/rma-$stamp.dump"

compose exec -T db pg_dump -U rma -d rma --format=custom --no-owner --no-privileges >"$dump.partial"
# A dump that pg_restore cannot list is not a backup.
compose exec -T db pg_restore --list <"$dump.partial" >/dev/null || {
    rm -f "$dump.partial"
    die "the dump failed verification and was discarded"
}
mv "$dump.partial" "$dump"
(cd "$BACKUP_DIR" && sha256sum "$(basename "$dump")" >"$(basename "$dump").sha256")
echo "database backup: $dump ($(wc -c <"$dump") bytes)"

if [ "$with_session" -eq 1 ]; then
    session="$BACKUP_DIR/rma-session-$stamp.tar.gz"
    compose run --rm --no-deps -T --entrypoint tar worker \
        -C /var/lib/rma-poc/state -cz omegaflow-session-state.json >"$session.partial" \
        || { rm -f "$session.partial"; die "no captured session to back up (log in first)"; }
    mv "$session.partial" "$session"
    echo "session backup:  $session (contains cookies: never share it)"
fi

# Keep only the newest RETENTION database dumps (with their checksums and session archives).
mapfile -t old < <(ls -1t "$BACKUP_DIR"/rma-*.dump 2>/dev/null | tail -n +"$((RETENTION + 1))")
for file in "${old[@]}"; do
    base="${file%.dump}"
    rm -f -- "$file" "$file.sha256" "${base/rma-/rma-session-}.tar.gz"
    echo "pruned: $(basename "$file")"
done
