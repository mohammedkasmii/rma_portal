"""The agency-server deployment package: Compose definition, scripts and image transfer.

Nothing here touches a server or builds an image. Compose checks use `docker compose config`
(no daemon needed) with non-secret test values; script checks run the real scripts with a fake
`docker` on PATH that records its calls, so they prove *what would run*, not what a server does.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
COMPOSE = ROOT / "compose.agency.yaml"
ENV_EXAMPLE = ROOT / ".env.agency.example"
AGENCY = ROOT / "scripts" / "agency"
SCRIPTS = sorted(AGENCY.glob("*.sh"))
STORAGE = "/data/rma-portal"
VERSION = "0123456789ab"
COMMIT = VERSION + "cdef0123456789abcdef0123456789ab"[: 40 - 12]

needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")


def _find_bash() -> str | None:
    """A usable bash (on Windows `bash` may be a WSL launcher with no distribution installed)."""
    candidates = [shutil.which("bash"), "C:/Program Files/Git/bin/bash.exe"]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            if (
                subprocess.run(
                    [candidate, "-c", "exit 0"], capture_output=True, timeout=20
                ).returncode
                == 0
            ):
                return candidate
        except OSError, subprocess.SubprocessError:
            continue
    return None


_FOUND_BASH = _find_bash()
BASH: str = _FOUND_BASH or "bash"


def _working_bash() -> bool:
    return _FOUND_BASH is not None


needs_bash = pytest.mark.skipif(not _working_bash(), reason="no working bash available")

# Destructive or out-of-scope operations that no agency script/definition may contain.
FORBIDDEN = [
    r"docker\s+(system|image|volume|network|builder|container)\s+prune",
    r"down\s+(-\w*v|--volumes)",
    r"volume\s+rm",
    r"\bnetwork\s+rm\s",
    r"(systemctl|service)\s+(\S+\s+)?restart\s+(\S+\s+)?docker|systemctl\s+restart\s+docker",
    r"daemon\.json",
    r"apt(-get)?\s+(install|upgrade|remove|purge|dist-upgrade)",
    r"\bufw\b",
    r"apache|a2ensite|a2enmod",
    r"\breboot\b|shutdown\s+-r",
    r"privileged\s*:\s*true|--privileged",
    r"network_mode\s*:\s*host|--net(work)?[ =]host",
    r"docker\.sock",
    r"docker\s+compose\s+[^\n]*\bbuild\b|docker\s+build",
    r"docker\s+compose\s+[^\n]*\bpull\b|docker\s+pull",
    r"crontab|systemd|\.timer\b|/etc/cron",
]
# Only the exporter (which runs on the validated VM, never on the server) may build/pull.
BUILD_EXEMPT = {"export-images.sh"}


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _test_env(tmp_path: Path, **overrides: str) -> Path:
    values = {
        "RMA_VERSION": VERSION,
        "POSTGRES_PASSWORD": "test-only-postgres-value",
        "RMA_SESSION_SECRET": "test-only-session-value-0123456789abcdef",
        "RMA_VNC_PASSWORD": "testvnc1",
    }
    values.update(overrides)
    lines = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        if "=" in line and not line.startswith("#") and key in values:
            continue
        lines.append(line)
    lines += [f"{k}={v}" for k, v in values.items()]
    env = tmp_path / "test.env"
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env


def _clean_env() -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES_", "RMA_", "COMPOSE_"))
    }
    env["MSYS_NO_PATHCONV"] = "1"  # Git-Bash on Windows must not rewrite /data/... arguments
    return env


def _compose(env_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "-f", str(COMPOSE), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=_clean_env(),
    )


@pytest.fixture(scope="module")
def stack(tmp_path_factory) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    env = _test_env(tmp_path_factory.mktemp("agency"))
    result = _compose(env, "config", "--format", "json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _load_audit():
    spec = importlib.util.spec_from_file_location("compose_audit", AGENCY / "compose_audit.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["compose_audit"] = module
    spec.loader.exec_module(module)
    return module


AUDIT_ARGS = {"root": STORAGE, "web": ("192.168.1.32", "8480"), "novnc": ("127.0.0.1", "6081")}


# --- Compose definition ---------------------------------------------------------------------------------


@needs_docker
def test_agency_compose_resolves_as_project_rma_portal(stack):
    assert stack["name"] == "rma-portal"
    assert set(stack["services"]) == {"db", "migrate", "api", "worker", "web", "browser"}
    assert list(stack["networks"]) == ["rma-portal-net"]
    for name, service in stack["services"].items():
        assert service["container_name"] == f"rma-portal-{name}"
        assert list(service["networks"]) == ["rma-portal-net"]
    assert "wexia" not in json.dumps(stack).lower()
    assert "shexpert" not in json.dumps(stack).lower()
    assert "supabase" not in json.dumps(stack).lower()


@needs_docker
def test_only_web_and_browser_publish_and_only_on_the_expected_addresses(stack):
    services = stack["services"]
    for name in ("db", "migrate", "api", "worker"):
        assert not services[name].get("ports"), f"{name} must not publish a port"
    (web,) = services["web"]["ports"]
    (novnc,) = services["browser"]["ports"]
    assert (web["host_ip"], web["published"], web["target"]) == ("192.168.1.32", "8480", 8080)
    assert (novnc["host_ip"], novnc["published"], novnc["target"]) == ("127.0.0.1", "6081", 6080)
    assert "0.0.0.0" not in json.dumps([web, novnc])
    assert "100." not in web["host_ip"]  # never the Tailscale address


@needs_docker
def test_bind_addresses_follow_the_environment(tmp_path):
    env = _test_env(
        tmp_path, RMA_WEB_BIND_IP="10.1.2.3", RMA_WEB_PORT="9000", RMA_NOVNC_PORT="9001"
    )
    config = json.loads(_compose(env, "config", "--format", "json").stdout)
    assert config["services"]["web"]["ports"][0]["host_ip"] == "10.1.2.3"
    assert config["services"]["web"]["ports"][0]["published"] == "9000"
    assert config["services"]["browser"]["ports"][0]["published"] == "9001"


@needs_docker
def test_every_persistent_mount_is_an_explicit_bind_under_the_storage_root(stack):
    assert not stack.get("volumes"), "no named volumes may remain"
    expected = {
        "db": {"postgres": "/var/lib/postgresql/data"},
        "migrate": {"app-data": "/var/lib/rma-portal", "logs": "/var/log/rma-portal"},
        "api": {"app-data": "/var/lib/rma-portal", "logs": "/var/log/rma-portal"},
        "worker": {
            "app-data": "/var/lib/rma-portal",
            "logs": "/var/log/rma-portal",
            "browser-profile": "/var/lib/rma-poc/profile",
            "session-state": "/var/lib/rma-poc/state",
        },
        "browser": {
            "browser-profile": "/var/lib/rma-poc/profile",
            "session-state": "/var/lib/rma-poc/state",
        },
        "web": {},
    }
    for name, mounts in expected.items():
        got = {}
        for volume in stack["services"][name].get("volumes", []):
            assert volume["type"] == "bind", f"{name}: {volume}"
            assert volume["bind"]["create_host_path"] is False
            assert volume["source"].startswith(STORAGE + "/")
            assert "/var/lib/docker" not in volume["source"]
            got[volume["source"].removeprefix(STORAGE + "/")] = volume["target"]
        assert got == mounts, name


@needs_docker
def test_every_service_has_limits_and_bounded_logs_and_long_running_ones_health_and_restart(stack):
    limits = {
        "db": (1, 2 << 30, 256),
        "migrate": (1, 1 << 30, 256),
        "api": (1, 1 << 30, 256),
        "worker": (2, 3 << 30, 512),
        "browser": (2, 3 << 30, 512),
        "web": (0.5, 256 << 20, 128),
    }
    for name, (cpus, memory, pids) in limits.items():
        service = stack["services"][name]
        assert float(service["cpus"]) == cpus, name
        assert int(service["mem_limit"]) == memory, name
        assert int(service["memswap_limit"]) == memory, name  # no swap use
        assert int(service["pids_limit"]) == pids, name
        assert service["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "5"},
        }
        if name != "migrate":
            assert service["restart"] == "unless-stopped"
            assert service["healthcheck"]["test"]
            assert int(service["mem_reservation"]) < int(service["mem_limit"])
    for name in ("worker", "browser"):
        assert int(stack["services"][name]["shm_size"]) == 1 << 30


@needs_docker
def test_containers_stay_hardened(stack):
    for name, service in stack["services"].items():
        assert "ALL" in service["cap_drop"], name
        assert "no-new-privileges:true" in service["security_opt"], name
        assert not service.get("privileged"), name
        assert service.get("network_mode") != "host", name
        assert service.get("pid") != "host" and service.get("ipc") != "host", name
        assert "docker.sock" not in json.dumps(service.get("volumes", [])), name
        assert not service.get("build"), f"{name}: production never builds"
        assert service["pull_policy"] == "never", name
    assert set(stack["services"]["db"]["cap_add"]) == {
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
    }
    assert set(stack["services"]["web"]["cap_add"]) == {"CHOWN", "SETGID", "SETUID"}
    for name in ("api", "worker", "browser", "migrate"):
        assert not stack["services"][name].get("cap_add"), name
    assert stack["services"]["api"]["read_only"] and stack["services"]["web"]["read_only"]


@needs_docker
def test_images_are_immutable_and_share_one_version(stack):
    images = {n: s["image"] for n, s in stack["services"].items()}
    assert images == {
        "db": f"rma-portal-postgres:{VERSION}",
        "web": f"rma-portal-web:{VERSION}",
        "migrate": f"rma-portal:{VERSION}",
        "api": f"rma-portal:{VERSION}",
        "worker": f"rma-portal:{VERSION}",
        "browser": f"rma-portal:{VERSION}",
    }
    text = COMPOSE.read_text(encoding="utf-8")
    assert ":latest" not in text and "2.0.0" not in text


@needs_docker
@pytest.mark.parametrize(
    "variable",
    [
        "RMA_VERSION",
        "POSTGRES_PASSWORD",
        "RMA_SESSION_SECRET",
        "RMA_VNC_PASSWORD",
        "RMA_WEB_BIND_IP",
        "RMA_STORAGE_ROOT",
    ],
)
def test_mandatory_values_have_no_default_and_fail_loudly(tmp_path, variable):
    env = _test_env(tmp_path, **{variable: ""})
    result = _compose(env, "config", "-q")
    assert result.returncode != 0
    assert variable in result.stderr


@needs_docker
def test_ai_is_disabled_and_polling_is_hourly_by_default(stack):
    worker_env = stack["services"]["worker"]["environment"]
    assert worker_env["RMA_PORTAL_OLLAMA_ENABLED"] == "false"
    assert worker_env["RMA_PORTAL_POLL_INTERVAL_SECONDS"] == "3600"
    assert worker_env["RMA_PORTAL_COOKIE_SECURE"] == "false"


def test_env_example_documents_every_variable_and_holds_no_secret():
    compose = COMPOSE.read_text(encoding="utf-8")
    used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)", compose))
    documented = {
        line.split("=", 1)[0]
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert used <= documented, sorted(used - documented)
    values = dict(
        line.split("=", 1)
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    for secret in ("POSTGRES_PASSWORD", "RMA_SESSION_SECRET", "RMA_VNC_PASSWORD", "RMA_VERSION"):
        assert values[secret] == "", f"{secret} must be empty in the example"
    assert values["RMA_STORAGE_ROOT"] == "/data/rma-portal"
    assert values["RMA_WEB_BIND_IP"] == "192.168.1.32" and values["RMA_WEB_PORT"] == "8480"
    assert values["RMA_NOVNC_BIND_IP"] == "127.0.0.1" and values["RMA_NOVNC_PORT"] == "6081"
    assert values["RMA_PUBLIC_ORIGIN"] == "http://192.168.1.32:8480"
    assert values["RMA_NOVNC_URL"] == "http://127.0.0.1:6081/vnc.html"
    assert values["RMA_COOKIE_SECURE"] == "false" and values["RMA_OLLAMA_ENABLED"] == "false"
    assert (
        values["RMA_POLL_INTERVAL_SECONDS"] == "3600"
        and values["RMA_MAX_DETAIL_READS_PER_CYCLE"] == "60"
    )
    assert values["RMA_TIMEZONE"] == "Africa/Casablanca"


def test_the_validated_vm_definition_is_unchanged():
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    diff = subprocess.run(
        ["git", "diff", "--quiet", "ac386ac", "--", "compose.prod.yaml", ".env.example", "docker"],
        cwd=ROOT,
        capture_output=True,
    )
    if diff.returncode not in (0, 1):
        pytest.skip("base commit ac386ac not available")
    assert diff.returncode == 0, "compose.prod.yaml / .env.example / docker/ must stay untouched"


# --- compose audit helper (what preflight relies on) ------------------------------------------------------


@needs_docker
def test_audit_accepts_the_resolved_stack_and_rejects_each_violation(stack):
    audit = _load_audit().audit
    assert [level for level, _ in audit(stack, **AUDIT_ARGS)] == ["OK"]

    def broken(mutate):
        config = copy.deepcopy(stack)
        mutate(config)
        return [m for level, m in audit(config, **AUDIT_ARGS) if level == "FAIL"]

    assert broken(
        lambda c: c["services"]["api"].update(ports=[{"target": 8765, "published": "8765"}])
    )
    assert broken(lambda c: c["services"]["web"]["ports"][0].update(host_ip="0.0.0.0"))
    assert broken(lambda c: c["services"]["browser"]["ports"][0].update(host_ip="192.168.1.32"))
    assert broken(lambda c: c["services"]["db"].update(privileged=True))
    assert broken(lambda c: c["services"]["worker"].update(network_mode="host"))
    assert broken(lambda c: c["services"]["worker"].pop("pids_limit"))
    assert broken(lambda c: c["services"]["worker"].update(cap_drop=[]))
    assert broken(
        lambda c: c["services"]["worker"]["volumes"].append(
            {"type": "bind", "source": "/var/run/docker.sock", "target": "/x"}
        )
    )
    assert broken(lambda c: c["services"]["db"]["volumes"][0].update(source="/var/lib/docker/pg"))
    assert broken(lambda c: c["services"]["worker"].update(image="rma-portal:latest"))
    assert broken(
        lambda c: c["services"]["worker"]["environment"].update(RMA_PORTAL_OLLAMA_ENABLED="true")
    )
    assert broken(lambda c: c.update(volumes={"pgdata": {}}))


# --- scripts ----------------------------------------------------------------------------------------------------


@needs_bash
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_are_valid_lf_bash_and_free_of_forbidden_operations(script):
    subprocess.run([BASH, "-n", str(script)], check=True, timeout=30)
    raw = script.read_bytes()
    assert b"\r" not in raw
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text or script.name in {"lib.sh", "preflight.sh"}
    code = _strip_comments(text)
    for pattern in FORBIDDEN:
        if script.name in BUILD_EXEMPT and re.search("build|pull", pattern):
            continue
        assert not re.search(pattern, code, re.IGNORECASE), f"{script.name} matches {pattern!r}"
    assert not re.search(r"\b(chown|chmod)\s+(-\w*R|--recursive)", code), "no recursive chown/chmod"


def test_compose_definition_contains_no_forbidden_operation():
    code = _strip_comments(COMPOSE.read_text(encoding="utf-8"))
    for pattern in FORBIDDEN:
        assert not re.search(pattern, code, re.IGNORECASE), pattern
    assert "build:" not in code


@needs_bash
def test_scripts_are_committed_executable():
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    out = subprocess.run(
        ["git", "ls-files", "-s", "scripts/agency"], cwd=ROOT, capture_output=True, text=True
    )
    modes = {line.split()[0] for line in out.stdout.splitlines()}
    assert modes <= {"100755"} and modes, modes


def test_preflight_is_read_only_by_construction():
    text = _strip_comments((AGENCY / "preflight.sh").read_text(encoding="utf-8"))
    mutating = (
        r"\b(mkdir|touch|chown|chmod|rm|mv|cp|tee|dd)\b",
        r"docker\s+(run|start|stop|restart|kill|rm|rmi|exec|pull|build|load|tag|create|volume\s+create|network\s+create)",
        r"compose[^\n]*\s(up|start|stop|restart|down|run|pull|build|create)\b",
        r"(?:^|\s)\d?>>?\s*[^&=\s]",
    )
    for pattern in mutating:
        hits = [line for line in text.splitlines() if re.search(pattern, line)]
        hits = [h for h in hits if "/dev/null" not in h and "2>&1" not in h]
        assert not hits, (pattern, hits)


def _run(args: list[str], *, env: dict[str, str] | None = None, cwd: Path = ROOT):
    return subprocess.run(
        [BASH, *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env or _clean_env(),
        cwd=cwd,
    )


def _not_root() -> bool:
    out = subprocess.run([BASH, "-c", "id -u"], capture_output=True, text=True)
    return out.stdout.strip() != "0"


@needs_bash
def test_prepare_storage_is_dry_run_by_default_and_prints_exact_operations():
    result = _run(["scripts/agency/prepare-storage.sh"])
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "DRY-RUN" in out and "nothing was changed" in out
    for line in (
        "mkdir /data/rma-portal/postgres",
        "chown 70:70 /data/rma-portal/postgres",
        "chmod 0700 /data/rma-portal/postgres",
        "chown 10001:10001 /data/rma-portal/app-data",
        "chmod 0750 /data/rma-portal/logs",
        "chmod 0700 /data/rma-portal/session-state",
        "chmod 0700 /data/rma-portal/browser-profile",
        "chmod 0700 /data/rma-portal/backups",
        "chmod 0700 /data/rma-portal/config",
        "chown 1000:1000 /data/rma-portal/releases",
        "chown 1000:1000 /data/rma-portal/image-bundles",
    ):
        assert line in out, line
    # /data itself is never a target
    assert not re.search(r"(mkdir|chown|chmod)[^\n]* /data\n", out)


@needs_bash
@pytest.mark.parametrize(
    "root",
    [
        "",
        "/",
        "/data",
        "/var",
        "/var/lib/docker",
        "/var/lib/docker/rma",
        "rma-portal",
        "data/rma",
        "/data/../etc",
        "/data/rma-portal/",
        "/data/a/b",
        "/etc",
    ],
)
def test_prepare_storage_rejects_dangerous_roots(root):
    result = _run(["scripts/agency/prepare-storage.sh", "--root", root])
    assert result.returncode != 0
    assert "refusing" in result.stderr or "must be" in result.stderr
    assert "planned operations" not in result.stdout


@needs_bash
@pytest.mark.skipif(
    not (_working_bash() and _not_root()), reason="must not run --apply as root in a test"
)
def test_prepare_storage_apply_needs_root():
    result = _run(["scripts/agency/prepare-storage.sh", "--apply"])
    assert result.returncode != 0
    assert "needs root" in result.stderr


def _safe_ref(ref: str) -> str:
    return ref.replace(":", "_").replace("/", "_")


def _fake_toolbox(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """PATH with a recording fake `docker` and a `python3` that is the current interpreter.

    After a fake `docker load`, `image inspect --format {{.Id}} REF` prints ids/<REF>; before it,
    pre/<REF> (an already-present tag) or a failure. `docker compose` is forwarded to the real CLI.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for sub in ("ids", "pre"):
        (tmp_path / sub).mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    state = tmp_path / "loaded"
    real = (shutil.which("docker") or "").replace("\\", "/")
    (bin_dir / "docker").write_text(
        f"""#!/bin/sh
echo "$@" >> "{log.as_posix()}"
for a; do last="$a"; done
safe="$(echo "$last" | tr ':/' '__')"
case "$*" in
  "compose "*) exec "{real}" "$@" ;;
  "load "*) : > "{state.as_posix()}"; exit 0 ;;
  "image inspect --format {{{{.Id}}}} "*)
     if [ -f "{tmp_path.as_posix()}/pre/$safe" ]; then cat "{tmp_path.as_posix()}/pre/$safe"; exit 0; fi
     if [ -f "{state.as_posix()}" ] && [ -f "{tmp_path.as_posix()}/ids/$safe" ]; then
        cat "{tmp_path.as_posix()}/ids/$safe"; exit 0; fi
     exit 1 ;;
  "image inspect --format {{{{.Os}}}}/{{{{.Architecture}}}} "*) echo linux/amd64; exit 0 ;;
  "image inspect --format {{{{.Architecture}}}} "*) echo amd64; exit 0 ;;
  "image inspect --format {{{{index .Config.Labels"*) exit 0 ;;
  "version --format {{{{.Server.Arch}}}}") echo amd64; exit 0 ;;
esac
exit 0
""",
        encoding="utf-8",
        newline="\n",
    )
    (bin_dir / "python3").write_text(
        "#!/bin/sh\n"
        "if command -v cygpath >/dev/null 2>&1; then\n"  # Git-Bash on Windows: native python needs C:\ paths
        '  n=$#; i=0; while [ $i -lt $n ]; do a="$1"; shift; case "$a" in /*) a="$(cygpath -w "$a")" ;; esac;'
        ' set -- "$@" "$a"; i=$((i+1)); done\n'
        "fi\n"
        f'exec "{Path(sys.executable).as_posix()}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    for tool in ("docker", "python3"):
        (bin_dir / tool).chmod((bin_dir / tool).stat().st_mode | stat.S_IEXEC)
    env = _clean_env()
    env["PATH"] = f"{bin_dir.as_posix()}{os.pathsep}{env['PATH']}"
    return env, log


APP_REFS = [f"rma-portal:{VERSION}", f"rma-portal-web:{VERSION}", f"rma-portal-postgres:{VERSION}"]
SOURCE_ENGINE_ID = (
    "sha256:" + "ee" * 32
)  # what a BuildKit/containerd source store may report: NOT portable


def _docker_archive(
    path: Path,
    refs: list[str] | None = None,
    *,
    layout: str = "classic",
    omit_config: str | None = None,
    duplicate_tag: str | None = None,
    drop_tag: str | None = None,
    unsafe_member: str | None = None,
    bad_digest: bool = False,
) -> dict[str, str]:
    """A synthetic `docker save` archive; returns {ref: portable config ID}."""
    refs = list(refs or APP_REFS)
    ids: dict[str, str] = {}
    entries: list[dict] = []

    def add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with tarfile.open(path, "w") as tar:
        for index, ref in enumerate(refs):
            config = json.dumps({"architecture": "amd64", "os": "linux", "ref": ref}).encode()
            digest = hashlib.sha256(config).hexdigest()
            member = f"{digest}.json" if layout == "classic" else f"blobs/sha256/{digest}"
            if ref != omit_config:
                add(tar, member, config + (b" " if bad_digest and index == 0 else b""))
            ids[ref] = f"sha256:{digest}"
            if ref != drop_tag:
                entries.append({"Config": member, "RepoTags": [ref], "Layers": []})
        if duplicate_tag:
            entries.append(
                {"Config": entries[0]["Config"], "RepoTags": [duplicate_tag], "Layers": []}
            )
        add(tar, "manifest.json", json.dumps(entries).encode())
        if unsafe_member:
            add(tar, unsafe_member, b"x")
    return ids


def _make_bundle(
    tmp_path: Path,
    *,
    corrupt: bool = False,
    layout: str = "classic",
    manifest_ids: dict | None = None,
) -> Path:
    """Bundle with a real synthetic archive; fake-docker post-load IDs are the portable config IDs."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    tar = bundle / f"rma-portal-images-{VERSION}.tar"
    ids = _docker_archive(tar, layout=layout)
    (tmp_path / "ids").mkdir(exist_ok=True)
    for ref, image_id in ids.items():
        (tmp_path / "ids" / _safe_ref(ref)).write_bytes(image_id.encode() + b"\n")
    digest = subprocess.run(
        ["sha256sum", tar.name], cwd=bundle, capture_output=True, text=True
    ).stdout
    (bundle / (tar.name + ".sha256")).write_text(digest, encoding="utf-8", newline="\n")
    manifest = bundle / f"rma-portal-images-{VERSION}.manifest.json"
    args = [
        sys.executable, str(AGENCY / "manifest.py"), "write", "--output", str(manifest),
        "--commit", COMMIT, "--version", VERSION, "--architecture", "amd64", "--archive", tar.name,
        "--archive-sha256", digest.split()[0], "--archive-bytes", str(tar.stat().st_size),
        "--postgres-source", "postgres:16-alpine@sha256:" + "1" * 64,
    ]  # fmt: skip
    for ref in APP_REFS:
        config_id = (manifest_ids or ids)[ref]
        args += ["--image", f"{ref}|{config_id}|{SOURCE_ENGINE_ID}|registry/x@{SOURCE_ENGINE_ID}"]
    subprocess.run(args, check=True)
    if corrupt:
        tar.write_bytes(b"tampered after checksum")
    return tar


def _docker_calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@needs_bash
def test_loader_is_dry_run_by_default(tmp_path):
    env, log = _fake_toolbox(tmp_path)
    tar = _make_bundle(tmp_path)
    result = _run(["scripts/agency/load-images.sh", tar.as_posix()], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry-run complete" in result.stdout and "would load" in result.stdout
    assert not any(call.startswith("load") for call in _docker_calls(log))


@needs_bash
def test_loader_verifies_the_checksum_before_any_docker_load(tmp_path):
    env, log = _fake_toolbox(tmp_path)
    tar = _make_bundle(tmp_path, corrupt=True)
    for extra in ([], ["--apply"]):
        result = _run(["scripts/agency/load-images.sh", tar.as_posix(), *extra], env=env)
        assert result.returncode != 0
        assert "checksum" in (result.stdout + result.stderr).lower()
    assert not any(call.startswith("load") for call in _docker_calls(log))


@needs_bash
def test_loader_loads_only_with_apply_and_then_confirms_image_ids(tmp_path):
    env, log = _fake_toolbox(tmp_path)
    tar = _make_bundle(tmp_path)
    result = _run(["scripts/agency/load-images.sh", tar.as_posix(), "--apply"], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _docker_calls(log)
    assert [c for c in calls if c.startswith("load")] == [f"load --input {tar.as_posix()}"]
    assert len(re.findall(r"^  OK  rma-portal", result.stdout, re.MULTILINE)) == 3
    assert not any(re.search(r"\b(rmi|prune|pull|build|rm)\b", call) for call in calls)


@needs_bash
def test_verify_bundle_reports_manifest_and_is_read_only(tmp_path):
    env, log = _fake_toolbox(tmp_path)
    tar = _make_bundle(tmp_path)
    result = _run(["scripts/agency/verify-bundle.sh", tar.as_posix()], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert COMMIT in result.stdout and f"rma-portal:{VERSION}" in result.stdout
    assert _docker_calls(log) == []


@needs_bash
def test_export_refuses_a_dirty_working_tree_before_building(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    shutil.copytree(AGENCY, repo / "scripts" / "agency")
    (repo / "compose.prod.yaml").write_text("services: {}\n", encoding="utf-8")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], cwd=repo, check=True)
    (repo / "untracked.txt").write_text("dirty", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    env, log = _fake_toolbox(tmp_path)
    result = _run(
        ["scripts/agency/export-images.sh", "--output-dir", out_dir.as_posix()], env=env, cwd=repo
    )
    assert result.returncode != 0
    assert "dirty" in result.stderr
    assert not any(c.startswith(("build", "save")) for c in _docker_calls(log))
    assert list(out_dir.iterdir()) == []


def test_export_derives_immutable_tags_from_the_commit_and_keeps_secrets_out():
    text = (AGENCY / "export-images.sh").read_text(encoding="utf-8")
    assert 'VERSION="${COMMIT:0:12}"' in text and "git rev-parse HEAD" in text
    assert "git status --porcelain" in text
    assert 'APP="rma-portal:$VERSION"' in text and 'WEB="rma-portal-web:$VERSION"' in text
    assert "docker save" in text and "sha256sum" in text and "manifest.py" in text
    assert "git archive" in text and ".env " not in text.replace(".env.agency.example", "")
    prod = (ROOT / "compose.prod.yaml").read_text(encoding="utf-8")
    assert "postgres:16-alpine@sha256:" in prod  # the pinned source the exporter re-tags


def test_backup_script_contract():
    text = (AGENCY / "backup.sh").read_text(encoding="utf-8")
    code = _strip_comments(text)
    assert "--format=custom" in code and "pg_restore --list" in code
    assert "sha256sum" in code and ".manifest" in code and ".complete" in code
    assert code.index(".complete.partial") > code.index(".manifest")  # marker written last
    assert "--with-session" in code
    assert '"$ROOT/backups"' in code and "RMA_DEFAULT_STORAGE_ROOT" in code
    assert "umask 077" in code
    assert not re.search(r"\.env\b(?!\.)[^\n]*(tar|cp |cat )", code.replace('"$ENV_FILE"', ""))
    for line in code.splitlines():
        if re.search(r"(?<![-\w])rm\s", line) and "cleanup" not in line:
            assert "$BACKUP_DIR" in line, line  # deletion only inside the backup directory
    assert "cron" not in code.lower() and "systemd" not in code.lower()


def test_restore_rehearsal_is_isolated():
    code = _strip_comments((AGENCY / "restore-rehearsal.sh").read_text(encoding="utf-8"))
    for token in (
        "--network none",
        "--tmpfs /var/lib/postgresql/data",
        "--cap-drop ALL",
        "--pull never",
        'NAME="rma-portal-restore-rehearsal"',
        "sha256sum -c",
        ".complete",
    ):
        assert token in code, token
    assert (
        " -p " not in code
        and "--publish" not in code
        and "-v " not in code
        and "--volume" not in code
    )
    for line in code.splitlines():
        if "docker rm" in line:
            assert '"$NAME"' in line


# --- preflight collision handling: strict first deployment vs --existing-rma upgrade ----------------------

FAKE_ENGINE = """#!/bin/sh
d="$FAKE_DIR"
echo "$@" >> "$d/calls.log"
case "$1" in
  version) echo 29.7.2 ;;
  info) echo /var/lib/docker ;;
  ps)
    [ $# -eq 1 ] && exit 0
    case "$*" in
      *" -q"*) cat "$d/ids" ;;
      *"{{.Ports}}"*) cat "$d/ports" ;;
      *) cat "$d/listing" ;;
    esac ;;
  network)
    case "$2" in ls) cat "$d/networks" ;; inspect) cat "$d/netlabel" ;; esac ;;
  inspect) for a; do n="$a"; done; cat "$d/mounts_$n" 2>/dev/null ;;
esac
exit 0
"""
GOOD_NAMES = ["db", "api", "web", "browser", "worker"]
GOOD_PORTS = {
    "db": "5432/tcp",
    "api": "8765/tcp",
    "web": "192.168.1.32:8480->8080/tcp",
    "browser": "127.0.0.1:6081->6080/tcp",
    "worker": "",
}
MOUNT_DIRS = {
    "db": "postgres",
    "api": "app-data",
    "web": "",
    "browser": "session-state",
    "worker": "logs",
}


def _w(path: Path, text: str) -> None:
    path.write_bytes(text.encode())  # LF only: the fake engine is read by sh


def _scenario(
    tmp_path: Path, *, listing=None, ports=None, mounts=None, netlabel="rma-portal"
) -> dict:
    fake = tmp_path / "fake"
    fake.mkdir()
    rows = listing or [
        (f"rma-portal-{n}", "Up 2 hours (healthy)", "rma-portal") for n in GOOD_NAMES
    ]
    _w(fake / "listing", "\n".join("|".join(r) for r in rows) + "\nother-app|Up 1 day|shexpert\n")
    port_map = {f"rma-portal-{n}": p for n, p in GOOD_PORTS.items()} | (ports or {})
    _w(fake / "ports", "\n".join(f"{n}|{p}" for n, p in port_map.items()) + "\n")
    _w(fake / "ids", "aa\nbb\n")
    _w(fake / "networks", "bridge\nrma-portal-net\n")
    _w(fake / "netlabel", netlabel + "\n")
    for n in GOOD_NAMES:
        default = f"bind {STORAGE}/{MOUNT_DIRS[n]}\n" if MOUNT_DIRS[n] else ""
        _w(fake / f"mounts_rma-portal-{n}", (mounts or {}).get(f"rma-portal-{n}", default))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_ENGINE, encoding="utf-8", newline="\n")
    (bin_dir / "docker").chmod(0o755)
    env = _clean_env()
    env["PATH"] = f"{bin_dir.as_posix()}{os.pathsep}{env['PATH']}"
    env["FAKE_DIR"] = fake.as_posix()
    return env


def _preflight_out(env: dict, *flags: str) -> str:
    result = _run(["scripts/agency/preflight.sh", "--pre-config", *flags], env=env)
    return result.stdout


def _fails(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("  [FAIL]")]


@needs_bash
def test_strict_initial_mode_rejects_any_existing_rma_resource(tmp_path):
    fails = "\n".join(_fails(_preflight_out(_scenario(tmp_path))))
    assert "container name collision: rma-portal-db" in fails
    assert "network name collision: rma-portal-net" in fails


@needs_bash
def test_upgrade_mode_accepts_correctly_labelled_rma_resources_and_stays_read_only(tmp_path):
    env = _scenario(tmp_path)
    out = _preflight_out(env, "--existing-rma")
    assert "existing RMA resources (5 containers) are genuine" in out
    assert "rma-portal-net belongs to project rma-portal" in out
    rma_fails = [f for f in _fails(out) if re.search(r"rma-portal|RMA-prefixed|publishes|mount", f)]
    assert not rma_fails, rma_fails
    calls = (tmp_path / "fake" / "calls.log").read_text().splitlines()
    for call in calls:
        assert not re.match(
            r"(run|start|stop|restart|rm|rmi|kill|exec|pull|build|load|create|prune)\b", call
        ), call
        if call.startswith("inspect"):
            assert "{{range .Mounts}}" in call and "Env" not in call and "Config" not in call, call


@needs_bash
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("foreign_label", "rma-portal-api belongs to Compose project 'shexpert'"),
        ("unknown_name", "unknown RMA-prefixed container: rma-portal-extra"),
        ("mount_outside", "mount /var/lib/docker/volumes/x/_data is outside /data/rma-portal"),
        (
            "mount_nested",
            "mount /data/rma-portal/app-data/sub is not a direct child of /data/rma-portal",
        ),
        ("mount_volume", "has a non-bind mount (volume /data/rma-portal/postgres)"),
        ("network_owner", "rma-portal-net belongs to 'other', not rma-portal"),
        (
            "web_all_interfaces",
            "rma-portal-web publishes '0.0.0.0:8480->8080/tcp', expected '192.168.1.32:8480->8080/tcp'",
        ),
        ("web_tailscale", "rma-portal-web publishes '100.89.63.25:8480->8080/tcp'"),
        (
            "browser_lan",
            "rma-portal-browser publishes '192.168.1.32:6081->6080/tcp', expected '127.0.0.1:6081->6080/tcp'",
        ),
        ("api_published", "rma-portal-api publishes '0.0.0.0:8765->8765/tcp', expected 'nothing'"),
    ],
)
def test_upgrade_mode_rejects_foreign_or_misconfigured_rma_resources(tmp_path, case, expected):
    base = [(f"rma-portal-{n}", "Up 2 hours (healthy)", "rma-portal") for n in GOOD_NAMES]
    kwargs: dict = {}
    if case == "foreign_label":
        base[1] = ("rma-portal-api", "Up", "shexpert")
        kwargs["listing"] = base
    elif case == "unknown_name":
        kwargs["listing"] = [*base, ("rma-portal-extra", "Up", "rma-portal")]
    elif case == "mount_outside":
        kwargs["mounts"] = {"rma-portal-db": "bind /var/lib/docker/volumes/x/_data\n"}
    elif case == "mount_nested":
        kwargs["mounts"] = {"rma-portal-api": f"bind {STORAGE}/app-data/sub\n"}
    elif case == "mount_volume":
        kwargs["mounts"] = {"rma-portal-db": f"volume {STORAGE}/postgres\n"}
    elif case == "network_owner":
        kwargs["netlabel"] = "other"
    elif case == "web_all_interfaces":
        kwargs["ports"] = {"rma-portal-web": "0.0.0.0:8480->8080/tcp"}
    elif case == "web_tailscale":
        kwargs["ports"] = {"rma-portal-web": "100.89.63.25:8480->8080/tcp"}
    elif case == "browser_lan":
        kwargs["ports"] = {"rma-portal-browser": "192.168.1.32:6081->6080/tcp"}
    elif case == "api_published":
        kwargs["ports"] = {"rma-portal-api": "0.0.0.0:8765->8765/tcp"}
    out = _preflight_out(_scenario(tmp_path, **kwargs), "--existing-rma")
    assert any(expected in line for line in _fails(out)), (expected, _fails(out))


# --- portable image IDs: archive config digests, not source-engine IDs -------------------------------------


def _archive_ids():
    spec = importlib.util.spec_from_file_location("archive_ids", AGENCY / "archive_ids.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["archive_ids"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("layout", ["classic", "oci"])
def test_archive_ids_are_the_config_digests_for_both_config_layouts(tmp_path, layout):
    module = _archive_ids()
    tar = tmp_path / "images.tar"
    expected = _docker_archive(tar, layout=layout)
    got = module.ids_for(str(tar), APP_REFS)
    assert got == expected
    assert SOURCE_ENGINE_ID not in got.values()
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in got.values())
    assert [p.name for p in tmp_path.iterdir()] == ["images.tar"]  # nothing was extracted


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"drop_tag": APP_REFS[1]}, "tag not found"),
        ({"duplicate_tag": APP_REFS[0]}, "duplicate tag"),
        ({"omit_config": APP_REFS[2]}, "config member missing"),
        ({"unsafe_member": "../escape.json"}, "unsafe member path"),
        ({"unsafe_member": "/etc/passwd"}, "unsafe member path"),
        ({"bad_digest": True}, "config digest mismatch"),
    ],
)
def test_archive_ids_reject_malformed_or_unsafe_archives(tmp_path, options, message):
    module = _archive_ids()
    tar = tmp_path / "images.tar"
    _docker_archive(tar, **options)
    with pytest.raises(module.ArchiveError, match=message):
        module.ids_for(str(tar), APP_REFS)
    assert [p.name for p in tmp_path.iterdir()] == ["images.tar"]


def test_archive_ids_reject_malformed_manifest_json(tmp_path):
    module = _archive_ids()
    tar = tmp_path / "images.tar"
    with tarfile.open(tar, "w") as archive:
        info = tarfile.TarInfo("manifest.json")
        info.size = 5
        archive.addfile(info, io.BytesIO(b"{not "))
    with pytest.raises(module.ArchiveError, match="malformed"):
        module.ids_for(str(tar), APP_REFS)


@needs_bash
@pytest.mark.parametrize("layout", ["classic", "oci"])
def test_manifest_uses_the_portable_id_and_labels_source_ids_informational(tmp_path, layout):
    tar = _make_bundle(tmp_path, layout=layout)
    manifest = tar.with_suffix(".manifest.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["schema"] == 2
    for image in data["images"]:
        assert image["archive_config_id"] != image["source_engine_id"] == SOURCE_ENGINE_ID
        assert "id" not in image
    base = [sys.executable, str(AGENCY / "manifest.py")]
    listed = subprocess.run(
        [*base, "images", str(manifest)], capture_output=True, text=True, check=True
    ).stdout
    assert SOURCE_ENGINE_ID not in listed
    shown = subprocess.run(
        [*base, "show", str(manifest)], capture_output=True, text=True, check=True
    ).stdout
    assert "(informational) source_engine_id" in shown and "archive_config_id=" in shown


@needs_bash
def test_verify_bundle_rejects_manifest_ids_that_do_not_match_the_tar(tmp_path):
    env, log = _fake_toolbox(tmp_path)
    wrong = dict.fromkeys(APP_REFS, SOURCE_ENGINE_ID)  # e.g. an old manifest that stored engine IDs
    tar = _make_bundle(tmp_path, manifest_ids=wrong)
    result = _run(["scripts/agency/verify-bundle.sh", tar.as_posix()], env=env)
    assert result.returncode != 0
    assert "archive config BAD" in result.stderr
    assert not any(call.startswith("load") for call in _docker_calls(log))


@needs_bash
def test_verify_bundle_rejects_a_missing_tag_in_the_tar(tmp_path):
    env, log = _fake_toolbox(tmp_path)
    tar = _make_bundle(tmp_path)
    # Same manifest, but a tar (with a matching checksum) that lacks one required tag.
    _docker_archive(tar, drop_tag=APP_REFS[1])
    digest = subprocess.run(
        ["sha256sum", tar.name], cwd=tar.parent, capture_output=True, text=True
    ).stdout
    (tar.parent / (tar.name + ".sha256")).write_text(digest, encoding="utf-8", newline="\n")
    manifest = tar.with_suffix(".manifest.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["archive"]["sha256"] = digest.split()[0]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    result = _run(["scripts/agency/verify-bundle.sh", tar.as_posix()], env=env)
    assert result.returncode != 0 and "tag not found" in result.stderr
    assert _docker_calls(log) == []


@needs_bash
def test_loader_dry_run_accepts_an_existing_tag_with_the_portable_id(tmp_path):
    env, log = _fake_toolbox(tmp_path)
    tar = _make_bundle(tmp_path)
    ref = APP_REFS[0]
    portable = (tmp_path / "ids" / _safe_ref(ref)).read_bytes()
    (tmp_path / "pre" / _safe_ref(ref)).write_bytes(portable)
    result = _run(["scripts/agency/load-images.sh", tar.as_posix()], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"already loaded, identical: {ref}" in result.stdout
    assert not any(call.startswith("load") for call in _docker_calls(log))


@needs_bash
@pytest.mark.parametrize("extra", [[], ["--apply"]])
def test_loader_rejects_a_conflicting_existing_tag_before_any_load(tmp_path, extra):
    env, log = _fake_toolbox(tmp_path)
    tar = _make_bundle(tmp_path)
    (tmp_path / "pre" / _safe_ref(APP_REFS[1])).write_bytes(SOURCE_ENGINE_ID.encode() + b"\n")
    result = _run(["scripts/agency/load-images.sh", tar.as_posix(), *extra], env=env)
    assert result.returncode != 0
    assert f"CONFLICT: {APP_REFS[1]}" in result.stderr
    calls = _docker_calls(log)
    assert not any(call.startswith("load") for call in calls)
    assert not any(re.match(r"(rmi|tag|rm|image (rm|tag|prune))\b", call) for call in calls)


@needs_bash
def test_loader_post_load_check_uses_the_portable_id_not_the_source_engine_id(tmp_path):
    env, log = _fake_toolbox(tmp_path)
    tar = _make_bundle(tmp_path)
    # the engine after load reports the OLD (source) ID for one tag -> must be rejected
    (tmp_path / "ids" / _safe_ref(APP_REFS[2])).write_bytes(SOURCE_ENGINE_ID.encode() + b"\n")
    result = _run(["scripts/agency/load-images.sh", tar.as_posix(), "--apply"], env=env)
    assert result.returncode != 0
    assert f"BAD {APP_REFS[2]}" in result.stderr


@needs_bash
@needs_docker
@pytest.mark.parametrize("consistent", [True, False])
def test_preflight_require_images_compares_the_portable_ids(tmp_path, consistent):
    env, _ = _fake_toolbox(tmp_path)
    tar = _make_bundle(tmp_path)
    manifest = tar.with_suffix(".manifest.json")
    (tmp_path / "loaded").write_bytes(b"")  # images present after a load
    if not consistent:
        (tmp_path / "ids" / _safe_ref(APP_REFS[0])).write_bytes(SOURCE_ENGINE_ID.encode() + b"\n")
    env_file = _test_env(tmp_path)
    result = _run(
        [
            "scripts/agency/preflight.sh",
            "--env-file",
            env_file.as_posix(),
            "--compose-file",
            COMPOSE.as_posix(),
            "--require-images",
            "--manifest",
            manifest.as_posix(),
        ],
        env=env,
    )
    out = result.stdout
    for ref in APP_REFS[1:]:
        assert f"[ OK ] {ref} image ID matches the manifest" in out, out
    if consistent:
        assert f"[ OK ] {APP_REFS[0]} image ID matches the manifest" in out, out
    else:
        assert f"[FAIL] {APP_REFS[0]} image ID differs from the manifest" in out, out


def test_docs_distinguish_the_authoritative_archive_config_id():
    text = (ROOT / "docs" / "agency-production-deployment.md").read_text(encoding="utf-8")
    assert "archive_config_id" in text and "informational" in text.lower()
    assert "manifest-list" in text
