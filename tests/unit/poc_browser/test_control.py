from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "docker" / "poc-browser"))

from rma_poc import control  # noqa: E402
from rma_poc.common import ExitCode, PocConfig  # noqa: E402

TOKEN = "a" * 32


def config(tmp_path: Path) -> PocConfig:
    return PocConfig(
        base_url="https://omegaflow.example",
        start_route="https://omegaflow.example/#login",
        locale="fr-FR",
        timezone_id="Africa/Casablanca",
        camoufox_os="windows",
        window=(1400, 820),
        profile_dir=tmp_path / "profile",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
        log_path=tmp_path / "control.log",
    )


def test_control_requires_its_private_token(tmp_path):
    with TestClient(control.create_app(token=TOKEN, cfg=config(tmp_path))) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/status").status_code == 401
        assert client.post("/login", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_control_allows_only_one_login_task(monkeypatch, tmp_path):
    async def login(_cfg, *, timeout_s):
        assert timeout_s == 900
        await __import__("asyncio").Event().wait()
        return ExitCode.OK

    monkeypatch.setattr(control, "run_login", login)
    with TestClient(control.create_app(token=TOKEN, cfg=config(tmp_path))) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = client.post("/login", headers=headers)
        second = client.post("/login", headers=headers)
        current = client.get("/status", headers=headers)

        assert first.status_code == 202 and first.json() == {
            "state": "CONNECTING",
            "started": True,
            "result": None,
        }
        assert second.status_code == 202 and second.json()["started"] is False
        assert current.json()["state"] == "CONNECTING"
