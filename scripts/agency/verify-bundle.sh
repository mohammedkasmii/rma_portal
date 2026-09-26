#!/usr/bin/env bash
# Server-side, read-only: verify an RMA image bundle and show its manifest.
#
#   scripts/agency/verify-bundle.sh /data/rma-portal/image-bundles/rma-portal-images-<version>.tar
#
# Checks the SHA-256 (against the .sha256 file AND the manifest), prints the manifest, and
# checks that the bundle architecture matches this host. Loads nothing, writes nothing.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

case "${1:-}" in -h|--help|"") sed -n '2,7p' "${BASH_SOURCE[0]}"; [ -n "${1:-}" ] && exit 0; exit 2 ;; esac
TAR="$1"
[ -f "$TAR" ] || die "bundle not found: $TAR"
[ -f "$TAR.sha256" ] || die "checksum file not found: $TAR.sha256"
MANIFEST="${TAR%.tar}.manifest.json"
[ -f "$MANIFEST" ] || die "manifest not found: $MANIFEST"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

echo "== SHA-256 (checksum file)"
(cd "$(dirname "$TAR")" && sha256sum -c "$(basename "$TAR").sha256") || die "checksum mismatch: do NOT load this bundle"
want="$(python3 -B "$HERE/manifest.py" get "$MANIFEST" archive.sha256)"
have="$(cut -d' ' -f1 "$TAR.sha256")"
[ "$want" = "$have" ] || die "the manifest checksum differs from the checksum file: do NOT load this bundle"
[ "$(python3 -B "$HERE/manifest.py" get "$MANIFEST" archive.name)" = "$(basename "$TAR")" ] || die "the manifest describes a different archive"

echo "== manifest"
python3 -B "$HERE/manifest.py" show "$MANIFEST"
python3 -B "$HERE/manifest.py" arch-ok "$MANIFEST" "$(uname -m)" || die "architecture mismatch: bundle is $(python3 -B "$HERE/manifest.py" get "$MANIFEST" architecture), host is $(uname -m)"
echo "== verified: checksum and architecture are consistent. Nothing was loaded."
