"""Read-only checks: public webhook isolation, auth and saved assistant mapping."""
from pathlib import Path
import argparse
import sys
from dotenv import dotenv_values
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/api'))
from app.voice_readiness import post_probe
parser = argparse.ArgumentParser()
parser.add_argument("--inspect-calls", action="store_true")
args = parser.parse_args()
cfg = dotenv_values(ROOT / ".env")
base = cfg.get("VAPI_SERVER_URL")
if not base or not cfg.get("VAPI_WEBHOOK_SECRET"):
    raise SystemExit("Run setup_vapi.py first.")
with httpx.Client(timeout=20) as client:
    assert post_probe(base, "", method="GET", path="/api/v1/locations").status_code == 404
    assert post_probe(base, "", path="/api/v1/auth/login", payload={"demo": True}).status_code == 404
    route = base + "/api/v1/voice/vapi"
    assert post_probe(base, "", payload={}).status_code == 401
    # Authenticate but use an unmapped assistant: reject before creating a call.
    result = post_probe(base, cfg["VAPI_WEBHOOK_SECRET"])
    assert result.status_code == 403, f"Authenticated boundary returned {result.status_code}"
    assert result.json()["code"] == "UNKNOWN_ASSISTANT"
    print("HTTPS tunnel: reachable; admin routes unavailable; missing credentials rejected; authenticated webhook reaches FastAPI.", flush=True)
    client.post("http://127.0.0.1:8000/api/v1/auth/login", json={"email": cfg.get("OWNER_EMAIL"), "password": cfg.get("OWNER_PASSWORD")}).raise_for_status()
    for vertical in ["restaurant", "salon"]:
        response = client.get(f"http://127.0.0.1:8000/api/v1/voice/config?location_id=demo-{vertical}")
        response.raise_for_status()
        vapi = response.json()["vapi"]
        assert vapi["configured"] and vapi["assistant_id"] == cfg[f"VAPI_{vertical.upper()}_ASSISTANT_ID"]
        print(f"{vertical.capitalize()}: dashboard Vapi configuration and saved assistant mapping verified.", flush=True)
print("No test call or booking created. Actual audio/webhook ordering still requires a web call.", flush=True)
if args.inspect_calls:
    with httpx.Client(base_url="https://api.vapi.ai", headers={"Authorization": f"Bearer {cfg['VAPI_PRIVATE_KEY']}"}, timeout=20) as provider:
        for vertical in ["restaurant", "salon"]:
            assistant = provider.get(f"/assistant/{cfg[f'VAPI_{vertical.upper()}_ASSISTANT_ID']}")
            assistant.raise_for_status()
            config = assistant.json()
            print(f"{vertical}: server subscriptions {config.get('serverMessages', [])}", flush=True)
        with httpx.Client(timeout=20) as local:
            local.post("http://127.0.0.1:8000/api/v1/auth/login", json={"email": cfg.get("OWNER_EMAIL"), "password": cfg.get("OWNER_PASSWORD")}).raise_for_status()
            response = local.get("http://127.0.0.1:8000/api/v1/calls?location_id=demo-restaurant&limit=10")
            response.raise_for_status()
            calls = response.json()["items"]
        for call in calls:
            if call["provider"] != "vapi":
                continue
            result = provider.get(f"/call/{call['provider_call_id']}")
            result.raise_for_status()
            data = result.json()
            print(f"Real Vapi call: status={data.get('status')} endedReason={data.get('endedReason') or 'none'}", flush=True)
