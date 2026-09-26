#!/usr/bin/env bash
# Build the immutable RMA image bundle on the validated Ubuntu VM (NEVER on the agency server).
#
#   scripts/agency/export-images.sh --output-dir /path/to/existing/dir
#
# From the CLEAN git checkout it:
#   1. refuses a dirty working tree (tracked, staged or untracked changes);
#   2. derives RMA_VERSION = first 12 characters of the full HEAD commit;
#   3. builds rma-portal:<version> (runtime) and rma-portal-web:<version> (web) from
#      docker/prod/Dockerfile (base images pinned by digest);
#   4. re-tags the exact pinned PostgreSQL image of compose.prod.yaml as
#      rma-portal-postgres:<version> (same image ID; a stable local reference that
#      survives `docker load`, which does not keep registry digests);
#   5. writes into --output-dir (created with umask 077, atomically, never overwriting):
#        rma-portal-images-<version>.tar            the three images (docker save)
#        rma-portal-images-<version>.tar.sha256     sha256sum -c compatible checksum
#        rma-portal-images-<version>.manifest.json  commit, tags, image IDs/digests, arch, timestamp
#        rma-portal-deploy-<version>.tar.gz         tracked deployment files (compose, scripts, runbook)
#        rma-portal-deploy-<version>.tar.gz.sha256
# The bundle contains no .env, password, cookie, database data or session state: images are
# built from the git-tracked context (docker/prod/Dockerfile.dockerignore excludes them) and
# the deployment archive is made with `git archive`.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

OUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --output-dir) OUT="${2:?--output-dir needs a value}"; shift ;;
        -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done
[ -n "$OUT" ] || die "usage: export-images.sh --output-dir <existing directory>"
[ -d "$OUT" ] || die "output directory does not exist (it is never created for you): $OUT"
OUT="$(cd "$OUT" && pwd)"
case "$OUT" in "$REPO_ROOT"|"$REPO_ROOT"/*) die "the output directory must be outside the repository" ;; esac

command -v docker >/dev/null 2>&1 || die "docker is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
cd "$REPO_ROOT"

# 1. clean tree ----------------------------------------------------------------------------------------
[ -z "$(git status --porcelain)" ] || die "the working tree is dirty; commit or stash first (the tag must identify exactly one commit)"
COMMIT="$(git rev-parse HEAD)"
VERSION="${COMMIT:0:12}"
ARCH_RAW="$(docker version --format '{{.Server.Arch}}')"
[ "$ARCH_RAW" = "amd64" ] || die "this VM builds linux/$ARCH_RAW; the agency server needs linux/amd64"

TAR="$OUT/rma-portal-images-$VERSION.tar"
MANIFEST="$OUT/rma-portal-images-$VERSION.manifest.json"
DEPLOY="$OUT/rma-portal-deploy-$VERSION.tar.gz"
for f in "$TAR" "$TAR.sha256" "$MANIFEST" "$DEPLOY" "$DEPLOY.sha256"; do
    [ ! -e "$f" ] || die "refusing to overwrite an existing file: $f"
done

APP="rma-portal:$VERSION"; WEB="rma-portal-web:$VERSION"; PG="rma-portal-postgres:$VERSION"
PG_SOURCE="$(grep -E '^\s+image:\s+postgres:' compose.prod.yaml | head -n1 | sed -E 's/^\s+image:\s+//')"
[ -n "$PG_SOURCE" ] || die "could not read the pinned PostgreSQL image from compose.prod.yaml"
case "$PG_SOURCE" in *@sha256:*) ;; *) die "the PostgreSQL image is not pinned by digest: $PG_SOURCE" ;; esac

echo "== version $VERSION (commit $COMMIT)"
echo "== building $APP and $WEB (context: git-tracked repository files)"
docker build --file docker/prod/Dockerfile --target runtime --label "org.opencontainers.image.revision=$COMMIT" --tag "$APP" .
docker build --file docker/prod/Dockerfile --target web --label "org.opencontainers.image.revision=$COMMIT" --tag "$WEB" .

echo "== pinned PostgreSQL: $PG_SOURCE"
docker image inspect "$PG_SOURCE" >/dev/null 2>&1 || docker pull "$PG_SOURCE"
docker tag "$PG_SOURCE" "$PG"

# 2. save + checksum + manifest (atomic: *.partial then mv) -----------------------------------------
umask 077
echo "== saving images"
docker save --output "$TAR.partial" "$APP" "$WEB" "$PG"
mv "$TAR.partial" "$TAR"
(cd "$OUT" && sha256sum "$(basename "$TAR")" >"$(basename "$TAR").sha256")
SHA="$(cut -d' ' -f1 "$TAR.sha256")"

specs=()
for ref in "$APP" "$WEB" "$PG"; do
    id="$(docker image inspect --format '{{.Id}}' "$ref")"
    digests="$(docker image inspect --format '{{join .RepoDigests ","}}' "$ref")"
    specs+=(--image "$ref|$id|$digests")
done
python3 -B "$HERE/manifest.py" write --output "$MANIFEST" --commit "$COMMIT" --version "$VERSION" \
    --architecture "$ARCH_RAW" --archive "$(basename "$TAR")" --archive-sha256 "$SHA" \
    --archive-bytes "$(stat -c %s "$TAR")" --postgres-source "$PG_SOURCE" "${specs[@]}"

# 3. tracked deployment files only (no .env, no untracked file can enter) ---------------------------
git archive --format=tar.gz --prefix="rma-portal-deploy-$VERSION/" -o "$DEPLOY.partial" HEAD \
    compose.agency.yaml .env.agency.example scripts/agency docs/agency-production-deployment.md
mv "$DEPLOY.partial" "$DEPLOY"
(cd "$OUT" && sha256sum "$(basename "$DEPLOY")" >"$(basename "$DEPLOY").sha256")

echo
echo "bundle ready in $OUT"
python3 -B "$HERE/manifest.py" show "$MANIFEST"
echo "next: copy the five files to the server (into /data/rma-portal/image-bundles), then follow Stage 4."
