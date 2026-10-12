"""Verify the isolated Compose stack through its real HTTP API, including races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx

BASE = "http://127.0.0.1:18000/api/v1"
with httpx.Client(timeout=30) as client:
    client.get(BASE + "/health").raise_for_status()
    client.post(BASE + "/auth/login", json={"email": "", "demo": True}).raise_for_status()
    response = client.post(BASE + "/locations", json={"name": "PostgreSQL verification", "vertical": "restaurant", "timezone": "America/New_York"})
    response.raise_for_status()
    loc = response.json()["id"]
    client.post(BASE + "/resources", json={"location_id": loc, "name": "Race table", "capacity": 4}).raise_for_status()
    when = (datetime.now(ZoneInfo("America/New_York")) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
    actions = []
    for _ in range(2):
        response = client.post(BASE + "/actions/prepare", json={"location_id": loc, "kind": "reservation", "name": "Verification guest", "party_size": 4, "start_at": when})
        response.raise_for_status()
        id_ = response.json()["action_id"]
        client.post(BASE + f"/actions/{id_}/confirm").raise_for_status()
        actions.append(id_)
    cookies = client.cookies
    def commit(id_):
        with httpx.Client(cookies=cookies, timeout=30) as concurrent:
            return concurrent.post(BASE + "/actions/commit", json={"action_id": id_}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(commit, actions))
    assert outcomes == [200, 409], outcomes
    rows = client.get(BASE + f"/reservations?location_id={loc}").json()
    assert len(rows) == 1
    print("PostgreSQL migrations, seed, auth, resource creation and concurrent booking: PASS")
    replay = next(id_ for id_ in actions if client.post(BASE + "/actions/commit", json={"action_id": id_}).status_code == 200)
    assert client.post(BASE + "/actions/commit", json={"action_id": replay}).json()["id"] == rows[0]["id"]
    print("PostgreSQL commit replay: one authoritative record: PASS")

with httpx.Client(timeout=30) as web:
    web.get("http://127.0.0.1:13000").raise_for_status()
    response = web.post("http://127.0.0.1:13000/api/v1/auth/login", json={"email": "", "demo": True}, headers={"Origin": "http://127.0.0.1:13000"})
    response.raise_for_status()
    web.get("http://127.0.0.1:13000/api/v1/locations").raise_for_status()
    print("Docker production dashboard → API → PostgreSQL proxy: PASS")
