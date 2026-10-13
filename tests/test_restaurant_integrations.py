from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from app.db import transaction
from app.models import Location, Resource, Reservation, Allocation, Order, ProviderRecord, IntegrationConnection, PendingAction, OperationalProjection, Membership, Tenant, now
from app.domain import prepare, commit, confirm_staff, availability, DomainError, utc
from app.schemas import PrepareAction, Availability
from app.integration_service import connect, sync, connection
from app.integration_adapters import MOCK_ADAPTERS, PROVIDERS
from app.auth import token_for


def make_location(client, software="internal"):
    response = client.post("/api/v1/locations", json={"name": "Provider test restaurant", "vertical": "restaurant", "existing_software": software})
    assert response.status_code == 200, response.json()
    return response.json()["id"]


def at(hour=17, minute=0):
    return (now().astimezone(ZoneInfo("America/New_York")) + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def book(client, loc, party=12, when=None):
    response = client.post("/api/v1/actions/prepare", json={"location_id": loc, "kind": "reservation", "name": "Booking guest", "phone": "+12125550123", "party_size": party, "start_at": (when or at()).isoformat()})
    assert response.status_code == 200, response.json()
    id_ = response.json()["action_id"]
    assert client.post(f"/api/v1/actions/{id_}/confirm").status_code == 200
    return id_, client.post("/api/v1/actions/commit", json={"action_id": id_})


def test_capability_matrix_has_real_differences():
    ot, toast, square = [MOCK_ADAPTERS[p].capabilities for p in ["mock_opentable", "mock_toast", "mock_square"]]
    assert ot["bookings_write"] == "supported" and ot["menu_read"] == "unsupported"
    assert toast["pos_signals_read"] == "supported" and toast["bookings_write"] == "unsupported"
    assert toast["orders_write"] == "unsupported" and toast["table_status_read"] == "unsupported"
    assert square["orders_write"] == "supported" and square["availability_read"] == "unsupported"
    assert set(PROVIDERS) >= {"internal", "toast", "square", "opentable", "mock_toast", "mock_square", "mock_opentable"}


def test_opentable_import_and_large_party_commit_has_one_authority(client, database):
    loc = make_location(client, "mock_opentable")
    snapshot = client.get(f"/api/v1/locations/{loc}/snapshot").json()
    assert snapshot["location"]["settings"]["booking_provider"] == "mock_opentable"
    assert not snapshot["resources"]  # No requirement to recreate provider tables.
    assert len(snapshot["reservations"]) == 1
    assert snapshot["location"]["settings"]["open_hour"] == 17
    action, response = book(client, loc)
    assert response.status_code == 200, response.json()
    result = response.json()
    assert result["provider"] == "mock_opentable" and result["status"] == "confirmed"
    assert client.post("/api/v1/actions/commit", json={"action_id": action}).json() == result
    with database() as db:
        remote = list(db.scalars(select(ProviderRecord).where(ProviderRecord.location_id == loc, ProviderRecord.kind == "reservation", ProviderRecord.data["idempotency_key"].as_string() == action)))
        mirrors = list(db.scalars(select(Reservation).where(Reservation.location_id == loc, Reservation.external_id == result["external_id"])))
        assert len(remote) == len(mirrors) == 1
        assert not db.scalar(select(Allocation).where(Allocation.reservation_id == mirrors[0].id))
    client.post(f"/api/v1/integrations/mock_opentable/sync?location_id={loc}")
    assert len(client.get(f"/api/v1/locations/{loc}/snapshot").json()["reservations"]) == 2


def test_provider_conflict_has_valid_alternatives(client):
    loc = make_location(client, "mock_opentable")
    result = client.post("/api/v1/availability/restaurant", json={"location_id": loc, "start_at": at(19).isoformat(), "party_size": 12}).json()
    assert result["status"] == "NO_CAPACITY" and result["alternatives"]
    assert all(utc(a["start_at"]) >= utc(at(20, 30)) for a in result["alternatives"])


def test_provider_race_commits_exactly_once(client, database):
    loc = make_location(client, "mock_opentable")
    ids = []
    with transaction(database) as db:
        location = db.get(Location, loc)
        for name in ["First", "Second"]:
            action = prepare(db, location, PrepareAction(location_id=loc, kind="reservation", name=name, start_at=at(), party_size=12))
            row = db.get(PendingAction, action["action_id"])
            confirm_staff(db, row)
            ids.append(row.id)
    def run(id_):
        try:
            with transaction(database) as db:
                return commit(db, db.get(Location, loc), db.get(PendingAction, id_))["status"]
        except DomainError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(run, ids)) == ["CAPACITY_CHANGED", "confirmed"]


@pytest.mark.parametrize("scenario,status", [("failure", "UNKNOWN"), ("stale", "UNKNOWN"), ("unavailable", "NO_CAPACITY")])
def test_failed_stale_or_unavailable_provider_never_falls_back(client, scenario, status):
    loc = make_location(client, "mock_opentable")
    result = client.post("/api/v1/integrations/mock_opentable/connect", json={"location_id": loc, "scenario": scenario})
    assert result.status_code == 200
    decision = client.post("/api/v1/availability/restaurant", json={"location_id": loc, "start_at": at().isoformat(), "party_size": 2}).json()
    assert decision["status"] == status
    assert decision["provider"] == "mock_opentable"


def test_ambiguous_write_reconciles_without_duplicate(client, database):
    loc = make_location(client, "mock_opentable")
    client.post("/api/v1/integrations/mock_opentable/connect", json={"location_id": loc, "scenario": "ambiguous"})
    action, response = book(client, loc)
    assert response.json()["status"] == "pending_confirmation"
    assert client.post("/api/v1/actions/commit", json={"action_id": action}).json()["status"] == "pending_confirmation"
    pending_item = next(i for i in client.get(f"/api/v1/attention?location_id={loc}").json() if i["kind"] == "integration_failure")
    assert client.post(f"/api/v1/attention/{pending_item['id']}/decision", json={"decision": "resolved"}).json()["code"] == "PENDING_PROVIDER_CONFIRMATION"
    client.post(f"/api/v1/integrations/mock_opentable/sync?location_id={loc}")
    assert client.post("/api/v1/actions/commit", json={"action_id": action}).json()["status"] == "confirmed"
    with database() as db:
        assert len(list(db.scalars(select(ProviderRecord).where(ProviderRecord.location_id == loc, ProviderRecord.data["idempotency_key"].as_string() == action)))) == 1


def test_pos_signal_import_cannot_verify_table_availability(client, database):
    loc = make_location(client, "mock_toast")
    snapshot = client.get(f"/api/v1/locations/{loc}/snapshot").json()
    assert len(snapshot["resources"]) == 3 and len(snapshot["menu"]) == 1 and len(snapshot["orders"]) == 1
    paid = next(r for r in snapshot["resources"] if r["attributes"].get("pos_signal") == "bill_paid")
    old_verification = paid["verified_at"]
    assert not paid["attributes"]["staff_verified"]
    assert client.post("/api/v1/availability/restaurant", json={"location_id": loc, "start_at": at().isoformat(), "party_size": 2}).json()["status"] == "UNKNOWN"
    body = {"location_id": loc, "event_id": "paid-event", "external_id": "new-paid", "kind": "pos_signal", "observed_at": now().isoformat(), "data": {"table_guid": "window-2", "check_state": "PAID"}}
    assert client.post("/api/v1/integrations/mock_toast/mock-events", json=body).json()["status"] == "processed"
    assert client.post("/api/v1/integrations/mock_toast/mock-events", json=body).json()["status"] == "already_processed"
    current = client.get(f"/api/v1/locations/{loc}/snapshot").json()
    assert next(r for r in current["resources"] if r["id"] == paid["id"])["verified_at"] == old_verification
    assert client.patch(f"/api/v1/orders/{snapshot['orders'][0]['id']}", json={"status": "ready"}).json()["code"] == "PROVIDER_MANAGED_RECORD"


def test_internal_large_party_requires_explicit_staff_arrangement(client):
    loc = make_location(client)
    tables = []
    for i in range(3):
        response = client.post("/api/v1/resources", json={"location_id": loc, "name": f"T{i}", "capacity": 4})
        assert response.status_code == 200
        tables.append(response.json()["id"])
    decision = client.post("/api/v1/availability/restaurant", json={"location_id": loc, "start_at": at().isoformat(), "party_size": 12}).json()
    assert decision["status"] == "UNKNOWN" and "STAFF_ARRANGEMENT_REQUIRED" in decision["reason_codes"]
    request = client.post("/api/v1/capacity-requests", json={"location_id": loc, "party_size": 12, "start_at": at().isoformat()}).json()
    assert request["booking_confirmed"] is False
    review = client.post(f"/api/v1/capacity-requests/{request['request_id']}/arrangement", json={"resource_ids": tables, "verified_arrangement": True})
    assert review.status_code == 200 and not review.json()["booking_confirmed"]
    assert client.post("/api/v1/availability/restaurant", json={"location_id": loc, "start_at": at().isoformat(), "party_size": 12}).json()["status"] == "AVAILABLE"


def test_authority_switch_is_guarded_and_staff_requests_do_not_auto_book(client):
    loc = make_location(client, "mock_opentable")
    result = client.post(f"/api/v1/locations/{loc}/booking-authority", json={"booking_provider": "internal"})
    assert result.json()["code"] == "PROVIDER_SWITCH_CONFLICT"
    staff_loc = make_location(client, "staff_requests")
    decision = client.post("/api/v1/availability/restaurant", json={"location_id": staff_loc, "start_at": at().isoformat(), "party_size": 2}).json()
    assert decision["status"] == "UNKNOWN" and "STAFF_APPROVAL_REQUIRED" in decision["reason_codes"]


def test_provider_staff_pause_and_tenant_isolation(client, database):
    loc = make_location(client, "mock_opentable")
    client.patch(f"/api/v1/locations/{loc}", json={"intake_paused": True})
    assert client.post("/api/v1/availability/restaurant", json={"location_id": loc, "start_at": at().isoformat(), "party_size": 12}).json()["status"] == "NO_CAPACITY"
    with transaction(database) as db:
        db.add(Tenant(id="provider-other", name="Other"))
        member = Membership(id="provider-other-owner", tenant_id="provider-other", email="other@example.com", role="owner")
        db.add(member); db.flush()
        token = token_for(member)
    client.cookies.set("frontdesk_session", token)
    assert client.post(f"/api/v1/integrations/mock_opentable/sync?location_id={loc}").status_code == 404
    assert client.post("/api/v1/integrations/mock_toast/connect", json={"location_id": loc}).status_code == 404


def test_provider_cancel_and_reschedule_update_same_remote_record(client, database):
    loc = make_location(client, "mock_opentable")
    action, response = book(client, loc, party=2)
    id_ = response.json()["id"]
    changed = client.post(f"/api/v1/reservations/{id_}/reschedule", json={"location_id": loc, "start_at": at(20).isoformat(), "party_size": 2})
    assert changed.status_code == 200, changed.json()
    assert client.patch(f"/api/v1/reservations/{id_}", json={"status": "cancelled"}).status_code == 200
    with database() as db:
        mirror = db.get(Reservation, id_)
        remote = db.scalar(select(ProviderRecord).where(ProviderRecord.location_id == loc, ProviderRecord.external_id == mirror.external_id))
        assert mirror.status == remote.data["status"] == "cancelled"


def test_square_import_stock_and_order_authority(client, database):
    loc = make_location(client, "mock_square")
    snapshot = client.get(f"/api/v1/locations/{loc}/snapshot").json()
    assert len(snapshot["menu"]) == 3 and not snapshot["resources"]
    soup = next(item for item in snapshot["menu"] if item["name"] == "Soup of the day")
    assert client.post("/api/v1/orders/quote", json={"location_id": loc, "items": [{"item_id": soup["id"]}]}).json()["code"] == "ITEM_UNAVAILABLE"
    selected = client.post(f"/api/v1/locations/{loc}/booking-authority", json={"booking_provider": "internal", "order_provider": "mock_square"})
    assert selected.status_code == 200, selected.text
    # The happy-path write is independent of the wall clock. Imported sample
    # hours are tested separately; staff can explicitly override operating hours.
    client.patch(f"/api/v1/locations/{loc}", json={"open_hour": 0, "close_hour": 24}).raise_for_status()
    salad = next(item for item in snapshot["menu"] if item["name"] == "Seasonal salad")
    proposal = client.post("/api/v1/actions/prepare", json={"location_id": loc, "kind": "order", "name": "Order guest", "items": [{"item_id": salad["id"]}]}).json()
    assert "action_id" in proposal, proposal
    client.post(f"/api/v1/actions/{proposal['action_id']}/confirm")
    result = client.post("/api/v1/actions/commit", json={"action_id": proposal["action_id"]})
    assert result.status_code == 200 and result.json()["provider"] == "mock_square"


def test_live_provider_is_never_silently_simulated(client):
    loc = make_location(client)
    assert client.post("/api/v1/integrations/opentable/connect", json={"location_id": loc}).json()["code"] == "PROVIDER_ACCESS_REQUIRED"


def test_staff_pause_after_confirmation_blocks_external_commit(client, database):
    loc = make_location(client, "mock_opentable")
    proposal = client.post('/api/v1/actions/prepare', json={"location_id": loc, "kind": "reservation", "name": "Pause test", "party_size": 2, "start_at": at().isoformat()}).json()
    client.post(f"/api/v1/actions/{proposal['action_id']}/confirm")
    client.patch(f"/api/v1/locations/{loc}", json={"intake_paused": True})
    result = client.post('/api/v1/actions/commit', json={"action_id": proposal['action_id']})
    assert result.json()['code'] == 'CAPACITY_CHANGED'
    with database() as db:
        assert not db.scalar(select(ProviderRecord).where(ProviderRecord.location_id == loc, ProviderRecord.data['idempotency_key'].as_string() == proposal['action_id']))


def test_sync_keeps_staff_menu_disable_and_provider_stock_limits(client):
    loc = make_location(client, 'mock_square')
    menu = client.get(f'/api/v1/locations/{loc}/snapshot').json()['menu']
    salad = next(i for i in menu if i['name'] == 'Seasonal salad')
    soup = next(i for i in menu if i['name'] == 'Soup of the day')
    client.patch(f"/api/v1/menu/{salad['id']}", json={'available': False})
    client.post(f'/api/v1/integrations/mock_square/sync?location_id={loc}')
    current = client.get(f'/api/v1/locations/{loc}/snapshot').json()['menu']
    assert not next(i for i in current if i['id'] == salad['id'])['available']
    assert not client.patch(f"/api/v1/menu/{soup['id']}", json={'available': True}).json()['available']


def test_pending_write_blocks_switch_after_proposal_expiry_and_clears_attention(client, database):
    loc = make_location(client, 'mock_opentable')
    client.post('/api/v1/integrations/mock_opentable/connect', json={'location_id': loc, 'scenario': 'ambiguous'})
    action, response = book(client, loc, party=2)
    assert response.json()['status'] == 'pending_confirmation'
    with transaction(database) as db:
        db.get(PendingAction, action).expires_at = now() - timedelta(hours=1)
    changed = client.post(f'/api/v1/locations/{loc}/booking-authority', json={'booking_provider': 'internal'})
    assert changed.json()['code'] == 'PROVIDER_SWITCH_CONFLICT'
    assert any(i['kind'] == 'integration_failure' for i in client.get(f'/api/v1/attention?location_id={loc}').json())
    client.post(f'/api/v1/integrations/mock_opentable/sync?location_id={loc}')
    assert not any(i['kind'] == 'integration_failure' for i in client.get(f'/api/v1/attention?location_id={loc}').json())


@pytest.mark.parametrize('provider', ['mock_toast', 'mock_square'])
@pytest.mark.parametrize('scenario', ['failure', 'stale', 'unavailable'])
def test_pos_provider_bad_health_or_stock_blocks_quotes(client, provider, scenario):
    loc = make_location(client, provider)
    client.post(f'/api/v1/integrations/{provider}/connect', json={'location_id': loc, 'scenario': scenario})
    item = client.get(f'/api/v1/locations/{loc}/snapshot').json()['menu'][0]
    response = client.post('/api/v1/orders/quote', json={'location_id': loc, 'items': [{'item_id': item['id']}]})
    assert response.status_code != 200
    assert response.json()['code'] in ['STALE_PROVIDER_DATA', 'ITEM_UNAVAILABLE']


def test_import_floor_does_not_orphan_active_internal_table_assignment(client):
    loc = make_location(client)
    client.post('/api/v1/resources', json={'location_id': loc, 'name': 'Existing table', 'capacity': 2})
    book(client, loc, party=2)
    response = client.post('/api/v1/integrations/mock_toast/connect', json={'location_id': loc})
    assert response.json()['code'] == 'FLOOR_IMPORT_CONFLICT'
    assert client.get(f'/api/v1/locations/{loc}/snapshot').json()['resources'][0]['name'] == 'Existing table'


def test_square_webhook_normalizes_stock_and_rejects_older_or_invalid_events(client):
    loc = make_location(client, 'mock_square')
    body = {'location_id': loc, 'event_id': 'catalog-change', 'external_id': 'sq-salad', 'kind': 'menu', 'observed_at': now().isoformat(), 'data': {'item_name': 'Seasonal salad', 'category': 'Mains', 'amount_minor': 1600, 'in_stock': False}}
    assert client.post('/api/v1/integrations/mock_square/mock-events', json=body).json()['status'] == 'processed'
    body.update(event_id='older', observed_at=(now() - timedelta(hours=1)).isoformat())
    body['data']['in_stock'] = True
    assert client.post('/api/v1/integrations/mock_square/mock-events', json=body).json()['status'] == 'ignored_older_event'
    item = next(i for i in client.get(f'/api/v1/locations/{loc}/snapshot').json()['menu'] if i['name'] == 'Seasonal salad')
    assert item['price_minor'] == 1600 and not item['available']
    body.update(event_id='invalid', observed_at=now().isoformat(), data={'in_stock': True})
    assert client.post('/api/v1/integrations/mock_square/mock-events', json=body).json()['code'] == 'INVALID_PROVIDER_PAYLOAD'


def test_disconnect_excludes_imported_catalog_and_reconnect_preserves_provider_identity(client):
    loc = make_location(client, 'mock_toast')
    original = client.get(f'/api/v1/locations/{loc}/snapshot').json()
    assert client.post(f'/api/v1/integrations/mock_toast/disconnect?location_id={loc}').status_code == 200
    disconnected = client.get(f'/api/v1/locations/{loc}/snapshot').json()
    assert not disconnected['menu'] and not disconnected['resources']
    assert not client.get(f'/api/v1/menu?location_id={loc}').json()
    assert not client.get(f'/api/v1/locations/{loc}/tables').json()
    assert client.post('/api/v1/integrations/mock_toast/connect', json={'location_id': loc}).status_code == 200
    reconnected = client.get(f'/api/v1/locations/{loc}/snapshot').json()
    assert {r['id'] for r in reconnected['resources']} == {r['id'] for r in original['resources']}
    assert reconnected['menu'][0]['id'] == original['menu'][0]['id']


def test_provider_menu_cannot_replace_selected_order_catalog(client):
    loc = make_location(client, 'mock_square')
    assert client.post(f'/api/v1/locations/{loc}/booking-authority', json={'booking_provider': 'internal', 'order_provider': 'mock_square'}).status_code == 200
    response = client.post('/api/v1/integrations/mock_toast/connect', json={'location_id': loc})
    assert response.json()['code'] == 'CATALOG_PROVIDER_CONFLICT'
    snapshot = client.get(f'/api/v1/locations/{loc}/snapshot').json()
    assert snapshot['location']['settings']['catalog_provider'] == 'mock_square'
    assert not snapshot['resources']


def test_new_pos_check_conflicts_with_old_staff_free_observation(client):
    loc = make_location(client, 'mock_toast')
    table = next(r for r in client.get(f'/api/v1/locations/{loc}/snapshot').json()['resources'] if r['name'] == 'Patio 1')
    client.post(f"/api/v1/locations/{loc}/tables/{table['id']}/transitions", json={'state': 'available'}).raise_for_status()
    query = {'location_id': loc, 'start_at': at().isoformat(), 'party_size': 4}
    assert client.post('/api/v1/availability/restaurant', json=query).json()['status'] == 'AVAILABLE'
    event = {'location_id': loc, 'event_id': 'new-open-check', 'external_id': 'new-check', 'kind': 'pos_signal', 'observed_at': now().isoformat(), 'data': {'table_guid': 'patio-1', 'check_state': 'OPEN'}}
    client.post('/api/v1/integrations/mock_toast/mock-events', json=event).raise_for_status()
    assert client.post('/api/v1/availability/restaurant', json=query).json()['status'] == 'UNKNOWN'
    # New explicit staff verification resolves the signal conflict.
    client.post(f"/api/v1/locations/{loc}/tables/{table['id']}/transitions", json={'state': 'available'}).raise_for_status()
    assert client.post('/api/v1/availability/restaurant', json=query).json()['status'] == 'AVAILABLE'
