"""Read-only HTTP readiness checks against the two local services."""
import httpx

with httpx.Client(timeout=30) as client:
    health = client.get("http://127.0.0.1:8000/api/v1/health")
    health.raise_for_status()
    assert health.json()["database"]["connected"]
    print("API health: ok · database:", health.json()["database"]["provider"], flush=True)
    pages = {"restaurant": ["overview", "floor", "reservations", "waitlist", "kitchen", "orders", "menu", "calls", "attention", "voice", "customers", "integrations", "settings"], "salon": ["overview", "calendar", "staff", "services", "calls", "attention", "voice", "customers", "integrations", "settings"]}
    paths = ["/"] + [f"/app/{vertical}/{section}" for vertical, sections in pages.items() for section in sections]
    for path in paths:
        response = client.get("http://127.0.0.1:3000" + path)
        response.raise_for_status()
        assert "FrontDesk OS" in response.text
        print(f"Web {path}: HTTP {response.status_code}", flush=True)
    font = client.get("http://127.0.0.1:3000/fonts/inter-latin.woff2")
    font.raise_for_status()
    assert font.content[:4] == b"wOF2"
    login = client.post("http://127.0.0.1:3000/api/v1/auth/login", json={"email": "", "demo": True}, headers={"Origin": "http://127.0.0.1:3000"})
    login.raise_for_status()
    snapshot = client.get("http://127.0.0.1:3000/api/v1/locations/demo-restaurant/snapshot")
    snapshot.raise_for_status()
    assert snapshot.json()["location"]["name"] == "Juniper & Rye"
    for location in ["demo-restaurant", "demo-salon"]:
        for route in [f"/locations/{location}/snapshot", f"/attention?location_id={location}", f"/customers?location_id={location}", f"/integrations?location_id={location}", f"/voice/config?location_id={location}"]:
            result = client.get("http://127.0.0.1:3000/api/v1" + route)
            result.raise_for_status()
            assert result.headers.get("x-correlation-id")
    print("Next.js proxy: authenticated snapshots, attention, customers, integrations and voice config for both verticals: PASS", flush=True)
    with client.stream("GET", "http://127.0.0.1:3000/api/v1/realtime?location_id=demo-restaurant") as stream:
        stream.raise_for_status()
        assert "text/event-stream" in stream.headers["content-type"]
        assert next(line for line in stream.iter_lines() if line).startswith("event: state")
    print("Realtime: authenticated SSE state event received through Next.js: PASS", flush=True)
    try:
        response = client.get("http://127.0.0.1:11434/api/tags", timeout=2)
        response.raise_for_status()
        print("Ollama available models:", ", ".join(m["name"] for m in response.json().get("models", [])) or "none installed")
    except httpx.HTTPError:
        print("Ollama: not reachable (additional prototype remains gated)")
