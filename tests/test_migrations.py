"""Alembic migration verification (spec section 12: 'from a completely
empty database' + 'upgrade head run twice without failure').
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

EXPECTED_TABLES = {
    "alembic_version",
    "users",
    "portal_accounts",
    "dossiers",
    "notifications",
    "notification_reads",
    "dossier_work",
    "dossier_notes",
    "poll_runs",
}


def _run_upgrade(data_dir: Path) -> None:
    env = {
        **__import__("os").environ,
        "RMA_PORTAL_DATA_DIR": str(data_dir),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_upgrade_from_empty_database_creates_expected_schema(tmp_path: Path):
    data_dir = tmp_path / "empty-db"
    data_dir.mkdir()
    _run_upgrade(data_dir)

    db_path = data_dir / "rma_portal.sqlite3"
    assert db_path.exists()
    con = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        assert tables >= EXPECTED_TABLES

        accounts = con.execute("select name, base_url, enabled from portal_accounts").fetchall()
        assert accounts == [("OmegaFlow", "https://omegaflow.ma", 1)]
    finally:
        con.close()


def test_upgrade_head_twice_is_a_no_op(tmp_path: Path):
    data_dir = tmp_path / "idempotent-db"
    data_dir.mkdir()
    _run_upgrade(data_dir)
    _run_upgrade(data_dir)  # must not fail or duplicate the seed row

    db_path = data_dir / "rma_portal.sqlite3"
    con = sqlite3.connect(db_path)
    try:
        count = con.execute("select count(*) from portal_accounts").fetchone()[0]
        assert count == 1
    finally:
        con.close()
