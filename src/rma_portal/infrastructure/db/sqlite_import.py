"""Idempotent import of the legacy SQLite installation into the V2 database.

The Windows installation keeps everything in one SQLite file. Cutover to the
Ubuntu/PostgreSQL deployment must not lose a dossier, note, read state or
work status, and it must be safe to run more than once (a failed cutover
attempt, then a retry).

How it stays safe:

* the source file is never modified -- it is copied to a scratch file and
  upgraded to the current schema there (the V2 migration backfills
  occurrences and workflow work), then read from the copy;
* rows keep their primary keys, so every foreign key stays valid;
* tables are inserted in dependency order inside one target transaction:
  either everything is imported or nothing is;
* ``INSERT`` skips primary keys that already exist, so re-running adds only
  what is missing. ``portal_accounts`` and ``workflows`` are the exception:
  the target migration seeds them, so they are updated in place, but only
  while the target's own poll state is not newer than the source's;
* PostgreSQL sequences are moved past the imported ids.

Browser profile and captured session state are *not* database content and are
copied separately (see docs/ubuntu-production-runbook.md).
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from sqlalchemy import Engine, Table, create_engine, func, insert, select, text, update

from alembic import command
from rma_portal.infrastructure.db.models import Base

# Dependency order: parents before children.
IMPORT_ORDER: tuple[str, ...] = (
    "portal_accounts",
    "users",
    "workflows",
    "dossiers",
    "workflow_memberships",
    "workflow_occurrences",
    "workflow_work",
    "sync_runs",
    "workflow_poll_runs",
    "workflow_events",
    "notifications",
    "notification_reads",
    "dossier_work",
    "dossier_notes",
    "poll_runs",
    "outbox_messages",
    "ai_runs",
)

_UPSERT_TABLES = frozenset({"portal_accounts", "workflows"})
_COMPOSITE_KEYS: dict[str, tuple[str, ...]] = {
    "notification_reads": ("notification_id", "user_id"),
    "workflow_work": ("membership_id",),
    "dossier_work": ("dossier_id",),
}
_BATCH_SIZE = 500
_REPO_ROOT = Path(__file__).resolve().parents[4]


class SqliteImportError(RuntimeError):
    """The legacy database cannot be imported."""


@dataclass(slots=True)
class ImportReport:
    inserted: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)

    def total_inserted(self) -> int:
        return sum(self.inserted.values())


def _alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def upgrade_to_head(url: str) -> None:
    command.upgrade(_alembic_config(url), "head")


def _aware(value: object) -> object:
    """SQLite returns naive datetimes for timestamptz columns; they are UTC."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _normalise(row: dict[str, object]) -> dict[str, object]:
    return {key: _aware(value) for key, value in row.items()}


def _primary_key_columns(table: Table) -> tuple[str, ...]:
    return _COMPOSITE_KEYS.get(table.name) or tuple(column.name for column in table.primary_key)


def _existing_keys(conn, table: Table, key_columns: tuple[str, ...]) -> set[tuple]:
    columns = [table.c[name] for name in key_columns]
    return {tuple(row) for row in conn.execute(select(*columns))}


def _newer_or_equal(target_value: object, source_value: object) -> bool:
    """True when the target already holds state at least as new as the source."""
    if target_value is None:
        return False
    if source_value is None:
        return True
    return _aware(target_value) > _aware(source_value)  # type: ignore[operator]


def _upsert_state_table(conn, table: Table, rows: list[dict[str, object]], report: ImportReport) -> None:
    key_columns = _primary_key_columns(table)
    existing = {
        tuple(row[name] for name in key_columns): row
        for row in (dict(r._mapping) for r in conn.execute(select(table)))
    }
    for row in rows:
        key = tuple(row[name] for name in key_columns)
        current = existing.get(key)
        if current is None:
            conn.execute(insert(table).values(**row))
            report.inserted[table.name] = report.inserted.get(table.name, 0) + 1
            continue
        if "last_poll_at" in table.c and _newer_or_equal(
            current.get("last_poll_at"), row.get("last_poll_at")
        ):
            report.skipped[table.name] = report.skipped.get(table.name, 0) + 1
            continue
        values = {name: value for name, value in row.items() if name not in key_columns}
        conn.execute(update(table).where(*[table.c[n] == row[n] for n in key_columns]).values(**values))
        report.updated[table.name] = report.updated.get(table.name, 0) + 1


def _insert_missing(conn, table: Table, rows: list[dict[str, object]], report: ImportReport) -> None:
    key_columns = _primary_key_columns(table)
    existing = _existing_keys(conn, table, key_columns)
    pending = []
    for row in rows:
        key = tuple(row[name] for name in key_columns)
        if key in existing:
            report.skipped[table.name] = report.skipped.get(table.name, 0) + 1
        else:
            pending.append(row)
            existing.add(key)
    for start in range(0, len(pending), _BATCH_SIZE):
        conn.execute(insert(table), pending[start : start + _BATCH_SIZE])
    if pending:
        report.inserted[table.name] = report.inserted.get(table.name, 0) + len(pending)


def _reset_sequences(conn) -> None:
    if conn.dialect.name != "postgresql":
        return
    for table in Base.metadata.sorted_tables:
        if "id" not in table.c or not table.c.id.primary_key:
            continue
        max_id = conn.execute(select(func.max(table.c.id))).scalar()
        if max_id is None:
            continue
        conn.execute(
            text("SELECT setval(pg_get_serial_sequence(:table, 'id'), :value, true)"),
            {"table": table.name, "value": max_id},
        )


def import_sqlite_database(source_path: Path, target_engine: Engine) -> ImportReport:
    """Copy a legacy SQLite database into ``target_engine`` (already migrated to head)."""
    if not source_path.is_file():
        raise SqliteImportError(f"Base source introuvable : {source_path}")

    report = ImportReport()
    with tempfile.TemporaryDirectory(prefix="rma-import-") as scratch:
        working_copy = Path(scratch) / "source.sqlite3"
        shutil.copy2(source_path, working_copy)
        for suffix in ("-wal", "-shm"):
            sidecar = source_path.with_name(source_path.name + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, working_copy.with_name(working_copy.name + suffix))
        source_url = f"sqlite:///{working_copy.as_posix()}"
        upgrade_to_head(source_url)

        source_engine = create_engine(source_url, future=True)
        try:
            with source_engine.connect() as source, target_engine.begin() as target:
                for name in IMPORT_ORDER:
                    table = Base.metadata.tables[name]
                    rows = [_normalise(dict(r._mapping)) for r in source.execute(select(table))]
                    if not rows:
                        continue
                    if name in _UPSERT_TABLES:
                        _upsert_state_table(target, table, rows, report)
                    else:
                        _insert_missing(target, table, rows, report)
                _reset_sequences(target)
        finally:
            source_engine.dispose()
    return report
