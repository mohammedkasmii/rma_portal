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
    "workflows",
    "workflow_memberships",
}


def _run_upgrade(data_dir: Path, revision: str = "head") -> None:
    env = {
        **__import__("os").environ,
        "RMA_PORTAL_DATA_DIR": str(data_dir),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
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

        workflows = con.execute(
            "select key, view_id, enabled, rules_status from workflows"
        ).fetchall()
        assert workflows == [("agreement_garage", "view_1874", 1, "UNCONFIRMED")]
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
        workflow_count = con.execute("select count(*) from workflows").fetchone()[0]
        assert workflow_count == 1
    finally:
        con.close()


def test_upgrade_backfills_existing_dossier_into_agreement_garage(tmp_path: Path):
    data_dir = tmp_path / "backfill-db"
    data_dir.mkdir()
    _run_upgrade(data_dir, "c22e2bb290df")  # pre-workflow schema

    db_path = data_dir / "rma_portal.sqlite3"
    con = sqlite3.connect(db_path)
    try:
        account_id = con.execute("select id from portal_accounts").fetchone()[0]
        seen = "2026-01-02 03:04:05.000000"
        text_cols = [
            "dossier_number", "insured_name", "procedure", "registration", "garage",
            "estimate_amount_raw", "portal_status", "city", "observation_count",
            "agreement_login", "details_href", "date_creation_raw",
            "date_premiere_fin_prevue_raw", "date_fin_travaux_prevue_raw",
            "date_envoi_devis_garage_raw", "date_photos_avant_raw",
        ]
        columns = ["portal_account_id", "record_id", *text_cols]
        columns += ["detail_complete", "first_seen_at", "last_seen_at", "active"]
        columns += ["missing_complete_polls"]
        values = [account_id, "rec-1", *["x"] * len(text_cols), 0, seen, seen, 1, 1]
        con.execute(
            f"insert into dossiers ({', '.join(columns)}) values ({', '.join('?' * len(values))})",
            values,
        )
        con.commit()
    finally:
        con.close()

    _run_upgrade(data_dir)

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "select w.key, m.active, m.missing_complete_polls, m.occurrence_number, "
            "m.first_seen_at, m.last_seen_at "
            "from workflow_memberships m "
            "join workflows w on w.id = m.workflow_id "
            "join dossiers d on d.id = m.dossier_id where d.record_id = 'rec-1'"
        ).fetchall()
        assert rows == [("agreement_garage", 1, 1, 1, seen, seen)]
    finally:
        con.close()
