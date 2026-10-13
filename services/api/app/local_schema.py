"""Adopt or migrate SQLite safely, without recreating an existing workspace.

Called from `db.init_db()`. Pre-Alembic databases are compared against the models and
stamped at head only if identical; otherwise startup fails rather than guessing.
Backups go to `.runtime/backups/` before any adoption or upgrade.
"""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from .models import Base

ROOT = Path(__file__).resolve().parents[3]


def backup_sqlite(engine, directory=None):
    """Online-copy the SQLite file to a timestamped 0600 backup; returns its path, or None for missing/in-memory DBs."""
    filename = engine.url.database
    if not filename or filename == ":memory:" or not Path(filename).exists():
        return None
    directory = Path(directory) if directory else ROOT / ".runtime" / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = directory / f"frontdesk-{stamp}.db"
    with sqlite3.connect(filename) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    target.chmod(0o600)
    return target


def migrate_sqlite(engine):
    """Upgrade the SQLite schema to Alembic head, adopting an unversioned legacy schema when it matches the models.

    Raises RuntimeError on a schema mismatch (before or after migrating) and ValueError for non-SQLite engines.
    """
    if engine.dialect.name != "sqlite":
        raise ValueError("Local schema adoption only applies to SQLite")
    config = Config(str(ROOT / "services/api/alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        current = MigrationContext.configure(connection).get_current_revision()
        adopt = bool(tables - {"alembic_version"}) and current is None
        if adopt and compare_metadata(MigrationContext.configure(connection), Base.metadata):
            raise RuntimeError("Existing SQLite schema does not match the application. No migration marker was written; review it before migration.")
    # Back up before any change (adoption or pending upgrade).
    if current not in heads or adopt:
        backup_sqlite(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if adopt:
            # create_all previously initialized this exact schema. Stamp only
            # after comparison, never blindly run CREATE TABLE over user records.
            command.stamp(config, "head")
        command.upgrade(config, "head")
        if compare_metadata(MigrationContext.configure(connection), Base.metadata):
            raise RuntimeError("SQLite migration completed with an unexpected schema difference")
