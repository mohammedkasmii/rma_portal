"""The production Compose stack: isolation, hardening, persistence and documented variables.

These checks use `docker compose config` (no daemon, no build, nothing deployed) and read the
repository's own files, so they prove the stack is *defined* correctly; running it on the Ubuntu
VM is covered by docs/ubuntu-production-runbook.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
COMPOSE = ROOT / "compose.prod.yaml"
ENV_EXAMPLE = ROOT / ".env.example"
DOCKERFILE = ROOT / "docker" / "prod" / "Dockerfile"
NGINX = ROOT / "docker" / "prod" / "nginx.conf"
SCRIPTS = sorted((ROOT / "scripts" / "prod").glob("*.sh"))

docker_missing = shutil.which("docker") is None
needs_docker = pytest.mark.skipif(docker_missing, reason="docker CLI not installed")


def _config(env_file: Path = ENV_EXAMPLE) -> dict:
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "--env-file", str(env_file), "config", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=60,
        env={k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES_", "RMA_", "COMPOSE_"))},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def stack() -> dict:
    if docker_missing:
        pytest.skip("docker CLI not installed")
    return _config()


@needs_docker
def test_stack_is_exactly_postgres_api_worker_web_and_browser(stack):
    assert set(stack["services"]) == {"db", "migrate", "api", "worker", "web", "browser"}
    # One repository, one version: API, worker, migrations and browser share a single image.
    images = {stack["services"][s]["image"] for s in ("api", "worker", "migrate", "browser")}
    assert images == {"rma-portal:2.0.0"}
    for name, service in stack["services"].items():
        text = json.dumps(service).lower()
        assert not re.search(r"redis|celery|kube|rabbit|memcache", text), name


@needs_docker
def test_only_the_web_ui_and_the_novnc_desktop_are_published(stack):
    published = {
        name: [(p["published"], p["target"]) for p in service.get("ports", [])]
        for name, service in stack["services"].items()
        if service.get("ports")
    }
    assert published == {"web": [("8480", 8080)], "browser": [("6081", 6080)]}
    for name in ("db", "api", "worker", "migrate"):
        assert not stack["services"][name].get("ports"), f"{name} must not publish ports"


@needs_docker
def test_nothing_of_wexia_or_the_phase0_stack_is_touched(stack):
    assert "wexia" not in json.dumps(stack).lower()
    # Names, not paths: the browser image keeps its validated /opt/rma-poc scripts.
    names = [stack["name"], *stack["networks"], *stack["volumes"]]
    names += [s["container_name"] for s in stack["services"].values()]
    names += [v["name"] for v in stack["volumes"].values()]
    names += [s["image"] for s in stack["services"].values()]
    assert not [n for n in names if "rma-poc" in n or "wexia" in n]
    assert set(stack["networks"]) == {"rma-portal-net"}
    assert all(n.get("external") is not True for n in stack["networks"].values())
    for name, volume in stack["volumes"].items():
        assert name.startswith("rma-portal-") and volume["name"].startswith("rma-portal-")
        assert volume.get("external") is not True
    for name, service in stack["services"].items():
        assert service["container_name"].startswith("rma-portal-"), name
        # No host paths, sockets or host namespaces.
        assert not any(v["type"] == "bind" for v in service.get("volumes", [])), name
        assert service.get("network_mode") is None and service.get("pid") is None, name
        assert not service.get("privileged"), name
        assert "docker.sock" not in json.dumps(service), name


@needs_docker
def test_state_that_must_survive_is_on_named_volumes(stack):
    sources = {
        name: {v["source"] for v in service.get("volumes", [])}
        for name, service in stack["services"].items()
    }
    assert sources["db"] == {"rma-portal-pgdata"}
    # The browser desktop and the worker share the profile and the captured session.
    assert {"rma-portal-browser-profile", "rma-portal-session-state"} <= sources["browser"]
    assert {"rma-portal-browser-profile", "rma-portal-session-state"} <= sources["worker"]
    assert "rma-portal-logs" in sources["api"] and "rma-portal-logs" in sources["worker"]


@needs_docker
def test_api_and_worker_are_separate_processes_and_migrations_run_first(stack):
    api, worker, migrate = (stack["services"][n] for n in ("api", "worker", "migrate"))
    assert api["command"] == ["serve"] and worker["command"] == ["worker"] and migrate["command"] == ["migrate"]
    assert api["environment"]["RMA_PORTAL_RUN_SCHEDULER"] == "false"  # the worker owns polling
    assert worker["environment"]["RMA_PORTAL_HEADLESS_BROWSER"] == "true"
    for service in (api, worker):
        assert service["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert migrate["depends_on"]["db"]["condition"] == "service_healthy"
    assert migrate["restart"] == "no"
    assert stack["services"]["web"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert api["environment"]["RMA_PORTAL_DATABASE_URL"].startswith("postgresql+psycopg://rma:")


@needs_docker
def test_every_long_running_service_has_a_health_check_and_a_restart_policy(stack):
    for name in ("db", "api", "worker", "web", "browser"):
        service = stack["services"][name]
        assert service.get("healthcheck", {}).get("test"), f"{name} needs a health check"
        assert service["restart"] == "unless-stopped", name
    assert stack["services"]["worker"]["healthcheck"]["test"][-1] == "worker-health"


@needs_docker
def test_containers_are_hardened(stack):
    for name, service in stack["services"].items():
        assert "no-new-privileges:true" in service["security_opt"], name
        assert "ALL" in service["cap_drop"], name
        assert not service.get("privileged"), name
    assert stack["services"]["api"]["read_only"] is True
    assert stack["services"]["web"]["read_only"] is True
    # PostgreSQL is only reachable from inside the compose network.
    assert not stack["services"]["db"].get("ports")


@needs_docker
def test_required_secrets_have_no_default_and_fail_loudly(tmp_path):
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "--env-file", str(empty), "config", "-q"],
        capture_output=True,
        text=True,
        timeout=60,
        env={k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES_", "RMA_"))},
    )
    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr or "RMA_SESSION_SECRET" in result.stderr


def test_env_example_documents_every_variable_the_stack_reads():
    compose_text = COMPOSE.read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose_text))
    documented = {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^[A-Z][A-Z0-9_]*=", line)
    }
    assert used <= documented, f"undocumented variables: {sorted(used - documented)}"
    for secret in ("POSTGRES_PASSWORD", "RMA_SESSION_SECRET", "RMA_VNC_PASSWORD"):
        assert secret in documented


def test_env_example_holds_placeholders_never_real_secrets():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert text.count("change-me") >= 3
    assert not re.search(r"(?i)^(?:POSTGRES_PASSWORD|RMA_SESSION_SECRET|RMA_VNC_PASSWORD)=(?!change-me)\S+", text, re.M)


def test_dockerfile_pins_bases_and_the_browser_build_and_runs_unprivileged():
    text = DOCKERFILE.read_text(encoding="utf-8")
    for line in re.findall(r"^FROM (\S+)", text, re.M):
        assert "@sha256:" in line, f"base image not pinned by digest: {line}"
        assert ":latest" not in line
    poc = (ROOT / "docker" / "poc-browser" / "Dockerfile").read_text(encoding="utf-8")
    spec = re.search(r"CAMOUFOX_BROWSER_SPEC=(\S+)", text)
    validated = re.search(r"CAMOUFOX_BROWSER_SPEC=(\S+)", poc)
    assert spec and validated and spec.group(1) == validated.group(1)  # the validated build
    runtime = text.split("AS runtime", 1)[1]
    assert runtime.rstrip().splitlines()[-3:] == ["WORKDIR /app", 'ENTRYPOINT ["rma-portal"]', 'CMD ["--help"]']
    assert re.findall(r"^USER (\S+)", runtime, re.M)[-1] == "rma"


def test_docker_ignore_keeps_migrations_but_never_secrets_or_data():
    lines = (ROOT / "docker" / "prod" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    active = {line for line in lines if line and not line.startswith("#")}
    assert {".env", ".git", "*.sqlite3*", "backups", "script_scrap"} <= active
    assert "alembic" not in active and "alembic.ini" not in active


def test_nginx_proxies_only_the_api_with_security_headers_and_a_login_limit():
    text = NGINX.read_text(encoding="utf-8")
    assert text.count("proxy_pass http://api:8765;") == 2
    for header in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
        assert header in text
    assert "frame-ancestors 'none'" in text and "server_tokens off" in text
    assert "limit_req zone=rma_login" in text and "location = /healthz" in text
    assert "try_files $uri /index.html" in text  # client-side routing


def _working_bash() -> bool:
    """A usable bash (on Windows `bash` may be a WSL launcher with no distribution installed)."""
    if shutil.which("bash") is None:
        return False
    try:
        return subprocess.run(["bash", "-c", "exit 0"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _working_bash(), reason="no working bash available")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_operational_scripts_are_valid_bash_and_stay_inside_the_project(script):
    subprocess.run(["bash", "-n", str(script)], check=True, timeout=30)
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text or script.name == "lib.sh"
    assert "\r" not in text  # LF endings: these run on Ubuntu
    for forbidden in ("system prune", "volume rm", "volume prune", "network rm", "docker rm ", "down -v", "--volumes", "wexia"):
        assert forbidden not in text, f"{script.name} must not use {forbidden!r}"


def test_backup_and_restore_use_the_documented_safe_flow():
    backup = (ROOT / "scripts/prod/backup.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts/prod/restore.sh").read_text(encoding="utf-8")
    assert "pg_dump" in backup and "--format=custom" in backup and "pg_restore --list" in backup
    assert "umask 077" in backup and "chmod 700" in backup and ".sha256" in backup
    assert "--with-session" in backup  # the session archive is opt-in (it holds cookies)
    assert "pg_restore" in restore and "--clean --if-exists" in restore
    assert 'sha256sum -c' in restore and "Type RESTORE" in restore
    assert "run --rm migrate" in restore  # an older dump is upgraded before serving


def test_temp_dir_is_not_used_by_the_stack_definition():
    assert tempfile.gettempdir() not in COMPOSE.read_text(encoding="utf-8")
