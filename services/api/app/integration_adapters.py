"""Provider-neutral contract and separate, persistent simulated provider adapters.

Mock capabilities describe fixtures, not vendor account permissions. ProviderRecord
is the simulated remote authority; local reservations are only its projections.

Fit: integration_service.py talks only to these adapters (never to ProviderRecord
directly for writes). MOCK_ADAPTERS are the activatable simulations; PROVIDERS also
lists the internal facade and the gated live adapters, which always refuse. Mock
`scenario` values (normal, failure, stale, unavailable, ambiguous) inject faults.
Write methods take an idempotency key so replays return the original result.
"""
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from zoneinfo import ZoneInfo
from uuid import uuid4
from sqlalchemy import select
from .models import ProviderRecord, now
from .domain import DomainError, utc

CAPABILITIES = ("configuration_read", "tables_read", "table_status_read", "pos_signals_read", "reservations_read", "availability_read", "bookings_write", "bookings_cancel", "bookings_modify", "orders_read", "orders_write", "menu_read")


@dataclass(frozen=True)
class ProviderContext:
    """Immutable per-call scope: tenant, location, timezone and mock fault scenario."""
    tenant_id: str
    location_id: str
    timezone: str
    scenario: str = "normal"


class RestaurantProvider(Protocol):
    """Structural contract every adapter implements (fetch, availability, booking and order writes)."""
    provider: str
    capabilities: dict[str, str]
    def fetch(self, db, context: ProviderContext) -> list[dict]: ...
    def availability(self, db, context: ProviderContext, request: dict, exclude: str | None = None) -> dict: ...
    def create_booking(self, db, context: ProviderContext, request: dict, idempotency_key: str) -> dict: ...
    def cancel_booking(self, db, context: ProviderContext, reference: str) -> dict: ...
    def modify_booking(self, db, context: ProviderContext, reference: str, request: dict) -> dict: ...
    def create_order(self, db, context: ProviderContext, quote: dict, idempotency_key: str) -> dict: ...


class MockProvider:
    """Base for simulated vendors; persists a separate system of record in ProviderRecord.

    Subclasses declare `supported` capabilities and implement seed()/availability(); unsupported
    capabilities raise UNSUPPORTED_CAPABILITY via require().
    """
    provider = "mock"
    supported: tuple[str, ...] = ()
    @property
    def capabilities(self):
        return {key: "supported" if key in self.supported else "unsupported" for key in CAPABILITIES}
    def require(self, capability):
        if capability not in self.supported:
            raise DomainError("UNSUPPORTED_CAPABILITY", f"{self.provider} does not support {capability}", 422)
    def rows(self, db, context, kind=None):
        """Provider-scoped ProviderRecord rows for this location, optionally filtered by kind."""
        query = select(ProviderRecord).where(ProviderRecord.location_id == context.location_id, ProviderRecord.tenant_id == context.tenant_id, ProviderRecord.provider == self.provider)
        if kind:
            query = query.where(ProviderRecord.kind == kind)
        return list(db.scalars(query))
    def add(self, db, ctx, kind, external_id, data):
        """Insert and flush a new simulated remote record."""
        row = ProviderRecord(tenant_id=ctx.tenant_id, location_id=ctx.location_id, provider=self.provider, kind=kind, external_id=external_id, data=data)
        db.add(row)
        db.flush()
        return row
    def seed(self, db, ctx):
        raise NotImplementedError
    def fetch(self, db, ctx):
        """Return the provider's records as a sync bundle, seeding fixtures on first use. Scenario 'failure' raises PROVIDER_UNREACHABLE; 'stale' backdates observed_at by four hours. Seating units stay internal to the provider."""
        if ctx.scenario == "failure":
            raise DomainError("PROVIDER_UNREACHABLE", "Simulated provider is unavailable", 503)
        if not self.rows(db, ctx):
            self.seed(db, ctx)
        observed = now() - timedelta(hours=4) if ctx.scenario == "stale" else now()
        return [{"kind": r.kind, "external_id": r.external_id, "data": r.data, "observed_at": observed} for r in self.rows(db, ctx) if r.kind != "seating_unit"]
    def availability(self, db, ctx, request, exclude=None):
        self.require("availability_read")
        raise NotImplementedError
    def create_booking(self, db, ctx, request, idempotency_key):
        self.require("bookings_write")
        raise NotImplementedError
    def cancel_booking(self, db, ctx, reference):
        """Mark the simulated booking cancelled; PROVIDER_RECORD_NOT_FOUND if missing."""
        self.require("bookings_cancel")
        row = next((r for r in self.rows(db, ctx, "reservation") if r.external_id == reference), None)
        if not row:
            raise DomainError("PROVIDER_RECORD_NOT_FOUND", "Provider booking is missing", 404)
        row.data = {**row.data, "status": "cancelled"}
        return {"status": "cancelled", "external_id": reference}
    def modify_booking(self, db, ctx, reference, request):
        """Move a booking if the provider still has availability (excluding itself); else PROVIDER_CONFLICT."""
        self.require("bookings_modify")
        decision = self.availability(db, ctx, request, exclude=reference)
        if decision["status"] != "AVAILABLE":
            raise DomainError("PROVIDER_CONFLICT", "Provider cannot move this booking")
        row = next((r for r in self.rows(db, ctx, "reservation") if r.external_id == reference), None)
        if not row:
            raise DomainError("PROVIDER_RECORD_NOT_FOUND", "Provider booking is missing", 404)
        row.data = {**row.data, **decision, "status": "confirmed"}
        return {"status": "confirmed", "external_id": reference, **decision}
    def create_order(self, db, ctx, quote, idempotency_key):
        """Create an order idempotently keyed by idempotency_key; scenario 'ambiguous' reports pending_confirmation."""
        self.require("orders_write")
        # Idempotency: replayed order key returns the original order.
        prior = next((r for r in self.rows(db, ctx, "order") if r.data.get("idempotency_key") == idempotency_key), None)
        if prior:
            return {"status": prior.data["state"], "external_id": prior.external_id}
        if ctx.scenario in ["unavailable", "failure", "stale"]:
            raise DomainError("PROVIDER_UNAVAILABLE", "Provider cannot accept orders", 503)
        row = self.add(db, ctx, "order", "SQ-" + str(uuid4())[:8], {**quote, "idempotency_key": idempotency_key, "state": "accepted"})
        return {"status": "pending_confirmation" if ctx.scenario == "ambiguous" else "accepted", "external_id": row.external_id}


class MockOpenTableAdapter(MockProvider):
    """Simulated booking system with verified seating arrangements (no POS or menu)."""
    provider = "mock_opentable"
    supported = ("configuration_read", "reservations_read", "availability_read", "bookings_write", "bookings_cancel", "bookings_modify")
    def seed(self, db, ctx):
        self.add(db, ctx, "configuration", "restaurant", {"open_hour": 17, "close_hour": 23, "booking_duration_min": 90})
        for id_, name, minimum, maximum in [("window-2", "Window table", 1, 2), ("booth-6", "Dining booth", 3, 6), ("private-12", "Verified private dining arrangement", 7, 12)]:
            self.add(db, ctx, "seating_unit", id_, {"name": name, "min_party": minimum, "max_party": maximum, "verified_arrangement": True})
        start = (now().astimezone(ZoneInfo(ctx.timezone)) + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
        self.add(db, ctx, "reservation", "OT-existing-12", {"customer_name": "Sample group", "party_size": 12, "start_at": utc(start).isoformat(), "end_at": utc(start + timedelta(minutes=90)).isoformat(), "unit_id": "private-12", "resources": ["Verified private dining arrangement"], "status": "confirmed"})
    def availability(self, db, ctx, request, exclude=None):
        self.require("availability_read")
        start = utc(request["start_at"])
        if start < now() - timedelta(seconds=30) or start > now() + timedelta(days=90):
            return {"status": "NO_CAPACITY", "reason_codes": ["INVALID_PROVIDER_BOOKING_TIME"]}
        end = start + timedelta(minutes=90)
        if ctx.scenario == "failure":
            raise DomainError("PROVIDER_UNREACHABLE", "Booking provider unavailable", 503)
        if ctx.scenario == "unavailable":
            return {"status": "NO_CAPACITY", "reason_codes": ["PROVIDER_SERVICE_UNAVAILABLE"]}
        if ctx.scenario == "stale":
            return {"status": "UNKNOWN", "reason_codes": ["STALE_PROVIDER_DATA"]}
        local_start, local_end = start.astimezone(ZoneInfo(ctx.timezone)), end.astimezone(ZoneInfo(ctx.timezone))
        if local_start.hour < 17 or local_end.date() != local_start.date() or local_end.hour > 23 or local_end.hour == 23 and local_end.minute:
            return {"status": "NO_CAPACITY", "reason_codes": ["PROVIDER_OUTSIDE_HOURS"]}
        for unit in self.rows(db, ctx, "seating_unit"):
            if not unit.data["min_party"] <= request["party_size"] <= unit.data["max_party"] or not unit.data["verified_arrangement"]:
                continue
            # A unit is busy if another live booking overlaps [start, end) on the same unit.
            busy = any(r.external_id != exclude and r.data["status"] in ["confirmed", "pending_confirmation"] and r.data["unit_id"] == unit.external_id and utc(r.data["start_at"]) < end and utc(r.data["end_at"]) > start for r in self.rows(db, ctx, "reservation"))
            if not busy:
                return {"status": "AVAILABLE", "start_at": start.isoformat(), "end_at": end.isoformat(), "unit_id": unit.external_id, "resources": [unit.data["name"]], "resource_ids": [], "party_size": request["party_size"], "reason_codes": []}
        return {"status": "NO_CAPACITY", "reason_codes": ["PROVIDER_NO_VERIFIED_ARRANGEMENT"]}
    def create_booking(self, db, ctx, request, idempotency_key):
        # Idempotency: a replay with the same key returns the existing booking instead of creating another.
        prior = next((r for r in self.rows(db, ctx, "reservation") if r.data.get("idempotency_key") == idempotency_key), None)
        if prior:
            return {**prior.data, "external_id": prior.external_id, "status": "confirmed"}
        decision = self.availability(db, ctx, request)
        if decision["status"] != "AVAILABLE":
            raise DomainError("PROVIDER_CONFLICT", "Provider slot is no longer available")
        row = self.add(db, ctx, "reservation", "OT-" + str(uuid4())[:8], {**request, **decision, "status": "confirmed", "idempotency_key": idempotency_key})
        return {**decision, "external_id": row.external_id, "status": "pending_confirmation" if ctx.scenario == "ambiguous" else "confirmed"}


class MockToastAdapter(MockProvider):
    """Simulated POS: tables, POS signals (hints only, never availability), menu and read-only orders."""
    provider = "mock_toast"
    supported = ("configuration_read", "tables_read", "pos_signals_read", "orders_read", "menu_read")
    def seed(self, db, ctx):
        self.add(db, ctx, "configuration", "restaurant", {"open_hour": 11, "close_hour": 23})
        for id_, name, capacity in [("patio-1", "Patio 1", 4), ("dining-7", "Dining 7", 6), ("window-2", "Window 2", 2)]:
            self.add(db, ctx, "table", id_, {"name": name, "capacity": capacity, "section": "Patio" if id_.startswith("patio") else "Dining room"})
        self.add(db, ctx, "pos_signal", "check-401", {"table_external_id": "dining-7", "signal": "check_open", "occupancy_hint": "possibly_occupied", "availability_verified": False})
        self.add(db, ctx, "pos_signal", "check-402", {"table_external_id": "window-2", "signal": "bill_paid", "occupancy_hint": "unknown", "availability_verified": False})
        self.add(db, ctx, "menu", "toast-burger", {"name": "House burger", "category": "Mains", "price_minor": 1800, "work_units": 3, "available": True, "modifiers": [{"id": "toast-cheese", "name": "Add cheese", "price_minor": 200}]})
        self.add(db, ctx, "order", "TOAST-1042", {"customer_name": "Sample takeout guest", "items": [{"name": "House burger", "quantity": 2, "modifiers": []}], "total_minor": 3600, "state": "preparing", "work_units": 6, "bucket_at": now().isoformat(), "pickup_min": (now() + timedelta(minutes=20)).isoformat(), "pickup_max": (now() + timedelta(minutes=30)).isoformat()})


class MockSquareAdapter(MockProvider):
    """Simulated ordering vendor: menu and order read/write, no tables or bookings."""
    provider = "mock_square"
    supported = ("configuration_read", "menu_read", "orders_read", "orders_write")
    def seed(self, db, ctx):
        self.add(db, ctx, "configuration", "restaurant", {"open_hour": 9, "close_hour": 22})
        for id_, name, category, price, available in [("sq-salad", "Seasonal salad", "Mains", 1400, True), ("sq-soup", "Soup of the day", "Mains", 850, False), ("sq-water", "Bottled water", "Drinks", 300, True)]:
            self.add(db, ctx, "menu", id_, {"name": name, "category": category, "price_minor": price, "work_units": 1, "available": available, "modifiers": []})
        self.add(db, ctx, "order", "SQUARE-84", {"customer_name": "Sample pickup guest", "items": [{"name": "Seasonal salad", "quantity": 1, "modifiers": []}], "total_minor": 1400, "state": "ready", "work_units": 1, "bucket_at": now().isoformat(), "pickup_min": now().isoformat(), "pickup_max": (now() + timedelta(minutes=10)).isoformat()})


MOCK_ADAPTERS = {adapter.provider: adapter for adapter in [MockOpenTableAdapter(), MockToastAdapter(), MockSquareAdapter()]}


class InternalRestaurantAdapter:
    """Facade around existing logic; commits require a server-held action."""
    provider = "internal"
    capabilities = {key: "unsupported" if key == "pos_signals_read" else "supported" for key in CAPABILITIES}
    def location(self, db, ctx):
        """Load the Location and enforce tenant ownership (NOT_FOUND otherwise)."""
        from .models import Location
        loc = db.get(Location, ctx.location_id)
        if not loc or loc.tenant_id != ctx.tenant_id:
            raise DomainError("NOT_FOUND", "Location not found", 404)
        return loc
    def availability(self, db, ctx, request, exclude=None):
        from .domain import availability
        from .schemas import Availability
        return availability(db, self.location(db, ctx), Availability.model_validate({**request, "location_id": ctx.location_id}), exclude=exclude, force_internal=True)
    def create_booking(self, db, ctx, request, idempotency_key):
        from .models import PendingAction
        from .domain import commit
        action = db.get(PendingAction, idempotency_key)
        if not action or action.tenant_id != ctx.tenant_id or action.location_id != ctx.location_id:
            raise DomainError("CONFIRMATION_REQUIRED", "Prepare and confirm a server-held action first")
        return commit(db, self.location(db, ctx), action)
    def create_order(self, db, ctx, quote, idempotency_key):
        return self.create_booking(db, ctx, quote, idempotency_key)
    def cancel_booking(self, db, ctx, reference):
        return self.create_booking(db, ctx, {}, reference)
    def modify_booking(self, db, ctx, reference, request):
        return self.create_booking(db, ctx, request, reference)
    def fetch(self, db, ctx):
        from .models import Resource, Reservation, Order, MenuItem
        from .serialization import serialize
        self.location(db, ctx)
        return [{"kind": kind, "external_id": row.id, "data": serialize(row), "observed_at": now()} for kind, model in [("table", Resource), ("reservation", Reservation), ("order", Order), ("menu", MenuItem)] for row in db.scalars(select(model).where(model.location_id == ctx.location_id, model.tenant_id == ctx.tenant_id)) if (not hasattr(row, "provider") or row.provider == "internal") and (not isinstance(row, Resource) or row.kind == "table" and not row.attributes.get("source_provider"))]


class GatedLiveAdapter:
    """Explicit access boundary; never falls back to a mock for a live account.

The existing Square/Toast HTTP clients remain intact. Activation additionally
requires account-scoped permission probing and vendor response normalization.
"""
    def __init__(self, provider, possible):
        self.provider = provider
        self.capabilities = {k: "requires_permission" if k in possible else "unsupported" for k in CAPABILITIES}
    def unavailable(self, capability):
        code = "UNSUPPORTED_CAPABILITY" if self.capabilities[capability] == "unsupported" else "PROVIDER_ACCESS_REQUIRED"
        raise DomainError(code, "Authorize vendor API access and normalization before activation", 503)
    def fetch(self, db, ctx): return self.unavailable("configuration_read")
    def availability(self, db, ctx, request, exclude=None): return self.unavailable("availability_read")
    def create_booking(self, db, ctx, request, idempotency_key): return self.unavailable("bookings_write")
    def cancel_booking(self, db, ctx, reference): return self.unavailable("bookings_cancel")
    def modify_booking(self, db, ctx, reference, request): return self.unavailable("bookings_modify")
    def create_order(self, db, ctx, quote, idempotency_key): return self.unavailable("orders_write")


PROVIDERS = {**MOCK_ADAPTERS, "internal": InternalRestaurantAdapter(), "opentable": GatedLiveAdapter("opentable", ("configuration_read", "reservations_read", "availability_read", "bookings_write", "bookings_cancel", "bookings_modify")), "toast": GatedLiveAdapter("toast", ("configuration_read", "tables_read", "pos_signals_read", "orders_read", "orders_write", "menu_read")), "square": GatedLiveAdapter("square", ("configuration_read", "orders_read", "orders_write", "menu_read"))}
