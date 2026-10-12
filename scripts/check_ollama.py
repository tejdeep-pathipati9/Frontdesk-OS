"""Exercise the real local model against an isolated demo database, without audio."""
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))
temporary = tempfile.TemporaryDirectory(prefix="frontdesk-ollama-")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(temporary.name) / "test.db")
os.environ["DEMO_MODE"] = "true"
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    client.post("/api/v1/auth/login", json={"email": "", "demo": True}).raise_for_status()
    session = client.post("/api/v1/voice/ollama/session", json={"location_id": "demo-restaurant"}).json()
    print("Local model:", session["model"], flush=True)
    response = client.post(f"/api/v1/voice/ollama/{session['call_id']}/chat", json={"text": "Use get_business_info to tell me the business name and timezone."})
    response.raise_for_status()
    result = response.json()
    assert any(t["name"] == "get_business_info" for t in result["tool_results"]), "Local model did not call the requested business tool"
    assert "Juniper" in result["text"], "Reply did not reflect authoritative business name"
    print("Real Ollama → shared business tool → response: PASS")
    print(result["text"])
    client.post(f"/api/v1/voice/ollama/{session['call_id']}/end").raise_for_status()
temporary.cleanup()
