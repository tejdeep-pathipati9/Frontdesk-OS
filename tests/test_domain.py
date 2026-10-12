from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.db import transaction
from app.models import Location, Resource, Reservation, MenuItem, KitchenState, Order, PendingAction, Call, WaitlistEntry, now
from app.seed import RESTAURANT, SALON, DEMO_TENANT
from app.schemas import Availability, PrepareAction, QuoteRequest, CartItem
from app.domain import availability, seating_wait, quote, prepare, confirm_staff, commit, DomainError, observe_transcript, utc


def request(kind="reservation", **kwargs):
    return PrepareAction(location_id=SALON if kind == "appointment" else RESTAURANT, kind=kind, name="Test Guest", start_at=now() + timedelta(hours=5), **kwargs)


def test_split_seats_are_not_capacity(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        loc.settings = {**loc.settings, "combinations": []}
        for row in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            row.state = "available" if row.capacity == 2 else "out_of_service"
        result = availability(db, loc, Availability(location_id=RESTAURANT, start_at=now() + timedelta(hours=1), party_size=4))
        assert result["status"] == "NO_CAPACITY"


def test_explicit_combination_can_fit(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        for row in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            row.state = "available" if row.id in ["table-5", "table-6"] else "out_of_service"
        result = availability(db, loc, Availability(location_id=RESTAURANT, start_at=now() + timedelta(hours=1), party_size=4))
        assert result["resource_ids"] == ["table-5", "table-6"]


def test_booking_requires_confirmation_and_replay_is_idempotent(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        result = prepare(db, loc, request(party_size=2))
        action = db.get(PendingAction, result["action_id"])
        with pytest.raises(DomainError, match="Waiting for customer"):
            commit(db, loc, action)
        confirm_staff(db, action)
        first = commit(db, loc, action)
        second = commit(db, loc, action)
        assert first == second
        assert len(list(db.scalars(select(Reservation).where(Reservation.idempotency_key == action.id)))) == 1


def test_concurrent_booking_exactly_one_commits(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        for table in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            table.state = "available" if table.id == "table-8" else "out_of_service"
        when = now() + timedelta(hours=4)
        actions = []
        for _ in range(2):
            result = prepare(db, loc, PrepareAction(location_id=RESTAURANT, kind="reservation", name="Guest", start_at=when, party_size=8))
            action = db.get(PendingAction, result["action_id"])
            confirm_staff(db, action)
            actions.append(action.id)
    def run(id_):
        try:
            with transaction(database) as db:
                return commit(db, db.get(Location, RESTAURANT), db.get(PendingAction, id_))["status"]
        except DomainError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, actions))
    assert sorted(results) == ["CAPACITY_CHANGED", "confirmed"]


def test_future_reservation_blocks_current_interval(database):
    with transaction(database) as db:
        result = availability(db, db.get(Location, RESTAURANT), Availability(location_id=RESTAURANT, start_at=now() + timedelta(hours=2), party_size=4))
        assert "table-4" not in result.get("resource_ids", [])


@pytest.mark.parametrize("state", ["cleaning", "out_of_service", "held"])
def test_unusable_table_is_not_available_now(database, state):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        for table in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            table.state = state if table.id == "table-8" else "out_of_service"
        result = availability(db, loc, Availability(location_id=RESTAURANT, start_at=now() + timedelta(seconds=1), party_size=8))
        assert result["status"] == "NO_CAPACITY"


def test_stale_floor_returns_unknown(database):
    with transaction(database) as db:
        for table in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            table.verified_at = now() - timedelta(hours=4)
        result = availability(db, db.get(Location, RESTAURANT), Availability(location_id=RESTAURANT, start_at=now() + timedelta(hours=1), party_size=2))
        assert result["status"] == "UNKNOWN"
        assert seating_wait(db, db.get(Location, RESTAURANT), 2)["status"] == "UNKNOWN"


def test_wait_range_with_provenance(database):
    with transaction(database) as db:
        for table in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            if table.id != "table-7":
                table.state = "out_of_service"
        result = seating_wait(db, db.get(Location, RESTAURANT), 6)
        assert result["status"] == "ESTIMATED_WAIT"
        assert result["wait_minutes"]["max"] >= result["wait_minutes"]["min"] > 0
        assert "configured_turn_time" in result["evidence"]["sources"]


def test_waitlist_priority_increases_wait(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        for table in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            table.state = "available" if table.id == "table-8" else "out_of_service"
        baseline = seating_wait(db, loc, 8)
        db.add(WaitlistEntry(tenant_id=DEMO_TENANT, location_id=RESTAURANT, name="Ahead", party_size=8))
        db.flush()
        after = seating_wait(db, loc, 8)
        assert after["wait_minutes"]["min"] > baseline["wait_minutes"]["min"]


def cart(**kwargs):
    return QuoteRequest(location_id=RESTAURANT, items=[CartItem(item_id="menu-burger", quantity=1, **kwargs)])


def test_kitchen_pause_blocks_open_kitchen(database):
    with transaction(database) as db:
        kitchen = db.get(KitchenState, "demo-kitchen")
        kitchen.kitchen_open, kitchen.accepting_takeout = True, False
        with pytest.raises(DomainError) as error:
            quote(db, db.get(Location, RESTAURANT), cart())
        assert error.value.code == "KITCHEN_PAUSED"


def test_modifiers_prices_and_tax(database):
    with transaction(database) as db:
        result = quote(db, db.get(Location, RESTAURANT), cart(modifiers=["extra-cheese"]))
        assert result["subtotal_minor"] == 2050
        assert result["tax_minor"] == 182
        assert result["total_minor"] == 2232


def test_invalid_modifier_and_stock(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        with pytest.raises(DomainError) as error:
            quote(db, loc, cart(modifiers=["made-up"]))
        assert error.value.code == "INVALID_MODIFIER"
        db.get(MenuItem, "menu-burger").available = False
        with pytest.raises(DomainError) as error:
            quote(db, loc, cart())
        assert error.value.code == "ITEM_UNAVAILABLE"


def test_order_capacity_race_requotes_second_cart(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        db.get(KitchenState, "demo-kitchen").capacity_per_bucket = 3
        actions = []
        for _ in range(2):
            result = prepare(db, loc, PrepareAction(location_id=RESTAURANT, kind="order", name="Guest", items=[CartItem(item_id="menu-burger")]))
            action = db.get(PendingAction, result["action_id"])
            confirm_staff(db, action)
            actions.append(action.id)
    def run(id_):
        try:
            with transaction(database) as db:
                return commit(db, db.get(Location, RESTAURANT), db.get(PendingAction, id_))["status"]
        except DomainError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, actions))
    assert sorted(results) == ["QUOTE_CHANGED", "accepted"]


def test_salon_eligible_staff_and_conflict(database):
    with transaction(database) as db:
        loc = db.get(Location, SALON)
        when = now() + timedelta(hours=5)
        incompatible = availability(db, loc, Availability(location_id=SALON, start_at=when, service_id="service-color", staff_id="stylist-2"))
        assert incompatible["status"] == "NO_CAPACITY"
        result = prepare(db, loc, request("appointment", service_id="service-cut", staff_id="stylist-1"))
        action = db.get(PendingAction, result["action_id"])
        confirm_staff(db, action)
        committed = commit(db, loc, action)
        booked = db.get(Reservation, committed["id"])
        conflict = availability(db, loc, Availability(location_id=SALON, start_at=utc(booked.start_at), service_id="service-cut", staff_id="stylist-1"))
        assert conflict["status"] == "NO_CAPACITY"
        assert conflict["alternatives"]


def test_voice_confirmation_requires_readback_and_yes(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        call = Call(tenant_id=DEMO_TENANT, location_id=RESTAURANT, provider="vapi", provider_call_id="confirmation-test")
        db.add(call)
        db.flush()
        proposal = prepare(db, loc, request(), call.id)
        action = db.get(PendingAction, proposal["action_id"])
        observe_transcript(db, call, "user", "yes")
        assert action.confirmed_at is None
        observe_transcript(db, call, "assistant", proposal["readback"])
        observe_transcript(db, call, "user", "yes, but make it tomorrow")
        assert action.confirmed_at is None
        observe_transcript(db, call, "assistant", proposal["readback"])
        observe_transcript(db, call, "user", "yes please")
        assert action.confirmed_at is not None
        assert call.transcript == []
        assert commit(db, loc, action)["status"] == "confirmed"


def test_hangup_cannot_commit(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        call = Call(tenant_id=DEMO_TENANT, location_id=RESTAURANT, provider="vapi", provider_call_id="hangup")
        db.add(call)
        db.flush()
        proposal = prepare(db, loc, request(), call.id)
        action = db.get(PendingAction, proposal["action_id"])
        confirm_staff(db, action)
        call.status = "ended"
        with pytest.raises(DomainError) as error:
            commit(db, loc, action)
        assert error.value.code == "CALL_ENDED"


def test_external_provider_never_silently_falls_back(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        loc.settings = {**loc.settings, "booking_provider": "square"}
        with pytest.raises(DomainError) as error:
            prepare(db, loc, request())
        assert error.value.code == "PROVIDER_WRITE_GATED"
