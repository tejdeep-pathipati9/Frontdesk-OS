from datetime import timedelta
from sqlalchemy import select
from app.models import Membership, Tenant, Location, ToolExecution, now
from app.db import transaction
from app.seed import RESTAURANT, SALON
from app.auth import token_for
from app.config import settings


def test_auth_and_cross_tenant_denied(client, database):
    assert client.get(f"/api/v1/locations/{RESTAURANT}/snapshot").status_code == 200
    with transaction(database) as db:
        db.add(Tenant(id="other-tenant", name="Other"))
        db.add(Membership(id="other-owner", tenant_id="other-tenant", email="other@example.com", role="owner"))
        db.flush()
        token = token_for(db.get(Membership, "other-owner"))
    client.cookies.set("frontdesk_session", token)
    assert client.get(f"/api/v1/locations/{RESTAURANT}/snapshot").status_code == 404
    assert client.post("/api/v1/availability/restaurant", json={"location_id": RESTAURANT, "start_at": (now() + timedelta(hours=3)).isoformat()}).status_code == 404
    assert client.get("/api/v1/locations").json() == []


def test_host_cannot_change_policies(client, database):
    with transaction(database) as db:
        member = db.get(Membership, "demo-owner")
        member.role = "host"
    assert client.patch(f"/api/v1/locations/{RESTAURANT}", json={"intake_paused": True}).status_code == 403
    assert client.post(f"/api/v1/locations/{RESTAURANT}/tables/table-1/transitions", json={"state": "seated", "party_size": 2}).status_code == 200


def test_no_offset_is_rejected(client):
    response = client.post("/api/v1/availability/restaurant", json={"location_id": RESTAURANT, "start_at": "2026-11-01T01:30:00"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_table_lifecycle_and_kitchen_persist(client):
    route = f"/api/v1/locations/{RESTAURANT}/tables/table-1/transitions"
    assert client.post(route, json={"state": "seated", "party_size": 2}).status_code == 200
    assert client.post(route, json={"state": "available"}).status_code == 409
    assert client.post(route, json={"state": "cleaning"}).status_code == 200
    assert client.post(route, json={"state": "available"}).status_code == 200
    assert client.patch(f"/api/v1/locations/{RESTAURANT}/operational-state", json={"accepting_takeout": False}).status_code == 200
    assert client.post("/api/v1/orders/quote", json={"location_id": RESTAURANT, "items": [{"item_id": "menu-burger"}]}).json()["code"] == "KITCHEN_PAUSED"
    assert len(client.get(f"/api/v1/audit-events?location_id={RESTAURANT}").json()) >= 4


def test_tool_test_flow_and_replay(client, database):
    call = client.post("/api/v1/voice/test-calls", json={"location_id": RESTAURANT}).json()
    route = f"/api/v1/voice/test-calls/{call['id']}/tools"
    payload = {"request_id": "prepare-1", "name": "prepare_action", "arguments": {"kind": "reservation", "name": "Guest", "party_size": 2, "start_at": (now() + timedelta(hours=4)).isoformat()}}
    prepared = client.post(route, json=payload).json()
    assert client.post(route, json=payload).json() == prepared
    assert client.post(route, json={"request_id": "bad-confirm", "name": "commit_action", "arguments": {"action_id": prepared["action_id"], "confirmed": True}}).json()["code"] == "INVALID_ARGUMENTS"
    assert client.post(route, json={"request_id": "without-confirm", "name": "commit_action", "arguments": {"action_id": prepared["action_id"]}}).json()["code"] == "CONFIRMATION_REQUIRED"
    assert client.post(f"/api/v1/voice/test-calls/{call['id']}/confirm", json={"action_id": prepared["action_id"]}).status_code == 200
    payload = {"request_id": "commit-1", "name": "commit_action", "arguments": {"action_id": prepared["action_id"]}}
    result = client.post(route, json=payload).json()
    assert result["status"] == "confirmed"
    assert client.post(route, json=payload).json() == result
    client.post(f"/api/v1/voice/test-calls/{call['id']}/end")
    detail = client.get(f"/api/v1/calls/{call['id']}").json()
    assert detail["outcome"] == "fulfilled"
    assert len(detail["reservations"]) == 1
    assert len(detail["tools"]) == 4


def test_webhook_auth_mapping_and_dedup(client, monkeypatch):
    monkeypatch.setattr(settings(), "vapi_webhook_secret", "test-webhook-secret")
    monkeypatch.setattr(settings(), "vapi_restaurant_assistant_id", "assistant-restaurant")
    body = {"message": {"id": "event1", "type": "status-update", "status": "in-progress", "call": {"id": "vapi-test", "assistantId": "assistant-restaurant"}}}
    assert client.post("/api/v1/voice/vapi", json=body).status_code == 401
    headers = {"Authorization": "Bearer test-webhook-secret"}
    assert client.post("/api/v1/voice/vapi", json=body, headers=headers).json()["status"] == "processed"
    assert client.post("/api/v1/voice/vapi", json=body, headers=headers).json()["status"] == "already_processed"
    body["message"]["call"]["assistantId"] = "unknown"
    assert client.post("/api/v1/voice/vapi", json=body, headers=headers).status_code == 403


def test_cross_origin_write_denied(client):
    response = client.patch(f"/api/v1/locations/{RESTAURANT}", json={"intake_paused": True}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_missing_integrations_are_honestly_labeled(client):
    data = client.get(f"/api/v1/integrations?location_id={RESTAURANT}").json()
    assert data[1]["mode"] == "not_configured"
    response = client.post(f"/api/v1/integrations/toast/test?location_id={RESTAURANT}")
    assert response.json()["capabilities"]["orders_write"] == "requires_permission"
