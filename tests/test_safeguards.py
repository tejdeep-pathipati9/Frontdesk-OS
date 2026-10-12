from datetime import timedelta
from sqlalchemy import select
from app.db import transaction
from app.models import Resource, Location, now
from app.domain import availability, seating_wait
from app.schemas import Availability
from app.seed import RESTAURANT


def test_overdue_seated_table_is_never_assumed_free(database):
    with transaction(database) as db:
        for table in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            table.state = "out_of_service"
        table = db.get(Resource, "table-8")
        table.state = "seated"
        table.seated_at = now() - timedelta(hours=3)
        table.verified_at = now()
        loc = db.get(Location, RESTAURANT)
        result = availability(db, loc, Availability(location_id=RESTAURANT, start_at=now() + timedelta(seconds=1), party_size=8))
        assert result["status"] == "UNKNOWN"
        assert seating_wait(db, loc, 8)["status"] == "UNKNOWN"


def test_cleaning_requires_completion_before_available_now(database):
    with transaction(database) as db:
        for table in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            table.state = "out_of_service"
        table = db.get(Resource, "table-8")
        table.state, table.verified_at = "cleaning", now()
        result = availability(db, db.get(Location, RESTAURANT), Availability(location_id=RESTAURANT, start_at=now() + timedelta(seconds=1), party_size=8))
        assert result["status"] == "NO_CAPACITY"


def test_proxy_origin_is_allowed_but_foreign_origin_is_denied(client):
    allowed = client.post("/api/v1/auth/login", json={"email": "", "demo": True}, headers={"Origin": "http://127.0.0.1:3000"})
    assert allowed.status_code == 200
    blocked = client.post("/api/v1/auth/login", json={"email": "", "demo": True}, headers={"Origin": "https://not-frontdesk.example"})
    assert blocked.status_code == 403
