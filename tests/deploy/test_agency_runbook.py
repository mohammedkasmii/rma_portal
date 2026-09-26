"""The agency production runbook: staged, scoped, reversible, no generic start, worker last."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
RUNBOOK = ROOT / "docs" / "agency-production-deployment.md"
TEXT = RUNBOOK.read_text(encoding="utf-8")
SERVICES = ("db", "api", "web", "browser", "worker")

# Destructive or out-of-scope operations that must not appear even as examples.
FORBIDDEN = [
    r"docker\s+(system|image|volume|network|builder|container)\s+prune",
    r"down\s+(-\w*v|--volumes)",
    r"volume\s+rm",
    r"\bnetwork\s+rm\s",
    r"(systemctl|service)\s+(\S+\s+)?restart\s+(\S+\s+)?docker",
    r"daemon\.json",
    r"apt(-get)?\s+(install|upgrade|remove|purge|dist-upgrade)",
    r"\breboot\b",
    r"--privileged|network_mode\s*:\s*host|docker\.sock",
    r"docker\s+build|docker\s+pull",
    r"rm\s+-\w*r\w*f?\s+/data",
    r"docker\s+rmi|docker\s+rm\s",
]


def _stage(number: int) -> str:
    match = re.search(
        rf"^### Stage {number} — .*?(?=^### Stage |^## )", TEXT, re.MULTILINE | re.DOTALL
    )
    assert match, f"Stage {number} missing"
    return match.group(0)


def test_all_fifteen_stages_exist_in_order_with_stop_and_rollback():
    numbers = [int(n) for n in re.findall(r"^### Stage (\d+) — ", TEXT, re.MULTILINE)]
    assert numbers == list(range(15))
    for number in range(15):
        stage = _stage(number)
        assert re.search(r"^\| Stop \|", stage, re.MULTILINE), (
            f"Stage {number} has no stop condition"
        )
        assert re.search(r"^\| Rollback \|", stage, re.MULTILINE), f"Stage {number} has no rollback"
        assert re.search(r"^\| (Confirm|Expected|Verify)", stage, re.MULTILINE), (
            f"Stage {number} has no expected result"
        )


def test_no_generic_first_deployment_up_and_every_start_is_scoped():
    occurrences = [m.start() for m in re.finditer(r"up -d", TEXT)]
    assert occurrences
    for start in occurrences:
        assert re.match(rf"up -d --no-deps ({'|'.join(SERVICES)})\b", TEXT[start:]), TEXT[
            start : start + 60
        ]
    for line in TEXT.splitlines():
        if re.search(r"docker compose .*\b(up|run)\b", line):
            assert "--no-deps" in line, line
        assert not re.search(r"compose[^\n]*\b(start|restart|stop)\s*$", line), f"unscoped: {line}"


def test_services_start_in_the_documented_order_with_the_worker_last():
    first = {s: TEXT.index(f"up -d --no-deps {s}") for s in SERVICES}
    order = sorted(SERVICES, key=first.__getitem__)
    assert order == ["db", "api", "web", "browser", "worker"]
    assert TEXT.index("run --rm --no-deps migrate") > first["db"]
    assert TEXT.index("run --rm --no-deps migrate") < first["api"]
    assert (
        TEXT.index("create-admin") > first["web"] and TEXT.index("create-admin") < first["browser"]
    )
    assert (
        TEXT.index("rma_poc login") > first["browser"]
        and TEXT.index("rma_poc login") < first["worker"]
    )
    # the worker is started once during the first deployment (Stage 12); Stage 14 only recreates it
    assert "up -d --no-deps worker" in _stage(12)
    assert "up -d --no-deps browser" not in _stage(12)


def test_first_stages_mutate_nothing_and_stage_one_is_only_the_preflight():
    stage0, stage1 = _stage(0), _stage(1)
    assert "nothing" in stage0.lower() and "no mutation" in stage0.lower()
    assert "preflight.sh --pre-config" in stage1
    assert "read-only" in stage1.lower()
    assert "docker compose" not in stage1
    assert "21 COMPLETE, 0 FAILED" in stage0


def test_storage_stage_is_dry_run_first_then_apply():
    stage = _stage(2)
    assert stage.index("prepare-storage.sh          # dry-run") < stage.index("--apply")
    for path in (
        "postgres",
        "app-data",
        "logs",
        "browser-profile",
        "session-state",
        "backups",
        "releases",
        "image-bundles",
        "config",
    ):
        assert f"`{path}`" in TEXT
    assert "/data/rma-portal" in TEXT
    assert "70:70" in TEXT and "10001:10001" in TEXT


def test_configuration_stage_uses_the_exact_validation_command_and_never_prints_secrets():
    stage = _stage(3)
    assert (
        "docker compose --env-file /data/rma-portal/config/.env -f compose.agency.yaml config -q"
        in stage
    )
    assert "umask 077" in stage and "noclobber" in stage
    assert "stat -c" in stage and "600" in stage
    assert not re.search(r"^\s*(cat|grep|echo|less|head)\s+[^\n]*config/\.env", stage, re.MULTILINE)
    for name in ("POSTGRES_PASSWORD", "RMA_SESSION_SECRET", "RMA_VNC_PASSWORD"):
        assert f'echo "{name}=$(openssl rand' in stage
    assert "Do not start" in stage or "no service starts" in stage.lower()


def test_image_stage_verifies_before_loading_and_never_pulls_or_builds():
    stage = _stage(4)
    assert stage.index("verify-bundle.sh") < stage.index("load-images.sh") < stage.index("--apply")
    assert "docker pull" not in stage and "docker build" not in stage


def test_network_exposure_and_tunnel_are_documented_exactly():
    assert "192.168.1.32:8480" in TEXT and "http://192.168.1.32:8480" in TEXT
    assert "127.0.0.1:6081" in TEXT
    assert "ssh -L 6081:127.0.0.1:6081 ubuntu@192.168.1.32" in TEXT
    assert "http://127.0.0.1:6081/vnc.html" in TEXT
    assert "RMA_COOKIE_SECURE=false" in TEXT
    assert "HTTPS" in TEXT and "separately approved" in TEXT
    assert "Tailscale" in TEXT and "100.89.63.25" in TEXT
    assert "8080/tcp -> 192.168.1.32:8480" in _stage(8)
    assert "6080/tcp -> 127.0.0.1:6081" in _stage(10)


def test_secret_behaviour_is_documented():
    assert "logs every user out" in TEXT or "logs everyone out" in TEXT
    assert "first 8 characters" in TEXT
    assert "first initialises" in TEXT or "first initialises an empty data directory" in TEXT


def test_ai_stays_disabled_and_polling_changes_only_at_acceptance():
    assert "RMA_OLLAMA_ENABLED" in TEXT and "false" in TEXT
    assert "3600" in _stage(12)
    stage14 = _stage(14)
    assert "300" in stage14 and "do not enable ai in the same change" in stage14.lower().replace(
        "**", ""
    )
    assert "300" not in _stage(12)


def test_first_cycle_expectations_are_complete():
    stage = _stage(13)
    for token in (
        "21",
        "COMPLETE",
        "FAILED",
        "PARTIAL",
        "AUTH_REQUIRED",
        "baseline",
        "blocked non-read-only",
        "storage",
        "healthy",
    ):
        assert token.lower() in stage.lower(), token


def test_rollback_backup_and_operations_sections_are_safe_and_complete():
    lowered = TEXT.lower()
    assert "does not reverse a database migration" in lowered
    assert "never deletes data" in lowered or "never deletes" in lowered
    assert (
        "verified rma backup" in lowered
        or "verified rma-specific backup" in lowered
        or "rma-specific backup" in lowered
    )
    assert "--with-session" in TEXT and "sensitive" in lowered
    assert "restore-rehearsal.sh" in TEXT
    assert "no cron job or systemd timer" in lowered
    for needle in (
        "RMA view",
        "Others unchanged?",
        "docker port rma-portal-web",
        "docker logs",
        "du -sh /data/rma-portal",
        "before-identity.txt",
        "blocked non-read-only OmegaFlow request",
    ):
        assert needle in TEXT, needle
    assert "RMA_VERSION=<previous V>" in TEXT


def test_pre_existing_unhealthy_containers_are_recorded_never_repaired():
    assert "pre-existing" in TEXT.lower()
    assert "never repaired" in TEXT or "never touched" in TEXT
    assert "Wexia" in TEXT and "Shexpert" in TEXT and "Supabase" in TEXT and "RustDesk" in TEXT


def test_runbook_contains_no_forbidden_operation():
    for pattern in FORBIDDEN:
        assert not re.search(pattern, TEXT, re.IGNORECASE), pattern


def test_readme_links_the_runbook_and_the_release_files_exist():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/agency-production-deployment.md" in readme
    for name in (
        "compose.agency.yaml",
        ".env.agency.example",
        "scripts/agency/preflight.sh",
        "scripts/agency/prepare-storage.sh",
        "scripts/agency/export-images.sh",
        "scripts/agency/load-images.sh",
        "scripts/agency/backup.sh",
    ):
        assert (ROOT / name).exists(), name
