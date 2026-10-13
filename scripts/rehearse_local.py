"""Exercise full local flows against a disposable copy, never the dashboard DB."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / 'services/api'))


def main():
    from app.config import Settings
    cfg = Settings()
    parsed = make_url(cfg.database_url)
    if not parsed.drivername.startswith('sqlite'):
        raise SystemExit('This rehearsal is restricted to the local SQLite workspace.')
    source = Path(parsed.database)
    if not source.is_absolute():
        source = ROOT / source
    with tempfile.TemporaryDirectory(prefix='frontdesk-rehearsal-') as scratch:
        target = Path(scratch) / 'rehearsal.db'
        with sqlite3.connect(source) as original, sqlite3.connect(target) as copy:
            original.backup(copy)
        os.environ['DATABASE_URL'] = f'sqlite:///{target}'
        from fastapi.testclient import TestClient
        from app.main import app
        from app.models import now
        with TestClient(app) as client:
            client.post('/api/v1/auth/login', json={'email': cfg.owner_email, 'password': cfg.owner_password}).raise_for_status()
            original = client.get('/api/v1/locations/demo-restaurant/snapshot').json()
            assert original['location']['name'] == 'Momentuum Bistro'
            tomorrow = (now().astimezone(ZoneInfo(original['location']['timezone'])) + timedelta(days=1)).replace(hour=13, minute=0, second=0, microsecond=0)
            when = tomorrow.isoformat()
            decision = client.post('/api/v1/availability/restaurant', json={'location_id': 'demo-restaurant', 'start_at': when, 'party_size': 2}).json()
            assert decision['status'] == 'AVAILABLE', decision
            def book(body):
                response = client.post('/api/v1/actions/prepare', json=body)
                response.raise_for_status()
                id_ = response.json()['action_id']
                client.post(f'/api/v1/actions/{id_}/confirm').raise_for_status()
                response = client.post('/api/v1/actions/commit', json={'action_id': id_})
                response.raise_for_status()
                assert client.post('/api/v1/actions/commit', json={'action_id': id_}).json()['id'] == response.json()['id']
                return response.json()
            reservation = book({'location_id': 'demo-restaurant', 'kind': 'reservation', 'name': 'Local rehearsal', 'party_size': 2, 'start_at': when})
            assert reservation['status'] == 'confirmed'
            client.post(f"/api/v1/reservations/{reservation['id']}/reschedule", json={'location_id': 'demo-restaurant', 'start_at': (tomorrow + timedelta(hours=2)).isoformat(), 'party_size': 2}).raise_for_status()
            client.patch(f"/api/v1/reservations/{reservation['id']}", json={'status': 'cancelled'}).raise_for_status()
            print('Restaurant availability, reservation, reschedule, cancellation and replay: PASS')
            wait = client.post('/api/v1/wait-estimates/seating', json={'location_id': 'demo-restaurant', 'name': 'Rehearsal wait', 'party_size': 4}).json()
            assert wait['status'] in ['AVAILABLE_NOW', 'ESTIMATED_WAIT'], wait
            client.post('/api/v1/waitlist', json={'location_id': 'demo-restaurant', 'name': 'Rehearsal wait', 'party_size': 4}).raise_for_status()
            print('Seating estimate and waitlist: PASS')
            client.patch('/api/v1/locations/demo-restaurant/operational-state', json={'accepting_takeout': False}).raise_for_status()
            cart = [{'item_id': 'menu-burger', 'quantity': 2, 'modifiers': ['extra-cheese']}]
            assert client.post('/api/v1/orders/quote', json={'location_id': 'demo-restaurant', 'items': cart}).json()['code'] == 'KITCHEN_PAUSED'
            client.patch('/api/v1/locations/demo-restaurant/operational-state', json={'accepting_takeout': True}).raise_for_status()
            order = book({'location_id': 'demo-restaurant', 'kind': 'order', 'name': 'Rehearsal takeout', 'items': cart})
            for status in ['preparing', 'ready', 'picked_up']:
                client.patch(f"/api/v1/orders/{order['id']}", json={'status': status}).raise_for_status()
            print('Kitchen pause, priced modifiers, order submission and lifecycle: PASS')
            appointment = book({'location_id': 'demo-salon', 'kind': 'appointment', 'name': 'Rehearsal salon', 'start_at': when, 'service_id': 'service-cut', 'staff_id': 'stylist-1'})
            assert appointment['status'] == 'confirmed'
            conflict = client.post('/api/v1/availability/salon', json={'location_id': 'demo-salon', 'start_at': when, 'service_id': 'service-cut', 'staff_id': 'stylist-1'}).json()
            assert conflict['status'] == 'NO_CAPACITY' and conflict['alternatives'], conflict
            print('Salon booking, conflict and alternatives: PASS')
            for provider in ['mock_opentable', 'mock_toast', 'mock_square']:
                location = client.post('/api/v1/locations', json={'name': 'Rehearsal provider', 'vertical': 'restaurant', 'existing_software': provider})
                location.raise_for_status()
                connections = client.get('/api/v1/integrations', params={'location_id': location.json()['id']}).json()
                assert next(c for c in connections if c['provider'] == provider)['sync_status'] == 'healthy'
            print('Provider imports with distinct capabilities and sync health: PASS')
        print('Disposable SQLite copy removed. Your dashboard records were not changed by this rehearsal.')


if __name__ == '__main__':
    main()
