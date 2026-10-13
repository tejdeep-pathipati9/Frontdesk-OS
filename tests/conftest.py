import sys
from pathlib import Path
import pytest
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))
from app import main, auth, db as storage
from app.models import Base
from app.seed import seed
from app.config import settings


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "ollama_confirmation_enabled", False)
    from app.confirmation_intent import clear
    clear()
    engine = storage.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with storage.transaction(factory) as session:
        seed(session)
    yield factory
    engine.dispose()
    clear()


@pytest.fixture
def client(database, monkeypatch):
    monkeypatch.setattr(main, "Session", database)
    monkeypatch.setattr(auth, "Session", database)
    monkeypatch.setattr(main, "transaction", lambda: storage.transaction(database))
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(settings(), "demo_mode", True)
    monkeypatch.setattr(settings(), "allow_demo_login", True)
    main.request_windows.clear()
    with TestClient(main.app) as test:
        result = test.post("/api/v1/auth/login", json={"email": "", "demo": True})
        assert result.status_code == 200
        yield test
