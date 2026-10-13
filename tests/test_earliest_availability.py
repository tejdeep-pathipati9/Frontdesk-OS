from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from app.db import transaction
from app.models import Location, Resource, Reservation, Allocation, now
from app.seed import RESTAURANT, DEMO_TENANT
from app.schemas import Availability
from app.domain import availability, local_slot_labels, DomainError, utc
from sqlalchemy import select


def evening():
    return (now().astimezone(ZoneInfo("America/New_York")) + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)


def test_utc_result_is_spoken_in_business_time_and_earliest_matches_exact(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        start = evening()
        earliest = availability(db, loc, Availability(location_id=loc.id, party_size=2, start_at=start, end_at=start + timedelta(hours=7), search="earliest"))
        at_seven = availability(db, loc, Availability(location_id=loc.id, party_size=2, start_at=start + timedelta(hours=2)))
        assert earliest["status"] == at_seven["status"] == "AVAILABLE"
        assert utc(earliest["start_at"]) == utc(start)
        assert "5 PM" in earliest["start_at_label"] and "7 PM" in at_seven["start_at_label"]
        assert datetime.fromisoformat(earliest["local_start_at"]).hour == 17
        assert earliest["timezone"] == "America/New_York"
        assert earliest["slots_checked"] == 1


def test_earliest_search_checks_overlaps_before_offering_a_later_slot(database):
    with transaction(database) as db:
        loc, start = db.get(Location, RESTAURANT), evening()
        for table in db.scalars(select(Resource).where(Resource.location_id == loc.id)):
            table.state = "available" if table.id == "table-1" else "out_of_service"
        booking = Reservation(tenant_id=DEMO_TENANT, location_id=loc.id, customer_id="seed-customer", start_at=utc(start), end_at=utc(start + timedelta(minutes=85)), party_size=2, kind="reservation", idempotency_key="occupied-earliest")
        db.add(booking); db.flush()
        db.add(Allocation(tenant_id=DEMO_TENANT, reservation_id=booking.id, resource_id="table-1", start_at=utc(start), end_at=booking.end_at)); db.flush()
        result = availability(db, loc, Availability(location_id=loc.id, party_size=2, start_at=start, end_at=start + timedelta(hours=4), search="earliest"))
        assert result["status"] == "AVAILABLE" and utc(result["start_at"]) == utc(start + timedelta(minutes=90))
        assert result["slots_checked"] == 7


def test_unknown_floor_cannot_claim_a_known_earliest_time(database):
    with transaction(database) as db:
        loc, start = db.get(Location, RESTAURANT), evening()
        for table in db.scalars(select(Resource).where(Resource.location_id == loc.id)):
            table.verified_at = now() - timedelta(hours=2)
        result = availability(db, loc, Availability(location_id=loc.id, start_at=start, end_at=start + timedelta(hours=4), search="earliest"))
        assert result["status"] == "UNKNOWN" and "start_at" not in result
        assert result["slots_checked"] == 1


def test_slot_duration_must_fit_inside_search_window(database):
    with transaction(database) as db:
        loc, start = db.get(Location, RESTAURANT), evening()
        result = availability(db, loc, Availability(location_id=loc.id, start_at=start, end_at=start + timedelta(minutes=30), search="earliest"))
        assert result["status"] == "NO_CAPACITY"


@pytest.mark.parametrize("offset", [None, 0, 25])
def test_search_requires_a_bounded_time_window(database, offset):
    with transaction(database) as db:
        loc, start = db.get(Location, RESTAURANT), evening()
        request = Availability(location_id=loc.id, start_at=start, search="earliest", end_at=start + timedelta(hours=offset) if offset is not None else None)
        with pytest.raises(DomainError): availability(db, loc, request)


def test_dst_labels_distinguish_the_repeated_local_hour():
    class Location:
        timezone = "America/New_York"
    early = local_slot_labels(Location(), {"start_at": "2026-11-01T05:30:00+00:00"})
    late = local_slot_labels(Location(), {"start_at": "2026-11-01T06:30:00+00:00"})
    assert "EDT" in early["start_at_label"] and "EST" in late["start_at_label"]
    assert early["local_start_at"] != late["local_start_at"]


def test_shared_voice_tool_returns_local_times_and_backend_earliest_slot(client):
    call = client.post("/api/v1/voice/test-calls", json={"location_id": RESTAURANT}).json()
    start = evening()
    result = client.post(f"/api/v1/voice/test-calls/{call['id']}/tools", json={"request_id": "earliest-slot", "name": "find_earliest_availability", "arguments": {"party_size": 2, "start_at": start.isoformat(), "end_at": (start + timedelta(hours=7)).isoformat()}}).json()
    assert result["status"] == "AVAILABLE" and result["search"] == "earliest"
    assert datetime.fromisoformat(result["start_at"]).hour == 17
    assert "5 PM" in result["start_at_label"]
    assert result["timezone"] == "America/New_York"


@pytest.mark.parametrize("scenario,status", [("normal", "AVAILABLE"), ("stale", "UNKNOWN"), ("failure", "UNKNOWN"), ("unavailable", "NO_CAPACITY")])
def test_earliest_search_uses_selected_booking_provider(client, scenario, status):
    response = client.post("/api/v1/locations", json={"name": "Earliest provider", "vertical": "restaurant", "existing_software": "mock_opentable"})
    assert response.status_code == 200
    loc = response.json()["id"]
    client.post("/api/v1/integrations/mock_opentable/connect", json={"location_id": loc, "scenario": scenario, "booking_authority": "mock_opentable"}).raise_for_status()
    start = evening()
    result = client.post("/api/v1/availability/restaurant", json={"location_id": loc, "party_size": 12, "start_at": start.isoformat(), "end_at": (start + timedelta(hours=4)).isoformat(), "search": "earliest"}).json()
    assert result["status"] == status
    assert result.get("evidence", {}).get("source_provider") == "mock_opentable"
