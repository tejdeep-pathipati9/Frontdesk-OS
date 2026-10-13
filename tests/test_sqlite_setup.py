from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from pathlib import Path
from sqlalchemy import inspect, text
from app.db import make_engine
from app.models import Base
from app.local_schema import migrate_sqlite, ROOT
import pytest


def test_fresh_sqlite_is_migrated_and_enforces_database_settings(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'new.db'}")
    migrate_sqlite(engine)
    with engine.connect() as db:
        assert MigrationContext.configure(db).get_current_revision() == "a81c30d6008d"
        assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert db.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    engine.dispose()


def test_existing_unversioned_workspace_is_adopted_without_losing_records(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(text("INSERT INTO tenants (id, name, created_at) VALUES ('preserved', 'Existing business', CURRENT_TIMESTAMP)"))
    migrate_sqlite(engine)
    migrate_sqlite(engine)
    engine.dispose()
    reopened = make_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    with reopened.connect() as db:
        assert db.execute(text("SELECT name FROM tenants WHERE id='preserved'")).scalar() == "Existing business"
        assert MigrationContext.configure(db).get_current_revision() == "a81c30d6008d"
    reopened.dispose()


def test_partial_unknown_schema_is_never_blindly_stamped(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'unknown.db'}")
    with engine.begin() as db:
        db.execute(text("CREATE TABLE custom_data (id INTEGER PRIMARY KEY, value TEXT)"))
        db.execute(text("INSERT INTO custom_data VALUES (1,'keep this')"))
    with pytest.raises(RuntimeError, match="does not match"):
        migrate_sqlite(engine)
    with engine.connect() as db:
        assert db.execute(text("SELECT value FROM custom_data")).scalar() == "keep this"
        assert "alembic_version" not in inspect(db).get_table_names()
    engine.dispose()


def test_previous_migration_upgrades_additively(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    config = Config(str(ROOT / 'services/api/alembic.ini'))
    with engine.begin() as db:
        config.attributes['connection'] = db
        command.upgrade(config, 'a81c30d6008d')
        db.execute(text("INSERT INTO tenants (id, name, created_at) VALUES ('preserved', 'Previous schema', CURRENT_TIMESTAMP)"))
    migrate_sqlite(engine)
    with engine.connect() as db:
        assert db.execute(text("SELECT name FROM tenants WHERE id='preserved'")).scalar() == 'Previous schema'
    engine.dispose()


def test_relative_sqlite_path_is_stable_outside_repository(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    engine = make_engine('sqlite:///./frontdesk.db')
    assert Path(engine.url.database) == ROOT / 'frontdesk.db'
    engine.dispose()
