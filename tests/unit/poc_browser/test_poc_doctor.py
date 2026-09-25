"""Pure-logic tests for the Ubuntu PoC doctor and stage timing (no browser)."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "docker" / "poc-browser"))

from rma_poc import doctor  # noqa: E402
from rma_poc.common import timed_stage  # noqa: E402


def test_http_403_counts_as_reachable_not_auth_failure():
    def runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, "403", "")

    status, elapsed_ms, error = doctor.probe_http("https://x", 1.0, runner=runner)
    assert (status, error) == (403, "")
    assert elapsed_ms >= 0
    assert "not an auth verdict" in doctor.classify_http_status(403)


def test_network_error_reports_type_only():
    def runner(*_args, **_kwargs):
        raise TimeoutError("secret detail")

    status, _elapsed, error = doctor.probe_http("https://x", 1.0, runner=runner)
    assert status is None
    assert error == "TimeoutError"


def test_probe_success_status():
    status, _elapsed, error = doctor.probe_http(
        "https://x",
        1.0,
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "200", ""),
    )
    assert (status, error) == (200, "")


def test_probe_failed_curl_reports_exit_code_only():
    status, _elapsed, error = doctor.probe_http(
        "https://x",
        1.0,
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 28, "000", "secret network detail"
        ),
    )
    assert status is None
    assert error == "curl_exit_28"


def test_running_process_names(tmp_path: Path):
    for pid, name in (("101", "Xvfb\n"), ("102", "x11vnc\n")):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "comm").write_text(name)
    (tmp_path / "self").mkdir()
    assert doctor.running_process_names(tmp_path) == {"Xvfb", "x11vnc"}
    assert doctor.running_process_names(tmp_path / "missing") == set()


def test_dir_writable_creates_nothing(tmp_path: Path):
    assert doctor.dir_writable(tmp_path)
    assert not doctor.dir_writable(tmp_path / "absent")
    assert list(tmp_path.iterdir()) == []


def test_ready_ignores_informational_checks():
    ok = doctor.Check("a", True)
    info_fail = doctor.Check("b", False, required=False)
    assert doctor.ready([ok, info_fail])
    assert not doctor.ready([ok, doctor.Check("c", False)])
    assert info_fail.line().startswith("[INFO]")


def test_timed_stage_logs_only_name_outcome_and_ms(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="rma_poc")
    with timed_stage("auth_wait") as stage:
        stage.outcome = "AUTHENTICATED"
    with pytest.raises(ValueError), timed_stage("session_capture"):
        raise ValueError("cookie=abc123")
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0].startswith("stage=auth_wait outcome=AUTHENTICATED elapsed_ms=")
    assert messages[1].startswith("stage=session_capture outcome=FAILED(ValueError) elapsed_ms=")
    assert all("abc123" not in m for m in messages)
