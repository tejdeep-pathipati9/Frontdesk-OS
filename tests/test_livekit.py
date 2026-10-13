import jwt
from sqlalchemy import select
from app.config import settings
from app.models import Call, now
from app.seed import RESTAURANT


def configure(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "livekit_url", "ws://127.0.0.1:7880")
    monkeypatch.setattr(cfg, "livekit_api_key", "fixture-key")
    monkeypatch.setattr(cfg, "livekit_api_secret", "fixture-secret-for-token-validation-only")
    monkeypatch.setattr(cfg, "agent_api_token", "fixture-agent-token")
    return cfg


def test_token_dispatch_and_agent_authorization(client, database, monkeypatch):
    cfg = configure(monkeypatch)
    response = client.post("/api/v1/voice/livekit/session", json={"location_id": RESTAURANT})
    assert response.status_code == 200
    data = response.json()
    claims = jwt.decode(data["token"], cfg.livekit_api_secret, algorithms=["HS256"])
    assert claims["video"]["roomJoin"] is True
    assert claims["video"]["canPublishData"] is False
    assert claims["roomConfig"]["agents"][0]["agentName"] == "frontdesk-local"
    assert data["call_id"] in claims["roomConfig"]["agents"][0]["metadata"]
    route = f"/api/v1/voice/agent/calls/{data['call_id']}"
    assert client.get(route).status_code == 401
    headers = {"Authorization": "Bearer fixture-agent-token"}
    assert client.get(route, headers=headers).status_code == 200
    payload = {"request_id": "business-info", "name": "get_business_info", "arguments": {"location_id": "other-tenant"}}
    result = client.post(route + "/tools", json=payload, headers=headers).json()
    assert result["name"] == "Momentuum Bistro"
    transcript = {"event_id": "final1", "role": "user", "text": "Hello"}
    assert client.post(route + "/transcript", json=transcript, headers=headers).json()["status"] == "processed"
    assert client.post(route + "/transcript", json=transcript, headers=headers).json()["status"] == "already_processed"
    assert client.post(route + "/end", headers=headers).status_code == 200
    with database() as db:
        call = db.get(Call, data["call_id"])
        assert call.status == "ended"
        assert call.transcript == []


def test_missing_livekit_configuration_blocks_session(client, monkeypatch):
    monkeypatch.setattr(settings(), "livekit_url", "")
    assert client.post("/api/v1/voice/livekit/session", json={"location_id": RESTAURANT}).status_code == 503
