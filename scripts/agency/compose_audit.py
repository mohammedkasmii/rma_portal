#!/usr/bin/env python3
"""Audit a resolved compose.agency.yaml (``docker compose config --format json`` on stdin).

Read-only and secret-safe: it prints findings and image references only, never an
environment value. Used by scripts/agency/preflight.sh; the same ``audit`` function is
imported by tests/deploy/test_agency_deployment.py.

    docker compose --env-file E -f compose.agency.yaml config --format json \
        | python3 compose_audit.py --storage-root /data/rma-portal \
              --web-ip 192.168.1.32 --web-port 8480 --novnc-ip 127.0.0.1 --novnc-port 6081
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

SERVICES = ("db", "migrate", "api", "worker", "web", "browser")
LONG_RUNNING = ("db", "api", "worker", "web", "browser")
ALLOWED_CAP_ADD = {
    "db": {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"},
    "web": {"CHOWN", "SETGID", "SETUID"},
}
IMAGE_NAMES = {
    "db": "rma-portal-postgres",
    "migrate": "rma-portal",
    "api": "rma-portal",
    "worker": "rma-portal",
    "web": "rma-portal-web",
    "browser": "rma-portal",
}
VERSION_RE = re.compile(r"^[0-9a-f]{12}$")
Finding = tuple[str, str]  # ("OK" | "WARN" | "FAIL", message)


def _limit_findings(name: str, svc: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    missing = [
        key for key in ("cpus", "mem_limit", "memswap_limit", "pids_limit") if not svc.get(key)
    ]
    if missing:
        out.append(("FAIL", f"{name}: missing resource limits {missing}"))
    if name in LONG_RUNNING:
        if not svc.get("mem_reservation"):
            out.append(("FAIL", f"{name}: missing memory reservation"))
        if svc.get("restart") != "unless-stopped":
            out.append(("FAIL", f"{name}: restart policy must be unless-stopped"))
        if not svc.get("healthcheck"):
            out.append(("FAIL", f"{name}: missing health check"))
    log = svc.get("logging") or {}
    options = log.get("options") or {}
    if (
        log.get("driver") != "json-file"
        or not options.get("max-size")
        or not options.get("max-file")
    ):
        out.append(("FAIL", f"{name}: log rotation (json-file max-size/max-file) missing"))
    return out


def _hardening_findings(name: str, svc: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    if svc.get("privileged"):
        out.append(("FAIL", f"{name}: privileged"))
    for key in ("network_mode", "pid", "ipc", "userns_mode"):
        if svc.get(key) == "host":
            out.append(("FAIL", f"{name}: {key}: host"))
    if "ALL" not in (svc.get("cap_drop") or []):
        out.append(("FAIL", f"{name}: cap_drop must contain ALL"))
    extra = set(svc.get("cap_add") or []) - ALLOWED_CAP_ADD.get(name, set())
    if extra:
        out.append(("FAIL", f"{name}: unexpected cap_add {sorted(extra)}"))
    if "no-new-privileges:true" not in (svc.get("security_opt") or []):
        out.append(("FAIL", f"{name}: no-new-privileges missing"))
    if svc.get("build"):
        out.append(("FAIL", f"{name}: build section (production never builds)"))
    if svc.get("pull_policy") != "never":
        out.append(("FAIL", f"{name}: pull_policy must be never"))
    if list(svc.get("networks") or {}) != ["rma-portal-net"]:
        out.append(("FAIL", f"{name}: must be attached to rma-portal-net only"))
    return out


def _mount_findings(name: str, svc: dict[str, Any], root: str) -> list[Finding]:
    out: list[Finding] = []
    for vol in svc.get("volumes") or []:
        source = str(vol.get("source", ""))
        if vol.get("type") != "bind":
            out.append(("FAIL", f"{name}: non-bind persistent mount {vol}"))
        elif "docker.sock" in source:
            out.append(("FAIL", f"{name}: Docker socket mount"))
        elif not source.startswith(root + "/") or source.count("/") != root.count("/") + 1:
            out.append(("FAIL", f"{name}: mount {source} is not a direct child of {root}"))
        elif (vol.get("bind") or {}).get("create_host_path", True):
            out.append(
                ("FAIL", f"{name}: {source} may be auto-created by Docker (create_host_path)")
            )
    return out


def audit(
    config: dict[str, Any],
    *,
    root: str,
    web: tuple[str, str],
    novnc: tuple[str, str],
    allow_ai: bool = False,
) -> list[Finding]:
    """Return findings for a resolved configuration. ``web``/``novnc`` are (ip, port)."""
    out: list[Finding] = []
    services = config.get("services") or {}
    if config.get("name") != "rma-portal":
        out.append(("FAIL", f"project name is {config.get('name')!r}, expected rma-portal"))
    if set(services) != set(SERVICES):
        out.append(("FAIL", f"services are {sorted(services)}, expected {sorted(SERVICES)}"))
    if set(config.get("networks") or {}) != {"rma-portal-net"}:
        out.append(("FAIL", "top-level networks must be exactly rma-portal-net"))
    if config.get("volumes"):
        out.append(("FAIL", "top-level named volumes are not allowed"))

    versions: set[str] = set()
    expected_ports = {"web": (web, 8080), "browser": (novnc, 6080)}
    for name in SERVICES:
        svc = services.get(name)
        if svc is None:
            continue
        if svc.get("container_name") != f"rma-portal-{name}":
            out.append(("FAIL", f"{name}: container_name is {svc.get('container_name')!r}"))
        image = str(svc.get("image", ""))
        repo, _, tag = image.rpartition(":")
        if repo != IMAGE_NAMES[name]:
            out.append(("FAIL", f"{name}: image {image!r} is not {IMAGE_NAMES[name]}:<version>"))
        versions.add(tag)
        out += (
            _limit_findings(name, svc)
            + _hardening_findings(name, svc)
            + _mount_findings(name, svc, root)
        )

        ports = svc.get("ports") or []
        if name in expected_ports:
            (ip, port), target = expected_ports[name]
            got = [(p.get("host_ip"), str(p.get("published")), p.get("target")) for p in ports]
            if got != [(ip, str(port), target)]:
                out.append(
                    ("FAIL", f"{name}: published ports {got}, expected {[(ip, str(port), target)]}")
                )
        elif ports:
            out.append(("FAIL", f"{name}: must not publish ports"))

    if len(versions) != 1 or not VERSION_RE.match(next(iter(versions), "")):
        out.append(("FAIL", f"image tags {sorted(versions)} must be one 12-character commit"))
    ai = str(
        (services.get("worker") or {}).get("environment", {}).get("RMA_PORTAL_OLLAMA_ENABLED", "")
    )
    if ai.lower() != "false":
        out.append(
            (
                "WARN" if allow_ai else "FAIL",
                "RMA_OLLAMA_ENABLED must be false for the initial deployment",
            )
        )
    if not out:
        out.append(
            ("OK", "compose configuration satisfies every isolation, storage, port and limit rule")
        )
    return out


def images(config: dict[str, Any]) -> list[str]:
    return sorted({svc["image"] for svc in (config.get("services") or {}).values()})


def main() -> int:
    sys.stdout.reconfigure(newline="\n")  # type: ignore[attr-defined]  # shell callers parse this
    parser =argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--web-ip", required=True)
    parser.add_argument("--web-port", required=True)
    parser.add_argument("--novnc-ip", required=True)
    parser.add_argument("--novnc-port", required=True)
    parser.add_argument("--allow-ai", action="store_true")
    args = parser.parse_args()
    config = json.load(sys.stdin)
    findings = audit(
        config,
        root=args.storage_root,
        web=(args.web_ip, args.web_port),
        novnc=(args.novnc_ip, args.novnc_port),
        allow_ai=args.allow_ai,
    )
    for level, message in findings:
        print(f"{level}: {message}")
    for ref in images(config):
        print(f"IMAGE {ref}")
    return 1 if any(level == "FAIL" for level, _ in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
