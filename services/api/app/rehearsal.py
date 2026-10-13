"""Explicit operator preparation of local sample observations, never live POS state."""
from sqlalchemy import select
from .models import Resource, KitchenState, Location, now
from .seed import RESTAURANT
from .domain import DomainError, emit


def refresh_sample_observations(db):
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
