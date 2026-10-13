"""Back up/check local SQLite; optionally refresh authorized sample observations."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "services/api"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-sample-state", action="store_true", help="Refresh sample observation timestamps; preserve reservations, occupancy, closures and pause switches")
    args = parser.parse_args()
    from app.db import engine, init_db, transaction
    from app.local_schema import backup_sqlite
    from app.rehearsal import refresh_sample_observations
    from sqlalchemy import text
    from alembic.migration import MigrationContext
    if engine.dialect.name != "sqlite":
        raise SystemExit("Rehearsal preparation is restricted to local SQLite.")
    backup = backup_sqlite(engine)
    if backup:
        print("Backup:", backup.relative_to(ROOT))
    init_db()
    with engine.connect() as db:
        assert db.execute(text("PRAGMA integrity_check")).scalar() == "ok"
        assert not db.execute(text("PRAGMA foreign_key_check")).all()
        assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        print("SQLite: integrity, foreign keys and WAL: PASS")
        print("Migration:", MigrationContext.configure(db).get_current_revision())
        for table in ["reservations", "resources", "orders", "customers", "calls"]:
            print(f"Persisted {table}:", db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar())
    if args.refresh_sample_state:
        with transaction() as db:
            result = refresh_sample_observations(db)
        print("Sample observations refreshed:", result)
        print("Existing bookings, orders, customers, calls, occupied tables, closures and pauses retained.")


if __name__ == "__main__":
    main()
