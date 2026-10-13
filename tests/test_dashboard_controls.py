"""Staff dashboard control endpoints (floor, kitchen, settings)."""
from datetime import timedelta
from sqlalchemy import select
from app.auth import token_for
from app.db import transaction
from app.models import AuditEvent, Customer, Membership, PendingAction, Reservation, Resource, Tenant, now
from app.seed import RESTAURANT, SALON
from app.config import settings


def proposal(client, **changes):
    result = client.post("/api/v1/actions/prepare", json={"location_id": RESTAURANT, "kind": "reservation", "name": "Dashboard guest", "phone": "+12125550123", "start_at": (now() + timedelta(hours=4)).isoformat(), "party_size": 2, **changes})
    assert result.status_code == 200, result.json()
    return result.json()["action_id"]


def test_attention_approval_commits_once_and_is_audited(client, database):
    action = proposal(client)
    assert f"action:{action}" in [r["id"] for r in client.get(f"/api/v1/attention?location_id={RESTAURANT}").json()]
    route = f"/api/v1/attention/action:{action}/decision"
    approved = client.post(route, json={"decision": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "confirmed"
    assert client.post(route, json={"decision": "approve"}).status_code == 409
    assert f"action:{action}" not in [r["id"] for r in client.get(f"/api/v1/attention?location_id={RESTAURANT}").json()]
    with database() as db:
        assert len(list(db.scalars(select(Reservation).where(Reservation.id == approved.json()["id"])))) == 1
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "attention.reviewed", AuditEvent.target_id == f"action:{action}"))


def test_rejected_proposal_has_no_business_side_effect(client, database):
    action = proposal(client)
    assert client.post(f"/api/v1/attention/action:{action}/decision", json={"decision": "reject"}).json()["status"] == "rejected"
    assert client.post("/api/v1/actions/commit", json={"action_id": action}).status_code == 409
    with database() as db:
        assert db.get(PendingAction, action).state == "rejected"
        assert not db.scalar(select(Reservation).where(Reservation.idempotency_key == action))


def test_attention_cannot_replace_voice_caller_confirmation(client):
    call = client.post("/api/v1/voice/test-calls", json={"location_id": RESTAURANT}).json()
    result = client.post(f"/api/v1/voice/test-calls/{call['id']}/tools", json={"request_id": "proposal", "name": "prepare_action", "arguments": {"kind": "reservation", "name": "Caller", "party_size": 2, "start_at": (now() + timedelta(hours=4)).isoformat()}}).json()
    action = result["action_id"]
    queue = client.get(f"/api/v1/attention?location_id={RESTAURANT}").json()
    row = next(r for r in queue if r["id"] == f"action:{action}")
    assert row["confirmation_required"] and not row["can_approve"]
    response = client.post(f"/api/v1/attention/action:{action}/decision", json={"decision": "approve"})
    assert response.json()["code"] == "CONFIRMATION_REQUIRED"


def test_pause_after_readback_blocks_ai_commit_but_allows_staff_booking(client):
    call = client.post("/api/v1/voice/test-calls", json={"location_id": RESTAURANT}).json()
    route = f"/api/v1/voice/test-calls/{call['id']}/tools"
    prepared = client.post(route, json={"request_id": "prepare", "name": "prepare_action", "arguments": {"kind": "reservation", "name": "Caller", "party_size": 2, "start_at": (now() + timedelta(hours=4)).isoformat()}}).json()
    action = prepared["action_id"]
    assert client.post(f"/api/v1/voice/test-calls/{call['id']}/confirm", json={"action_id": action}).status_code == 200
    assert client.patch(f"/api/v1/locations/{RESTAURANT}", json={"ai_actions": {"bookings": False}}).status_code == 200
    assert client.post(route, json={"request_id": "commit", "name": "commit_action", "arguments": {"action_id": action}}).json()["code"] == "AI_ACTION_PAUSED"
    # Staff attention approval cannot bypass the same pause for a voice action.
    assert client.post(f"/api/v1/attention/action:{action}/decision", json={"decision": "approve"}).json()["code"] == "AI_ACTION_PAUSED"
    manual = proposal(client)
    assert client.post(f"/api/v1/attention/action:{manual}/decision", json={"decision": "approve"}).json()["status"] == "confirmed"


def test_customer_edit_preserves_contact_and_location_scoping(client, database):
    action = proposal(client)
    booking = client.post(f"/api/v1/attention/action:{action}/decision", json={"decision": "approve"}).json()
    rows = client.get(f"/api/v1/customers?location_id={RESTAURANT}").json()
    customer = next(r for r in rows if r["display_name"] == "Dashboard guest")
    assert customer["phone_masked"] == "•••• 0123"
    assert "phone" not in customer
    assert customer["id"] not in [r["id"] for r in client.get(f"/api/v1/customers?location_id={SALON}").json()]
    assert client.patch(f"/api/v1/customers/{customer['id']}", json={"display_name": "Updated guest"}).status_code == 200
    with database() as db:
        assert db.get(Customer, customer["id"]).phone == "+12125550123"
        assert db.get(Reservation, booking["id"]).customer_id == customer["id"]


def test_attention_and_customer_cross_tenant_access_denied(client, database):
    action = proposal(client)
    booking = client.post(f"/api/v1/attention/action:{action}/decision", json={"decision": "approve"}).json()
    with transaction(database) as db:
        customer = db.get(Reservation, booking["id"]).customer_id
        db.add(Tenant(id="unrelated", name="Other"))
        member = Membership(id="other", tenant_id="unrelated", email="other@example.com", role="owner")
        db.add(member)
        db.flush()
        token = token_for(member)
    client.cookies.set("frontdesk_session", token)
    assert client.get(f"/api/v1/attention?location_id={RESTAURANT}").status_code == 404
    assert client.get(f"/api/v1/customers?location_id={RESTAURANT}").status_code == 404
    assert client.patch(f"/api/v1/customers/{customer}", json={"display_name": "Bad edit"}).status_code == 404
    assert client.post(f"/api/v1/attention/action:{action}/decision", json={"decision": "reject"}).status_code == 404


def test_stale_table_review_requires_real_state_verification(client, database):
    with transaction(database) as db:
        db.get(Resource, "table-1").verified_at = now() - timedelta(hours=2)
    assert "table:table-1" in [r["id"] for r in client.get(f"/api/v1/attention?location_id={RESTAURANT}").json()]
    assert client.post("/api/v1/attention/table:table-1/decision", json={"decision": "resolved"}).status_code == 422
    assert client.post(f"/api/v1/locations/{RESTAURANT}/tables/table-1/transitions", json={"state": "available"}).status_code == 200
    assert "table:table-1" not in [r["id"] for r in client.get(f"/api/v1/attention?location_id={RESTAURANT}").json()]


def test_integration_health_survives_navigation_and_credential_change(client, monkeypatch):
    from app import main
    monkeypatch.setattr(settings(), "square_access_token", "test-token")
    monkeypatch.setattr(settings(), "square_location_id", "test-seller-location")
    monkeypatch.setattr(main.SquareAdapter, "test", lambda _: {"provider": "square", "mode": "degraded", "capabilities": {"locations_read": "degraded", "bookings_write": "requires_permission"}, "error_code": "PROVIDER_TIMEOUT"})
    assert client.post(f"/api/v1/integrations/square/test?location_id={RESTAURANT}").status_code == 200
    rows = client.get(f"/api/v1/integrations?location_id={RESTAURANT}").json()
    square = next(r for r in rows if r["provider"] == "square")
    assert square["mode"] == "degraded" and square["last_tested_at"]
    assert "test-token" not in str(rows)
    assert "_configuration_ref" not in square
    monkeypatch.setattr(settings(), "square_access_token", "changed-test-token")
    square = next(r for r in client.get(f"/api/v1/integrations?location_id={RESTAURANT}").json() if r["provider"] == "square")
    assert square["mode"] == "configured_untested"
