"""Reservation modify/cancel flows via pending actions."""
from datetime import timedelta
import pytest
from app.db import transaction
from app.models import Location, PendingAction, Reservation, now
from app.schemas import PrepareAction, CartItem
from app.domain import prepare, confirm_staff, commit, lookup_order, DomainError, utc
from app.seed import RESTAURANT


def initial_booking(db, loc):
    proposal = prepare(db, loc, PrepareAction(location_id=RESTAURANT, kind="reservation", name="Verified guest", phone="+12125550123", start_at=now() + timedelta(hours=4), party_size=2))
    action = db.get(PendingAction, proposal["action_id"])
    confirm_staff(db, action)
    return commit(db, loc, action)


def test_verified_cancel_has_bound_confirmation(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        original = initial_booking(db, loc)
        with pytest.raises(DomainError) as error:
            prepare(db, loc, PrepareAction(location_id=RESTAURANT, kind="cancel_booking", name="Guest", phone="+12125550999", booking_reference=original["reference"]))
        assert error.value.code == "VERIFICATION_FAILED"
        proposal = prepare(db, loc, PrepareAction(location_id=RESTAURANT, kind="cancel_booking", name="Guest", phone="+12125550123", booking_reference=original["reference"]))
        action = db.get(PendingAction, proposal["action_id"])
        with pytest.raises(DomainError) as error:
            commit(db, loc, action)
        assert error.value.code == "CONFIRMATION_REQUIRED"
        confirm_staff(db, action)
        result = commit(db, loc, action)
        assert result["status"] == "cancelled"
        assert db.get(Reservation, original["id"]).status == "cancelled"
        assert commit(db, loc, action) == result


def test_verified_reschedule_rechecks_and_moves_allocation(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        original = initial_booking(db, loc)
        new_start = now() + timedelta(hours=6)
        proposal = prepare(db, loc, PrepareAction(location_id=RESTAURANT, kind="reschedule_booking", name="Guest", phone="+12125550123", booking_reference=original["reference"], start_at=new_start, party_size=2))
        action = db.get(PendingAction, proposal["action_id"])
        confirm_staff(db, action)
        result = commit(db, loc, action)
        assert result["status"] == "confirmed"
        assert utc(db.get(Reservation, original["id"]).start_at) == utc(new_start)


def test_order_status_requires_matching_phone(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        proposal = prepare(db, loc, PrepareAction(location_id=RESTAURANT, kind="order", name="Guest", phone="+12125550123", items=[CartItem(item_id="menu-burger")]))
        action = db.get(PendingAction, proposal["action_id"])
        confirm_staff(db, action)
        result = commit(db, loc, action)
        assert lookup_order(db, loc, result["reference"], "+12125550123")["status"] == "accepted"
        with pytest.raises(DomainError) as error:
            lookup_order(db, loc, result["reference"], "+12125550999")
        assert error.value.code == "VERIFICATION_FAILED"
