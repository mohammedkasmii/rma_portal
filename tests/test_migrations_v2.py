"""V2 platform migration: schema, Garage agréé backfill and immutability."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tests.test_migrations import _run_upgrade

V2_TABLES = {
    "workflow_occurrences",
    "workflow_work",
    "sync_runs",
    "workflow_poll_runs",
    "workflow_events",
    "outbox_messages",
    "ai_runs",
}
PRE_V2_REVISION = "8f3b2a1d7c40"
SEEN = "2026-01-02 03:04:05.000000"


def seed_legacy_installation(db_path: Path) -> dict[str, int]:
    """A pre-V2 Garage agréé database: three dossiers, alerts, reads, work, notes."""
    con = sqlite3.connect(db_path)
    try:
        account_id = con.execute("select id from portal_accounts").fetchone()[0]
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

        def add_dossier(record_id: str, first_seen: str, active: int = 1) -> int:
            values = [account_id, record_id, *["x"] * len(text_cols), 0, first_seen, first_seen]
            values += [active, 0]
            cur = con.execute(
                f"insert into dossiers ({', '.join(columns)}) "
                f"values ({', '.join('?' * len(values))})",
                values,
            )
            return cur.lastrowid

        for username in ("alice", "bob"):
            con.execute(
                "insert into users (username, display_name, password_hash, role, active, created_at)"
                " values (?, ?, 'hash', 'EMPLOYEE', 1, ?)",
                (username, username.title(), SEEN),
            )
        alice = con.execute("select id from users where username='alice'").fetchone()[0]
        bob = con.execute("select id from users where username='bob'").fetchone()[0]

        # d1: baseline dossier that later left and came back once (one legacy alert)
        d1 = add_dossier("rec-baseline", "2026-01-02 03:04:05.000000")
        # d2: appeared after baseline (alert at first sight)
        d2 = add_dossier("rec-new", "2026-02-01 10:00:00.000000")
        # d3: never alerted, inactive
        d3 = add_dossier("rec-old", "2026-01-02 03:04:05.000000", active=0)

        def add_notification(dossier_id: int, detected: str) -> int:
            cur = con.execute(
                "insert into notifications (dossier_id, kind, detected_at) "
                "values (?, 'NEW_AGREEMENT_DOSSIER', ?)",
                (dossier_id, detected),
            )
            return cur.lastrowid

        n_return = add_notification(d1, "2026-03-01 09:00:00.000000")
        n_new = add_notification(d2, "2026-02-01 10:00:20.000000")
        con.execute(
            "insert into notification_reads (notification_id, user_id, seen_at) values (?, ?, ?)",
            (n_new, alice, "2026-02-01 11:00:00.000000"),
        )
        con.execute(
            "insert into dossier_work (dossier_id, status, version, updated_by, updated_at) "
            "values (?, 'IN_PROGRESS', 4, ?, ?)",
            (d2, alice, "2026-02-02 08:00:00.000000"),
        )
        con.execute(
            "insert into dossier_notes (dossier_id, author_id, body, created_at) values (?, ?, ?, ?)",
            (d2, bob, "Appeler le garage", "2026-02-02 09:00:00.000000"),
        )
        con.execute(
            "insert into poll_runs (portal_account_id, started_at, completed_at, status, rows_seen, "
            "pages_seen, details_failed) values (?, ?, ?, 'COMPLETE', 12, 2, 0)",
            (account_id, "2026-03-01 09:00:00.000000", "2026-03-01 09:01:00.000000"),
        )
        con.commit()
        return {
            "d1": d1, "d2": d2, "d3": d3, "alice": alice, "bob": bob,
            "n_new": n_new, "n_return": n_return,
        }
    finally:
        con.close()


def _legacy_db(tmp_path: Path, name: str) -> tuple[Path, Path, dict[str, int]]:
    data_dir = tmp_path / name
    data_dir.mkdir()
    _run_upgrade(data_dir, PRE_V2_REVISION)
    db_path = data_dir / "rma_portal.sqlite3"
    return data_dir, db_path, seed_legacy_installation(db_path)


def test_v2_upgrade_backfills_garage_history_without_data_loss(tmp_path: Path):
    data_dir, db_path, ids = _legacy_db(tmp_path, "v2-backfill")

    _run_upgrade(data_dir)

    con = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        assert tables >= V2_TABLES

        # Garage agréé keeps its validated rules and gains its captured filter.
        workflow = con.execute(
            "select rules_status, notification_class, filter_field, filter_value, "
            "primary_date_key, last_poll_status from workflows where key='agreement_garage'"
        ).fetchone()
        assert workflow == (
            "CONFIRMED", "ACTION", "field_219", "5ed644a2faf17c0015d8c367",
            "date_envoi_devis_garage", "COMPLETE",
        )

        # One occurrence per legacy alert, plus a baseline occurrence when needed.
        occurrences = {
            (dossier_id, number): (origin, detected)
            for dossier_id, number, origin, detected in con.execute(
                "select dossier_id, occurrence_number, origin, detected_at from workflow_occurrences"
            )
        }
        assert occurrences[(ids["d1"], 1)][0] == "BASELINE"
        assert occurrences[(ids["d1"], 2)] == ("RETURNED", "2026-03-01 09:00:00.000000")
        assert occurrences[(ids["d2"], 1)] == ("NEW", "2026-02-01 10:00:20.000000")
        assert occurrences[(ids["d3"], 1)][0] == "BASELINE"
        assert len(occurrences) == 4
        counts = dict(
            con.execute("select dossier_id, occurrence_number from workflow_memberships")
        )
        assert counts == {ids["d1"]: 2, ids["d2"]: 1, ids["d3"]: 1}

        # Alerts point to their occurrence and workflow; per-employee reads survive.
        notes = {
            row[0]: row[1:]
            for row in con.execute(
                "select n.id, n.kind, n.workflow_id, o.dossier_id, o.occurrence_number "
                "from notifications n join workflow_occurrences o on o.id = n.workflow_occurrence_id"
            )
        }
        assert notes[ids["n_new"]][0] == "WORKFLOW_ITEM_NEW"
        assert notes[ids["n_new"]][2:] == (ids["d2"], 1)
        assert notes[ids["n_return"]][0] == "WORKFLOW_ITEM_RETURNED"
        assert notes[ids["n_return"]][2:] == (ids["d1"], 2)
        reads = con.execute("select notification_id, user_id from notification_reads").fetchall()
        assert reads == [(ids["n_new"], ids["alice"])]

        # Work status moved to the membership; dossiers without a row start TO_DO.
        work = {
            dossier_id: (status, version, updated_by)
            for dossier_id, status, version, updated_by in con.execute(
                "select m.dossier_id, w.status, w.version, w.updated_by from workflow_work w "
                "join workflow_memberships m on m.id = w.membership_id"
            )
        }
        assert work[ids["d2"]] == ("IN_PROGRESS", 4, ids["alice"])
        assert work[ids["d1"]][:2] == ("TO_DO", 1)
        assert len(work) == 3
        # Legacy tables are untouched, so an application rollback stays possible.
        assert con.execute("select status, version from dossier_work").fetchall() == [
            ("IN_PROGRESS", 4)
        ]

        # Notes stay dossier-level (no invented workflow context).
        assert con.execute(
            "select body, workflow_membership_id from dossier_notes"
        ).fetchall() == [("Appeler le garage", None)]

        # Poll history is mirrored under parent sync runs.
        assert con.execute(
            "select s.status, s.workflows_total, s.workflows_complete, p.rows_seen "
            "from sync_runs s join workflow_poll_runs p on p.sync_run_id = s.id"
        ).fetchall() == [("COMPLETE", 1, 1, 12)]
        assert con.execute("select count(*) from poll_runs").fetchone()[0] == 1
    finally:
        con.close()


def test_v2_occurrences_and_events_are_immutable(tmp_path: Path):
    data_dir, db_path, _ = _legacy_db(tmp_path, "v2-immutable")
    _run_upgrade(data_dir)

    con = sqlite3.connect(db_path)
    try:
        try:
            con.execute("update workflow_occurrences set origin = 'NEW'")
        except sqlite3.DatabaseError as exc:
            assert "immutable" in str(exc)
        else:
            raise AssertionError("workflow_occurrences must reject UPDATE")
        workflow_id = con.execute("select id from workflows limit 1").fetchone()[0]
        con.execute(
            "insert into workflow_events (workflow_id, kind, notification_class, detected_at, "
            "changed_fields_json, before_fingerprints_json, after_fingerprints_json) "
            "values (?, 'WORKFLOW_ITEM_NEW', 'ACTION', ?, '[]', '{}', '{}')",
            (workflow_id, SEEN),
        )
        try:
            con.execute("update workflow_events set message = 'rewritten'")
        except sqlite3.DatabaseError as exc:
            assert "immutable" in str(exc)
        else:
            raise AssertionError("workflow_events must reject UPDATE")
    finally:
        con.close()


def test_v2_upgrade_keeps_note_length_check_constraint(tmp_path: Path):
    data_dir = tmp_path / "v2-check"
    data_dir.mkdir()
    _run_upgrade(data_dir)
    con = sqlite3.connect(data_dir / "rma_portal.sqlite3")
    try:
        sql = con.execute("select sql from sqlite_master where name = 'dossier_notes'").fetchone()[0]
        assert "ck_note_max_length" in sql
    finally:
        con.close()


def test_v2_downgrade_restores_the_pre_v2_schema(tmp_path: Path):
    import os
    import subprocess
    import sys

    from tests.test_migrations import REPO_ROOT

    data_dir, db_path, ids = _legacy_db(tmp_path, "v2-downgrade")
    _run_upgrade(data_dir)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", PRE_V2_REVISION],
        cwd=REPO_ROOT,
        env={**os.environ, "RMA_PORTAL_DATA_DIR": str(data_dir)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    con = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        assert not tables & V2_TABLES
        assert con.execute("select count(*) from dossiers").fetchone()[0] == 3
        assert con.execute("select count(*) from notification_reads").fetchone()[0] == 1
    finally:
        con.close()
