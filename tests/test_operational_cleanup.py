"""Operational data cleanup and attention-queue behavior."""
from datetime import timedelta
from sqlalchemy import select
import pytest
from app.db import transaction
from app.models import Location, Resource, KitchenState, MenuItem, Call, ToolExecution, Event, now
from app.seed import RESTAURANT, DEMO_TENANT, seed
from app.domain import table_needs_verification, quote, DomainError
from app.schemas import QuoteRequest, CartItem
from app.rehearsal import configure_staff_managed_restaurant
from app.bistro_menu import add_bistro_menu
from app.attention import group_call_followups


def test_lasting_staff_state_keeps_pauses_and_provider_checks(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        configure_staff_managed_restaurant(db)
        table = db.get(Resource, "table-1")
        table.verified_at = now() - timedelta(days=2)
        assert not table_needs_verification(loc, table)
        table.attributes = {**table.attributes, "pos_signal": "check_open", "signal_observed_at": now().isoformat()}
        assert table_needs_verification(loc, table)
        table.attributes = {"source_provider": "mock_toast", "staff_verified": True}
        assert table_needs_verification(loc, table)
        table.attributes = {"staff_verified": False}
        assert table_needs_verification(loc, table)
        kitchen = db.get(KitchenState, "demo-kitchen")
        kitchen.verified_at = now() - timedelta(days=2)
        request = QuoteRequest(location_id=loc.id, items=[CartItem(item_id="menu-salmon")])
        assert quote(db, loc, request)["total_minor"] > 0
        kitchen.accepting_takeout = False
        with pytest.raises(DomainError, match="not accepting"):
            quote(db, loc, request)


def test_default_freshness_policy_is_preserved(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        table = db.get(Resource, "table-1")
        table.verified_at = now() - timedelta(hours=2)
        assert table_needs_verification(loc, table)


def test_expanded_menu_preserves_owner_changes_and_is_orderable(database):
    with transaction(database) as db:
        loc = db.get(Location, RESTAURANT)
        item = db.get(MenuItem, "menu-salmon")
        item.available = False
        item.price_minor = 3000
        seed(db)
        seed(db)
        assert len(list(db.scalars(select(MenuItem).where(MenuItem.location_id == loc.id)))) == 17
        assert item.price_minor == 3000 and item.available is False
        request = QuoteRequest(location_id=loc.id, items=[CartItem(item_id="menu-steak", modifiers=["medium"])])
        assert quote(db, loc, request)["total_minor"] > 0
        loc.settings = {**loc.settings, "catalog_provider": "mock_toast"}
        assert add_bistro_menu(db, loc) == 0


def test_call_followups_group_without_merging_transaction_reviews():
    items = [{"id": "tool:1", "kind": "failed_action", "call_id": "c1", "created_at": "2026-10-13T12:00:00"},
             {"id": "tool:2", "kind": "failed_action", "call_id": "c1", "created_at": "2026-10-13T12:01:00"},
             {"id": "event:3", "kind": "staff_followup", "call_id": "c1", "created_at": "2026-10-13T12:02:00"},
             {"id": "action:4", "kind": "pending_approval", "call_id": "c1", "created_at": "2026-10-13T12:03:00"},
             {"id": "event:5", "kind": "integration_failure", "call_id": "c1", "created_at": "2026-10-13T12:04:00"}]
    result = group_call_followups(items)
    assert len(result) == 3
    group = next(i for i in result if i["kind"] == "staff_followup")
    assert set(group["related_ids"]) == {"tool:1", "tool:2", "event:3"}


def test_resolving_followup_reviews_all_related_failures(client, database):
    with transaction(database) as db:
        db.add(Call(id="followup-call", tenant_id=DEMO_TENANT, location_id=RESTAURANT,
                    provider="vapi", provider_call_id="followup-provider-call", status="ended"))
        db.flush()
        for index in range(3):
            db.add(ToolExecution(id=f"followup-tool-{index}", tenant_id=DEMO_TENANT, location_id=RESTAURANT,
                                 call_id="followup-call", request_id=f"followup-{index}", name="prepare_action",
                                 args={}, result={"code": "INVALID_ARGUMENTS", "message": "Missing date"}, status="failed"))
        db.add(Event(id="followup-event", tenant_id=DEMO_TENANT, location_id=RESTAURANT,
                     type="call.escalation_requested", payload={"call_id": "followup-call", "reason": "Booking assistance"}))
    queue = client.get(f"/api/v1/attention?location_id={RESTAURANT}").json()
    assert len(queue) == 1 and len(queue[0]["related_ids"]) == 4
    response = client.post(f"/api/v1/attention/{queue[0]['id']}/decision", json={"decision": "resolved"})
    assert response.status_code == 200
    assert client.get(f"/api/v1/attention?location_id={RESTAURANT}").json() == []
    with database() as db:
        assert len(list(db.scalars(select(ToolExecution).where(ToolExecution.call_id == "followup-call")))) == 3
