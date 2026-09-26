#!/usr/bin/env python3
"""Portable image IDs of a ``docker save`` archive, read WITHOUT extracting anything.

``docker load`` registers each image under the SHA-256 of its *config object* (the JSON that
``manifest.json`` references as ``Config``). That digest, not the source engine's ``.Id`` (which
can be an OCI manifest-list/registry-manifest digest with the containerd store), is what
``docker image inspect --format '{{.Id}}'`` prints after a load. This module derives it from the
finished tar so an export and a load on different engines agree.

    archive_ids.py ids IMAGES.tar REF [REF ...]     # prints "<ref> sha256:<config digest>"

Strict by design: unsafe member paths, malformed JSON, missing/duplicate tags, missing config
members and a config whose bytes do not hash to the digest in its own path are all errors.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tarfile
from pathlib import PurePosixPath

MAX_CONFIG_BYTES = 16 * 1024 * 1024
_HEX = r"[0-9a-f]{64}"
_CONFIG_PATHS = (
    re.compile(rf"^({_HEX})\.json$"),  # traditional layout
    re.compile(rf"^blobs/sha256/({_HEX})$"),  # OCI layout (containerd image store)
)


class ArchiveError(Exception):
    """The archive is malformed, unsafe or does not contain what the manifest promises."""


def _check_member_path(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:", name)
        or ".." in path.parts
    ):
        raise ArchiveError(f"unsafe member path in archive: {name!r}")


def _declared_digest(config_path: str) -> str:
    for pattern in _CONFIG_PATHS:
        match = pattern.match(config_path)
        if match:
            return match.group(1)
    raise ArchiveError(f"unsupported Config path {config_path!r}")


def _tag_matches(repo_tag: str, ref: str) -> bool:
    return repo_tag in (ref, f"docker.io/library/{ref}")


def ids_for(tar_path: str, refs: list[str]) -> dict[str, str]:
    """Return ``{ref: "sha256:<config digest>"}`` for every requested tag."""
    try:
        with tarfile.open(tar_path, "r:") as tar:
            members = {}
            for member in tar.getmembers():
                _check_member_path(member.name)
                members[member.name] = member
            manifest_member = members.get("manifest.json")
            if manifest_member is None or not manifest_member.isreg():
                raise ArchiveError("manifest.json is missing from the archive")
            handle = tar.extractfile(manifest_member)
            assert handle is not None
            try:
                entries = json.loads(handle.read(MAX_CONFIG_BYTES))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ArchiveError(f"manifest.json is malformed: {exc}") from exc
            if not isinstance(entries, list):
                raise ArchiveError("manifest.json is not a list")

            seen: dict[str, str] = {}  # repo tag -> config path
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("Config"), str):
                    raise ArchiveError("manifest.json entry without a Config path")
                tags = entry.get("RepoTags") or []
                if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                    raise ArchiveError("manifest.json entry with malformed RepoTags")
                _check_member_path(entry["Config"])
                for tag in tags:
                    if tag in seen:
                        raise ArchiveError(f"duplicate tag in archive: {tag}")
                    seen[tag] = entry["Config"]

            result: dict[str, str] = {}
            for ref in refs:
                candidates = [t for t in seen if _tag_matches(t, ref)]
                if not candidates:
                    raise ArchiveError(f"tag not found in archive: {ref}")
                if len(candidates) > 1:
                    raise ArchiveError(f"duplicate tag in archive: {ref}")
                config_path = seen[candidates[0]]
                config = members.get(config_path)
                if config is None or not config.isreg():
                    raise ArchiveError(f"config member missing for {ref}: {config_path}")
                if config.size > MAX_CONFIG_BYTES:
                    raise ArchiveError(f"config member of {ref} is implausibly large")
                data = tar.extractfile(config)
                assert data is not None
                actual = hashlib.sha256(data.read()).hexdigest()
                declared = _declared_digest(config_path)
                if actual != declared:
                    raise ArchiveError(
                        f"config digest mismatch for {ref}: path says {declared}, bytes hash to {actual}"
                    )
                result[ref] = f"sha256:{actual}"
            return result
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveError(f"cannot read archive: {exc}") from exc


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[1] != "ids":
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.reconfigure(newline="\n")  # type: ignore[attr-defined]  # shell callers parse this
    try:
        for ref, image_id in ids_for(sys.argv[2], sys.argv[3:]).items():
            print(ref, image_id)
    except ArchiveError as exc:
        print(f"archive: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
