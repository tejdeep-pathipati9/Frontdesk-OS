import sys
from pathlib import Path
from datetime import timedelta
import httpx
from fastapi.testclient import TestClient
from app.config import settings
from app.models import now
from app.domain import business_open

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from export_vapi import assistant_config
import vapi_gateway


def test_gateway_exposes_only_authenticated_webhook(monkeypatch):
    monkeypatch.setattr(vapi_gateway, "dotenv_values", lambda _: {"VAPI_WEBHOOK_SECRET": "fixture-secret"})
    with TestClient(vapi_gateway.app) as gateway:
        assert gateway.get("/docs").status_code == 404
        assert gateway.post("/api/v1/auth/login", json={"demo": True}).status_code == 404
        assert gateway.get("/api/v1/locations").status_code == 404
        assert gateway.post("/api/v1/voice/vapi", json={}).status_code == 401
        assert gateway.post("/api/v1/voice/vapi", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert gateway.post("/api/v1/voice/vapi", content=b"x" * (vapi_gateway.MAX_BODY + 1), headers={"Authorization": "Bearer fixture-secret"}).status_code == 413


def test_gateway_forwards_only_fixed_backend_route(monkeypatch):
    monkeypatch.setattr(vapi_gateway, "dotenv_values", lambda _: {"VAPI_WEBHOOK_SECRET": "fixture-secret"})
    sent = []
    class Backend:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, url, **kwargs):
            sent.append((url, kwargs))
            return httpx.Response(403, json={"code": "UNKNOWN_ASSISTANT"})
    monkeypatch.setattr(vapi_gateway.httpx, "AsyncClient", Backend)
    with TestClient(vapi_gateway.app) as gateway:
        response = gateway.post("/api/v1/voice/vapi", json={"message": {}}, headers={"Authorization": "Bearer fixture-secret", "Origin": "https://untrusted.example"})
        assert response.status_code == 403
        assert sent[0][0] == "http://127.0.0.1:8000/api/v1/voice/vapi"
        assert "Origin" not in sent[0][1]["headers"]


def test_export_privacy_and_vertical_tool_boundary():
    config = assistant_config("https://webhook.example", "credential", "salon")
    assert config["artifactPlan"]["transcriptPlan"]["enabled"] is False
    assert not config["artifactPlan"]["recordingEnabled"]
    names = {t["function"]["name"] for t in config["model"]["tools"]}
    assert "check_order_capacity" not in names and "join_waitlist" not in names
    assert "prepare_action" in names and "commit_action" in names


def test_continuous_demo_hours_allow_booking_across_midnight():
    from datetime import datetime
    from types import SimpleNamespace
    start = datetime.fromisoformat("2026-10-12T23:30:00-04:00")
    loc = SimpleNamespace(timezone="America/New_York", settings={"open_hour": 0, "close_hour": 24})
    assert business_open(loc, start, start + timedelta(minutes=85))
    loc.settings["close_hour"] = 22
    assert not business_open(loc, start, start + timedelta(minutes=85))


def test_vapi_interruption_revokes_readback_confirmation(client, monkeypatch):
    monkeypatch.setattr(settings(), "vapi_webhook_secret", "fixture-webhook")
    monkeypatch.setattr(settings(), "vapi_restaurant_assistant_id", "fixture-assistant")
    headers = {"Authorization": "Bearer fixture-webhook"}
    base = {"id": "fixture-call", "assistantId": "fixture-assistant"}
    def event(id_, **body):
        return client.post("/api/v1/voice/vapi", headers=headers, json={"message": {"id": id_, "call": base, **body}})
    prepare = event("prepare", type="tool-calls", toolCallList=[{"id": "prepare-tool", "function": {"name": "prepare_action", "arguments": {"kind": "reservation", "name": "Guest", "party_size": 2, "start_at": (now() + timedelta(hours=4)).isoformat()}}}])
    import json
    proposal = json.loads(prepare.json()["results"][0]["result"])
    event("readback", type="transcript", transcriptType="final", role="assistant", transcript=proposal["readback"])
    event("yes", type="transcript", transcriptType="final", role="user", transcript="yes")
    event("interruption", type="user-interrupted")
    result = event("commit", type="tool-calls", toolCallList=[{"id": "commit-tool", "function": {"name": "commit_action", "arguments": {"action_id": proposal["action_id"]}}}])
    assert json.loads(result.json()["results"][0]["result"])["code"] == "CONFIRMATION_REQUIRED"


def test_readback_can_span_final_segments_without_retaining_speech(database):
    from app.db import transaction
    from app.models import Call, Location, PendingAction
    from app.domain import prepare, observe_transcript, commit
    from app.schemas import PrepareAction
    from app.seed import RESTAURANT
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        call = Call(tenant_id=loc.tenant_id, location_id=loc.id, provider="vapi", provider_call_id="segmented-test")
        db.add(call)
        db.flush()
        proposal = prepare(db, loc, PrepareAction(location_id=loc.id, kind="reservation", name="Guest", party_size=2, start_at=now() + timedelta(hours=4)), call.id)
        action = db.get(PendingAction, proposal["action_id"])
        text = proposal["readback"]
        parts = [text[:20], text[20:55], text[55:]]
        observe_transcript(db, call, "assistant", "Here is the proposal. " + parts[0])
        assert not action.readback_at
        observe_transcript(db, call, "assistant", parts[1])
        assert not action.readback_at
        observe_transcript(db, call, "assistant", parts[2])
        assert action.readback_at
        observe_transcript(db, call, "user", "yes")
        assert commit(db, loc, action)["status"] == "confirmed"
        assert call.transcript == []
        assert isinstance(action.payload["readback_progress"], int)


def test_incomplete_readback_and_user_correction_cannot_authorize(database):
    from app.db import transaction
    from app.models import Call, Location, PendingAction
    from app.domain import prepare, observe_transcript
    from app.schemas import PrepareAction
    from app.seed import RESTAURANT
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        call = Call(tenant_id=loc.tenant_id, location_id=loc.id, provider="vapi", provider_call_id="incomplete-test")
        db.add(call)
        db.flush()
        proposal = prepare(db, loc, PrepareAction(location_id=loc.id, kind="reservation", name="Guest", party_size=2, start_at=now() + timedelta(hours=4)), call.id)
        action = db.get(PendingAction, proposal["action_id"])
        cut = len(proposal["readback"]) // 2
        observe_transcript(db, call, "assistant", proposal["readback"][:cut])
        observe_transcript(db, call, "user", "yes")
        assert not action.confirmed_at and not action.readback_at
        assert action.payload["readback_progress"] == 0
        observe_transcript(db, call, "assistant", proposal["readback"][cut:])
        observe_transcript(db, call, "user", "yes")
        assert not action.confirmed_at


def test_operating_status_explains_stale_state_without_guessing(client, database):
    from app.db import transaction
    from app.models import Resource, KitchenState
    from sqlalchemy import select
    from app.seed import RESTAURANT
    with transaction(database) as db:
        for table in db.scalars(select(Resource).where(Resource.location_id == RESTAURANT)):
            table.verified_at = now() - timedelta(hours=2)
        kitchen = db.scalar(select(KitchenState).where(KitchenState.location_id == RESTAURANT))
        kitchen.verified_at = now() - timedelta(hours=2)
    call = client.post("/api/v1/voice/test-calls", json={"location_id": RESTAURANT}).json()
    result = client.post(f"/api/v1/voice/test-calls/{call['id']}/tools", json={"request_id": "status", "name": "get_operating_status", "arguments": {}}).json()
    assert result["floor"]["verification_required"]
    assert result["floor"]["verified_count"] == 0
    assert result["kitchen"]["status"] == "UNKNOWN"
    assert result["kitchen"]["kitchen_open"] is None
    assert result["known_information"]


def test_saved_assistant_has_dynamic_business_local_date_context():
    config = assistant_config('https://webhook.example', 'credential', 'restaurant', 'America/New_York')
    prompt = config['model']['messages'][0]['content']
    assert '{{"now" | date:' in prompt and 'America/New_York' in prompt
    assert 'Resolve tomorrow from this business-local date' in prompt
    assert 'That is right' in prompt and 'magic phrase' in prompt
    assert config['model']['temperature'] == 0.1
    assert all(t['async'] is False and t['messages'] == [{'type': 'request-start', 'content': ''}] for t in config['model']['tools'])
