#!/usr/bin/env bash
# Create the RMA persistent-storage directories on the agency server.
#
#   scripts/agency/prepare-storage.sh                 # DRY-RUN: print the exact mutations, change nothing
#   sudo scripts/agency/prepare-storage.sh --apply    # perform them (root is needed to chown to 70 / 10001)
#   scripts/agency/prepare-storage.sh --root /data/other-name   # another single-level /data/<name>
#
# Creates only the storage root and its nine documented subdirectories, each with an explicit
# owner and mode (see docs/agency-production-deployment.md, "Persistent storage"). It never
# recurses, never touches /data itself, never overwrites files, and is idempotent.
# It starts no container and changes no firewall, web-server, Docker or secret.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ROOT="$RMA_DEFAULT_STORAGE_ROOT"
APPLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --root) [ $# -ge 2 ] || die "--root needs a value"; ROOT="$2"; shift ;;
        -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

# --- refuse dangerous roots ---------------------------------------------------------------
[ -n "$ROOT" ] || die "refusing an empty storage root"
case "$ROOT" in /*) ;; *) die "refusing a relative storage root: $ROOT" ;; esac
case "$ROOT" in
    *..*|*//*|*/) die "refusing a non-canonical storage root: $ROOT" ;;
esac
case "$ROOT" in
    /|/data|/var|/var/lib|/var/lib/docker|/var/lib/docker/*|/usr|/etc|/home|/root|/opt|/tmp|/boot|/bin|/sbin|/lib|/srv|/mnt|/media)
        die "refusing a dangerous storage root: $ROOT" ;;
esac
# Exactly one level under /data: /data/rma-portal (default) or /data/<name> for rehearsals.
case "$ROOT" in
    /data/*/*) die "the storage root must be a single directory directly under /data: $ROOT" ;;
    /data/?*) ;;
    *) die "the storage root must be /data/<name> (default $RMA_DEFAULT_STORAGE_ROOT): $ROOT" ;;
esac
# No symlink may be involved (a link could redirect chown/chmod elsewhere).
for p in /data "$ROOT"; do
    if [ -L "$p" ]; then die "refusing a symlink: $p"; fi
done
if [ -e "$ROOT" ] && [ "$(realpath "$ROOT")" != "$ROOT" ]; then
    die "refusing a storage root that resolves elsewhere: $ROOT -> $(realpath "$ROOT")"
fi

# --- plan ---------------------------------------------------------------------------------------
plan=()      # plain, space-free commands executed in order by --apply
errors=()
expected_names=()

is_empty_dir() { [ -z "$(ls -A "$1" 2>/dev/null)" ]; }

plan_dir() {  # path uid gid mode
    local path="$1" uid="$2" gid="$3" mode="$4" cur
    if [ -L "$path" ]; then errors+=("$path is a symlink"); return; fi
    if [ ! -e "$path" ]; then
        plan+=("mkdir $path" "chown $uid:$gid $path" "chmod $mode $path")
    elif [ ! -d "$path" ]; then
        errors+=("$path exists and is not a directory")
    else
        cur="$(stat -c '%u:%g %a' "$path")"
        if [ "$cur" = "$uid:$gid ${mode#0}" ]; then
            note "  ok (unchanged): $path is already $uid:$gid $mode"
        elif is_empty_dir "$path"; then
            plan+=("chown $uid:$gid $path" "chmod $mode $path")
        else
            errors+=("$path is not empty and is $cur, expected $uid:$gid ${mode#0}: refusing to change ownership of live data")
        fi
    fi
}

echo "storage root : $ROOT"
echo "mode         : $([ "$APPLY" -eq 1 ] && echo APPLY || echo DRY-RUN)"

if [ "$APPLY" -eq 1 ]; then
    [ "$(id -u)" -eq 0 ] || die "--apply needs root (chown to uid 70 and 10001): re-run with sudo"
    [ -d /data ] || die "/data does not exist"
    [ "$(stat -c %d /data)" != "$(stat -c %d /)" ] || die "/data is on the root filesystem; expected the separate /data filesystem"
fi
[ -d /data ] || note "  NOTE: /data does not exist on this host (it must exist on the agency server)"

read -r r_uid r_gid r_mode <<<"$RMA_ROOT_OWNER"
plan_dir "$ROOT" "$r_uid" "$r_gid" "$r_mode"

if [ -d "$ROOT" ]; then
    for row in "${RMA_STORAGE_LAYOUT[@]}"; do expected_names+=("${row%% *}"); done
    while IFS= read -r entry; do
        [ -n "$entry" ] || continue
        found=0
        for name in "${expected_names[@]}"; do
            if [ "$entry" = "$name" ]; then found=1; fi
        done
        [ "$found" -eq 1 ] || errors+=("unexpected entry in $ROOT: $entry (refusing to touch it)")
    done < <(ls -A "$ROOT")
fi
for row in "${RMA_STORAGE_LAYOUT[@]}"; do
    read -r name uid gid mode <<<"$row"
    plan_dir "$ROOT/$name" "$uid" "$gid" "$mode"
done

if [ "${#errors[@]}" -gt 0 ]; then
    echo "REFUSED:" >&2
    printf '  - %s\n' "${errors[@]}" >&2
    exit 2
fi

echo "planned operations (${#plan[@]}):"
if [ "${#plan[@]}" -eq 0 ]; then echo "  (none: everything already in place)"; fi
if [ "${#plan[@]}" -gt 0 ]; then printf '  %s\n' "${plan[@]}"; fi

if [ "$APPLY" -ne 1 ]; then
    echo "dry-run complete: nothing was changed. Re-run with --apply (as root) after review."
    exit 0
fi

for op in "${plan[@]}"; do
    echo "+ $op"
    # shellcheck disable=SC2086  # the plan holds plain, space-free tokens by construction
    $op
done

# --- verify ------------------------------------------------------------------------------------------
bad=0
check() {  # path uid gid mode
    local got; got="$(stat -c '%u:%g %a' "$1")"
    if [ "$got" = "$2:$3 ${4#0}" ]; then
        echo "  verified: $1 $2:$3 $4"
    else
        echo "  MISMATCH: $1 is $got, expected $2:$3 ${4#0}" >&2; bad=1
    fi
}
check "$ROOT" "$r_uid" "$r_gid" "$r_mode"
for row in "${RMA_STORAGE_LAYOUT[@]}"; do
    read -r name uid gid mode <<<"$row"
    check "$ROOT/$name" "$uid" "$gid" "$mode"
done
[ "$bad" -eq 0 ] || die "verification failed"
echo "storage prepared. No container was started; no secret was created."
