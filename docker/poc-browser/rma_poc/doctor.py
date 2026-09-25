"""Read-only infrastructure diagnostics (``python -m rma_poc doctor``).

Never launches Camoufox, never writes or removes anything, and needs no
OmegaFlow credentials. Output is check names, OK/FAIL, versions, booleans and
milliseconds only -- no cookie/storage data, HTML, or URLs beyond the host.

The pure helpers here are imported by ``healthcheck.sh`` too, so this module
must stay light: stdlib at import time, Camoufox imported lazily.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from urllib.parse import urlsplit

NOVNC_URL = "http://127.0.0.1:6080/vnc.html"
REQUIRED_PROCESSES = ("Xvfb", "x11vnc", "websockify", "openbox")


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True  # informational checks never fail the doctor

    def line(self) -> str:
        status = "OK  " if self.ok else ("FAIL" if self.required else "INFO")
        return f"[{status}] {self.name}" + (f": {self.detail}" if self.detail else "")


def browser_executable() -> Path:
    """Resolve the installed Camoufox executable without any download or launch.

    ``camoufox_path(download_if_missing=False)`` raises instead of fetching, and
    passing its result to ``launch_path`` keeps that call from fetching too.
    """
    from camoufox.pkgman import camoufox_path, launch_path

    path = Path(launch_path(browser_path=camoufox_path(download_if_missing=False)))
    if not (path.is_file() and os.access(path, os.X_OK)):
        raise FileNotFoundError("camoufox executable is missing or not executable")
    return path


def running_process_names(proc_root: Path = Path("/proc")) -> set[str]:
    names: set[str] = set()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return names
    for entry in entries:
        if entry.name.isdigit():
            try:
                names.add((entry / "comm").read_text().strip())
            except OSError:
                continue
    return names


def dir_writable(path: Path) -> bool:
    """Permission test only -- creates nothing."""
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


def classify_http_status(status: int) -> str:
    """Any HTTP response proves reachability. A 403 to a plain HTTP client is
    expected (bot protection) and is not an authentication verdict."""
    if 200 <= status < 400:
        return "reachable"
    if status in (401, 403):
        return f"reachable (plain HTTP client gets HTTP {status}; not an auth verdict)"
    return f"reachable (HTTP {status})"


def probe_http(
    url: str,
    timeout_s: float,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[int | None, float, str]:
    """(status or None, elapsed_ms, error type or ""). Never raises."""
    started = time.perf_counter()
    status: int | None = None
    error = ""
    try:
        result = runner(
            [
                "curl",
                "--location",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--connect-timeout",
                str(timeout_s),
                "--max-time",
                str(timeout_s),
                "--user-agent",
                "rma-poc-doctor",
                "--write-out",
                "%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s + 1,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            status = int(result.stdout.strip())
        else:
            error = f"curl_exit_{result.returncode}"
    except Exception as exc:  # noqa: BLE001 - DNS, refused, timeout, TLS ... by type only
        error = type(exc).__name__
    return status, (time.perf_counter() - started) * 1000, error


def _versions_check() -> Check:
    try:
        package = metadata.version("camoufox")
    except metadata.PackageNotFoundError:
        return Check("camoufox package", False, "not installed")
    try:
        from camoufox.pkgman import installed_verstr

        browser = installed_verstr()
    except Exception as exc:  # noqa: BLE001
        return Check(
            "camoufox package/browser", False, f"package={package} browser={type(exc).__name__}"
        )
    return Check("camoufox package/browser", True, f"package={package} browser={browser}")


def collect_checks(
    *,
    base_url: str,
    profile_dir: Path,
    state_path: Path,
    timeout_s: float = 10.0,
) -> list[Check]:
    checks: list[Check] = [_versions_check()]

    try:
        browser_executable()
        checks.append(Check("camoufox executable", True, "resolves and is executable"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("camoufox executable", False, type(exc).__name__))

    running = running_process_names()
    for process in REQUIRED_PROCESSES:
        checks.append(Check(f"process {process}", process in running))

    status, elapsed_ms, error = probe_http(NOVNC_URL, 3.0)
    checks.append(
        Check(
            "noVNC endpoint",
            status == 200,
            f"http={status if status is not None else error} elapsed_ms={elapsed_ms:.0f}",
        )
    )

    checks.append(Check("display socket", Path("/tmp/.X11-unix/X99").exists()))
    for label, directory in (("profile dir", profile_dir.parent), ("state dir", state_path.parent)):
        checks.append(Check(f"{label} writable", dir_writable(directory), str(directory)))

    profile_present = profile_dir.is_dir() and any(profile_dir.iterdir())
    checks.append(
        Check("browser profile", profile_present, f"present={'yes' if profile_present else 'no'}", False)
    )
    state_present = state_path.is_file()
    checks.append(
        Check("saved session state", state_present, f"present={'yes' if state_present else 'no'}", False)
    )

    host = urlsplit(base_url).netloc
    status, elapsed_ms, error = probe_http(base_url, timeout_s)
    checks.append(
        Check(
            f"OmegaFlow connectivity ({host})",
            status is not None,
            (classify_http_status(status) if status is not None else f"unreachable: {error}")
            + f" elapsed_ms={elapsed_ms:.0f}",
        )
    )
    return checks


def ready(checks: list[Check]) -> bool:
    return all(check.ok for check in checks if check.required)
