from app.db import transaction
from app.models import Location, Resource, KitchenState, Reservation, now
from app.seed import RESTAURANT
from app.rehearsal import refresh_sample_observations
from app.domain import DomainError
import pytest


def test_sample_refresh_keeps_closures_pauses_and_bookings(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        loc.settings = {**loc.settings, "intake_paused": True}
        closed = db.get(Resource, 'table-1')
        closed.state = 'out_of_service'
        observed = closed.verified_at
        occupied = db.get(Resource, 'table-3')
        seated_at = occupied.seated_at
        kitchen = db.get(KitchenState, 'demo-kitchen')
        kitchen.accepting_takeout = False
        reservation = db.get(Reservation, 'seed-reservation')
        start_at, status = reservation.start_at, reservation.status
        result = refresh_sample_observations(db)
        assert result['tables_refreshed'] == 7 and result['intake_paused']
        assert closed.state == 'out_of_service' and closed.verified_at == observed
        assert occupied.state == 'seated' and occupied.seated_at == seated_at
        assert not kitchen.accepting_takeout
        assert (reservation.start_at, reservation.status) == (start_at, status)


def test_sample_refresh_cannot_verify_connected_floor(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        loc.settings = {**loc.settings, 'floor_provider': 'mock_toast'}
        with pytest.raises(DomainError, match='connected provider'):
            refresh_sample_observations(db)
