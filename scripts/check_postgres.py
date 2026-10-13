"""Verify the isolated Compose stack through its real HTTP API, including races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx
from pathlib import Path
from dotenv import dotenv_values

cfg = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
login = {"email": cfg.get("OWNER_EMAIL"), "password": cfg.get("OWNER_PASSWORD")}

BASE = "http://127.0.0.1:18000/api/v1"
with httpx.Client(timeout=30) as client:
    client.get(BASE + "/health").raise_for_status()
    client.post(BASE + "/auth/login", json=login).raise_for_status()
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

    # Provider records are independent of local projections. Exercise their
    # transaction locking and JSON normalization on the actual PostgreSQL stack.
    response = client.post(BASE + "/locations", json={"name": "OpenTable provider verification", "vertical": "restaurant", "existing_software": "mock_opentable"})
    response.raise_for_status()
    provider_loc = response.json()["id"]
    provider_when = (datetime.now(ZoneInfo("America/New_York")) + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0).isoformat()
    provider_actions = []
    for _ in range(2):
        response = client.post(BASE + "/actions/prepare", json={"location_id": provider_loc, "kind": "reservation", "name": "Provider race guest", "party_size": 12, "start_at": provider_when})
        response.raise_for_status()
        id_ = response.json()["action_id"]
        client.post(BASE + f"/actions/{id_}/confirm").raise_for_status()
        provider_actions.append(id_)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(commit, provider_actions)) == [200, 409]
    snapshot = client.get(BASE + f"/locations/{provider_loc}/snapshot").json()
    assert len(snapshot["reservations"]) == 2 and not snapshot["resources"]
    assert all(r["is_projection"] and not r["resource_ids"] for r in snapshot["reservations"])
    client.post(BASE + f"/integrations/mock_opentable/sync?location_id={provider_loc}").raise_for_status()
    assert len(client.get(BASE + f"/locations/{provider_loc}/snapshot").json()["reservations"]) == 2
    print("PostgreSQL OpenTable mock: large-party race, single provider commit, stable projections: PASS")

    response = client.post(BASE + "/locations", json={"name": "Square provider verification", "vertical": "restaurant", "existing_software": "mock_square"})
    response.raise_for_status()
    square_loc = response.json()["id"]
    item = next(i for i in client.get(BASE + f"/locations/{square_loc}/snapshot").json()["menu"] if i["name"] == "Seasonal salad")
    event = {"location_id": square_loc, "event_id": "square-stock-check", "external_id": "sq-salad", "kind": "menu", "observed_at": datetime.now(ZoneInfo("UTC")).isoformat(), "data": {"item_name": "Seasonal salad", "category": "Mains", "amount_minor": 1500, "in_stock": False}}
    client.post(BASE + "/integrations/mock_square/mock-events", json=event).raise_for_status()
    assert client.post(BASE + "/integrations/mock_square/mock-events", json=event).json()["status"] == "already_processed"
    assert client.patch(BASE + f"/menu/{item['id']}", json={"available": True}).json()["available"] is False
    assert client.post(BASE + "/orders/quote", json={"location_id": square_loc, "items": [{"item_id": item["id"]}]}).json()["code"] == "ITEM_UNAVAILABLE"
    print("PostgreSQL Square mock: normalized webhook, event deduplication, catalog stock guard: PASS")

with httpx.Client(timeout=30) as web:
    web.get("http://127.0.0.1:13000").raise_for_status()
    response = web.post("http://127.0.0.1:13000/api/v1/auth/login", json=login, headers={"Origin": "http://127.0.0.1:13000"})
    response.raise_for_status()
    web.get("http://127.0.0.1:13000/api/v1/locations").raise_for_status()
    print("Docker production dashboard → API → PostgreSQL proxy: PASS")
