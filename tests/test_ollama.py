import httpx
from datetime import timedelta
from app.models import now
from app.ollama import chats


def test_local_model_tool_loop_is_shared_and_labeled(client, monkeypatch):
    chats.clear()
    responses = iter([
        {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_business_info", "arguments": {}}}]}},
        {"message": {"role": "assistant", "content": "Juniper & Rye is in America/New_York."}},
    ])
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(200, json=next(responses), request=httpx.Request("POST", "http://localhost/api/chat")))
    session = client.post("/api/v1/voice/ollama/session", json={"location_id": "demo-restaurant"}).json()
    result = client.post(f"/api/v1/voice/ollama/{session['call_id']}/chat", json={"text": "What is the business name?"}).json()
    assert result["provider"] == "ollama_text"
    assert result["tool_results"][0]["result"]["name"] == "Juniper & Rye"
    client.post(f"/api/v1/voice/ollama/{session['call_id']}/end")
    assert session["call_id"] not in chats
    detail = client.get(f"/api/v1/calls/{session['call_id']}").json()
    assert detail["caller_masked"] == "Ollama text test · no audio"
    assert detail["transcript"] == []


def test_local_model_prepare_has_authoritative_readback(client, monkeypatch):
    chats.clear()
    def generate(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", "http://localhost/api/chat"), json={"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "prepare_action", "arguments": {"kind": "reservation", "name": "Guest", "party_size": 2, "start_at": (now() + timedelta(hours=4)).isoformat()}}}]}})
    monkeypatch.setattr(httpx, "post", generate)
    session = client.post("/api/v1/voice/ollama/session", json={"location_id": "demo-restaurant"}).json()
    result = client.post(f"/api/v1/voice/ollama/{session['call_id']}/chat", json={"text": "Book a table."}).json()
    assert result["text"] == result["tool_results"][0]["result"]["readback"]
    assert result["text"].startswith("Please confirm:")
