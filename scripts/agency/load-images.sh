#!/usr/bin/env bash
# Server-side: load an RMA image bundle into the local Docker image store. DRY-RUN by default.
#
#   scripts/agency/load-images.sh <bundle.tar>            # verify + show what would happen
#   scripts/agency/load-images.sh <bundle.tar> --apply    # verify, then `docker load`
#
# The SHA-256 (and manifest consistency) is ALWAYS verified first; `docker load` runs only
# with --apply and only after that check passed. It adds the three rma-portal* image tags
# and nothing else: it never deletes, replaces or prunes any image, and never pulls or builds.
# After loading it confirms that every loaded image ID equals the portable archive config ID in the manifest.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

TAR=""; APPLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*) die "unknown argument: $1" ;;
        *) [ -z "$TAR" ] || die "only one bundle can be given"; TAR="$1" ;;
    esac
    shift
done
[ -n "$TAR" ] || die "usage: load-images.sh <bundle.tar> [--apply]"
command -v docker >/dev/null 2>&1 || die "docker is required"
MANIFEST="${TAR%.tar}.manifest.json"

# Verification precedes any load, in dry-run and in apply mode alike.
"$HERE/verify-bundle.sh" "$TAR" || die "verification failed: nothing was loaded"

echo "== images this bundle adds (existing images are never replaced or removed)"
# Expected IDs are the portable archive config IDs (what docker load registers), never the
# source engine's IDs. A same-name tag with a different ID is a CONFLICT and aborts BEFORE any load.
conflict=0
while read -r ref id; do
    if current="$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null)"; then
        if [ "$current" = "$id" ]; then
            echo "  already loaded, identical: $ref"
        else
            echo "  CONFLICT: $ref exists with ID $current, bundle has $id" >&2; conflict=1
        fi
    else
        echo "  would load: $ref ($id)"
    fi
done < <(python3 -B "$HERE/manifest.py" images "$MANIFEST")
[ "$conflict" -eq 0 ] || die "conflicting tags found: nothing was loaded, nothing was changed (resolve by choosing a new version)"

if [ "$APPLY" -ne 1 ]; then
    echo "dry-run complete: nothing was loaded. Re-run with --apply after approval."
    exit 0
fi

echo "== docker load (this only adds image layers/tags)"
docker load --input "$TAR"

echo "== confirming exact image IDs"
status=0
while read -r ref id; do
    got="$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null || echo missing)"
    if [ "$got" = "$id" ]; then echo "  OK  $ref $got"; else echo "  BAD $ref expected $id got $got" >&2; status=1; fi
done < <(python3 -B "$HERE/manifest.py" images "$MANIFEST")
[ "$status" -eq 0 ] || die "loaded image IDs do not match the manifest's archive config IDs"
echo "loaded. No pruning, pulling or building was performed."
