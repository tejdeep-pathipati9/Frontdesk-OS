"""Explicit operator preparation of local sample observations, never live POS state.

Fit: used by scripts/prepare_rehearsal.py before a demo or voice rehearsal, because
table and kitchen verifications expire. Only the local internal demo restaurant can
be prepared; connected-provider data must be synced instead. Each action is audited
with actor "local_operator" and preserves staff holds, out-of-service tables and
intake pauses.
"""
from sqlalchemy import select
from .models import Resource, KitchenState, Location, now
from .seed import RESTAURANT
from .domain import DomainError, emit


def refresh_sample_observations(db):
    """Re-stamp local sample tables and kitchen state as freshly verified.

    Raises SAMPLE_PREPARATION_DISABLED unless the location is the internal demo restaurant. Held/out-of-service
    and provider-imported tables are skipped. Returns counts and whether intake is paused.
    """
    loc = db.get(Location, RESTAURANT)
    if not loc or loc.mode != "demo" or loc.settings.get("floor_provider") or loc.settings.get("booking_provider", "internal") != "internal":
        raise DomainError("SAMPLE_PREPARATION_DISABLED", "Sample preparation requires the local internal restaurant; connected provider data must be synchronized separately", 422)
    refreshed = []
    for table in db.scalars(select(Resource).where(Resource.location_id == loc.id, Resource.tenant_id == loc.tenant_id, Resource.kind == "table")):
        if table.state in ["held", "out_of_service"] or table.attributes.get("source_provider"):
            continue
        table.verified_at = now()
        table.attributes = {**table.attributes, "staff_verified": True, "verification_source": "local_sample_setup"}
        refreshed.append(table.name)
    kitchen = db.scalar(select(KitchenState).where(KitchenState.location_id == loc.id, KitchenState.tenant_id == loc.tenant_id))
    if kitchen:
        kitchen.verified_at = now()
    emit(db, loc, "operations.sample_observations_refreshed", {"tables": refreshed, "kitchen_verified": bool(kitchen), "states_and_pauses_preserved": True}, actor="local_operator")
    return {"tables_refreshed": len(refreshed), "kitchen_verified": bool(kitchen), "intake_paused": loc.settings.get("intake_paused", False)}


def configure_staff_managed_restaurant(db):
    """Refresh samples, make staff table state persistent and add the bistro menu.

    Side effects: sets settings.staff_state_persistent and emits an audit event. Returns refreshed counts plus menu_items_added.
    """
    # The operator explicitly chooses lasting staff controls for the local floor.
    # Imported tables retain provider freshness and conflicting POS checks.
    refreshed = refresh_sample_observations(db)
    loc = db.get(Location, RESTAURANT)
    loc.settings = {**loc.settings, "staff_state_persistent": True}
    from .bistro_menu import add_bistro_menu
    added = add_bistro_menu(db, loc)
    emit(db, loc, "operations.staff_state_configured", {"staff_state_persistent": True, "menu_items_added": added}, actor="local_operator")
    return {**refreshed, "menu_items_added": added, "staff_state_persistent": True}
