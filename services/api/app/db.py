from contextlib import contextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from pathlib import Path
from sqlalchemy.orm import sessionmaker
from .config import settings
from .models import Base


def make_engine(url):
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite") and parsed.database and parsed.database != ":memory:" and not Path(parsed.database).is_absolute():
        parsed = parsed.set(database=str((Path(__file__).resolve().parents[3] / parsed.database).resolve()))
        url = parsed.render_as_string(hide_password=False)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}, pool_pre_ping=True)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine


engine = make_engine(settings().database_url)
Session = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction(factory=Session):
    """Serialize capacity mutations: SQLite writer lock or PostgreSQL transaction lock.

    The prototype locks all business writes. This is intentionally conservative;
    a deployment can replace the global advisory lock with ordered location locks.
    No network provider calls are performed inside this transaction.
    """
    with factory() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        else:
            db.execute(text("SELECT pg_advisory_xact_lock(734291)"))
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def init_db():
    if engine.dialect.name == "sqlite":
        from .local_schema import migrate_sqlite
        migrate_sqlite(engine)
    else:
        Base.metadata.create_all(engine)
