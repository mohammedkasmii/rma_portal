#!/usr/bin/env python3
"""Write, show and query the image-bundle manifest (stdlib only, no secrets involved).

    manifest.py write  --output M --commit C --version V --architecture amd64 --archive NAME \
                       --archive-sha256 H --archive-bytes N --postgres-source REF \
                       --image "ref|image-id|digest,digest" [--image ...]
    manifest.py show   M          # human summary
    manifest.py images M          # "<ref> <image-id>" per line
    manifest.py get    M KEY      # a top-level scalar (git_commit, version, architecture, ...)
    manifest.py arch-ok M UNAME   # exit 0 when the manifest architecture matches `uname -m`
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9a-f]{12}$")


def write(args: argparse.Namespace) -> int:
    if not COMMIT_RE.match(args.commit):
        sys.exit("manifest: --commit must be a full 40-character git commit")
    if not VERSION_RE.match(args.version) or not args.commit.startswith(args.version):
        sys.exit("manifest: --version must be the first 12 characters of --commit")
    images = []
    for spec in args.image:
        ref, image_id, digests = (spec.split("|") + ["", ""])[:3]
        if not ref or not image_id.startswith("sha256:"):
            sys.exit(f"manifest: bad --image {spec!r}")
        images.append(
            {"ref": ref, "id": image_id, "repo_digests": [d for d in digests.split(",") if d]}
        )
    manifest = {
        "schema": 1,
        "git_commit": args.commit,
        "version": args.version,
        "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "architecture": args.architecture,
        "os": "linux",
        "archive": {
            "name": args.archive,
            "sha256": args.archive_sha256,
            "bytes": args.archive_bytes,
        },
        "postgres_source": args.postgres_source,
        "images": images,
        "contains_secrets": False,
    }
    Path(args.output).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def show(args: argparse.Namespace) -> int:
    m = _load(args.manifest)
    print(f"git commit   : {m['git_commit']}")
    print(f"version      : {m['version']}")
    print(f"created (UTC): {m['created_utc']}")
    print(f"architecture : {m['os']}/{m['architecture']}")
    print(f"archive      : {m['archive']['name']} ({m['archive']['bytes']} bytes)")
    print(f"archive sha  : {m['archive']['sha256']}")
    print(f"postgres src : {m['postgres_source']}")
    for image in m["images"]:
        digests = ",".join(image["repo_digests"]) or "-"
        print(f"image        : {image['ref']}  {image['id']}  digests={digests}")
    return 0


def images(args: argparse.Namespace) -> int:
    for image in _load(args.manifest)["images"]:
        print(image["ref"], image["id"])
    return 0


def get(args: argparse.Namespace) -> int:
    value = _load(args.manifest)
    for part in args.key.split("."):
        value = value[part]
    print(value)
    return 0


def arch_ok(args: argparse.Namespace) -> int:
    return 0 if ARCH.get(args.uname) == _load(args.manifest)["architecture"] else 1


def main() -> int:
    sys.stdout.reconfigure(newline="\n")  # shell callers parse the output: never CRLF
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    for flag in (
        "output",
        "commit",
        "version",
        "architecture",
        "archive",
        "archive-sha256",
        "postgres-source",
    ):
        w.add_argument(f"--{flag}", required=True)
    w.add_argument("--archive-bytes", type=int, required=True)
    w.add_argument("--image", action="append", required=True)
    w.set_defaults(func=write)
    for name, func in (("show", show), ("images", images)):
        p = sub.add_parser(name)
        p.add_argument("manifest")
        p.set_defaults(func=func)
    g = sub.add_parser("get")
    g.add_argument("manifest")
    g.add_argument("key")
    g.set_defaults(func=get)
    a = sub.add_parser("arch-ok")
    a.add_argument("manifest")
    a.add_argument("uname")
    a.set_defaults(func=arch_ok)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
