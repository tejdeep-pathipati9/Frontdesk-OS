"""Capabilities, import/sync, source selection and authoritative provider commits.

Only mock adapters perform writes in this prototype. Live vendor access remains
permission-gated; the existing real HTTP clients are retained in providers.py.
No simulated result can activate a live connection. Mock writes use the same
database transaction lock as internal writes, but a separate system of record.
"""
from datetime import timedelta
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select, or_, and_
from .models import IntegrationConnection, OperationalProjection, ProviderRecord, Resource, MenuItem, Reservation, Order, Customer, PendingAction, Call, Event, AuditEvent, Allocation, now
from .domain import DomainError, utc, emit
from .integration_adapters import MOCK_ADAPTERS, CAPABILITIES, ProviderContext

LIVE_CAPABILITIES = {
    "opentable": {"configuration_read", "reservations_read", "availability_read", "bookings_write", "bookings_cancel", "bookings_modify"},
    "toast": {"configuration_read", "tables_read", "pos_signals_read", "orders_read", "orders_write", "menu_read"},
    "square": {"configuration_read", "orders_read", "orders_write", "menu_read"},
}


def stable_id(loc, provider, kind, external_id):
    return str(uuid5(NAMESPACE_URL, f"frontdesk:{loc.tenant_id}:{loc.id}:{provider}:{kind}:{external_id}"))


def connection(db, loc, provider):
    return db.scalar(select(IntegrationConnection).where(IntegrationConnection.tenant_id == loc.tenant_id, IntegrationConnection.location_id == loc.id, IntegrationConnection.provider == provider))


def context(loc, conn):
    return ProviderContext(loc.tenant_id, loc.id, loc.timezone, conn.config.get("scenario", "normal"))


def require_adapter(db, loc, provider, capability=None, require_fresh=True):
    conn = connection(db, loc, provider)
    if provider not in MOCK_ADAPTERS or not conn or conn.mode != "mock" or conn.status == "disconnected":
        raise DomainError("PROVIDER_WRITE_GATED", "Provider credentials/permissions and normalized integration are not activated", 503)
    if capability and conn.capabilities.get(capability) != "supported":
        raise DomainError("UNSUPPORTED_CAPABILITY", f"{provider} cannot perform {capability}", 422)
    if require_fresh and (conn.status != "healthy" or not conn.last_sync_at or now() - utc(conn.last_sync_at) > timedelta(minutes=loc.settings.get("freshness_minutes", 30))):
        raise DomainError("STALE_PROVIDER_DATA", "Synchronize the selected provider before making a commitment", 503)
    return MOCK_ADAPTERS[provider], conn


def choose_authority(db, loc, key, provider):
    previous = loc.settings.get(key, "internal")
    if previous == provider:
        return
    model = Order if key == "order_provider" else Reservation
    rows = list(db.scalars(select(model).where(model.location_id == loc.id, model.tenant_id == loc.tenant_id)))
    # Read-only POS imports are not competing FrontDesk commitments. Existing
    # bookings, however, must remain with their source even when imported.
    active = any((r.state in ["accepted", "preparing", "ready", "pending_confirmation"] and (r.provider == previous or not r.idempotency_key.startswith("import:")) if model is Order else r.status in ["confirmed", "seated", "pending_confirmation"] and utc(r.end_at) > now()) and r.provider != ("internal" if provider == "staff_requests" else provider) for r in rows)
    pending = db.scalar(select(PendingAction.id).where(PendingAction.location_id == loc.id, PendingAction.tenant_id == loc.tenant_id, or_(PendingAction.state == "pending_confirmation", and_(PendingAction.state == "awaiting_confirmation", PendingAction.expires_at > now()))))
    if active or pending:
        raise DomainError("PROVIDER_SWITCH_CONFLICT", "Resolve active commitments and proposals before changing the authoritative provider")
    loc.settings = {**loc.settings, key: provider}
    emit(db, loc, "integration.authority_changed", {"kind": key, "before": previous, "after": provider})


def connect(db, loc, provider, booking_authority=None, order_authority=None, scenario="normal"):
    if loc.vertical != "restaurant":
        raise DomainError("WRONG_VERTICAL", "Restaurant integration onboarding applies to restaurants", 422)
    if provider not in MOCK_ADAPTERS:
        raise DomainError("PROVIDER_ACCESS_REQUIRED", "Live account activation requires authorized vendor access; use the explicitly labeled mock for experiments", 503)
    adapter = MOCK_ADAPTERS[provider]
    conn = connection(db, loc, provider)
    if not conn:
        conn = IntegrationConnection(tenant_id=loc.tenant_id, location_id=loc.id, provider=provider, capabilities=adapter.capabilities, config={"scenario": scenario}, mode="mock")
        db.add(conn)
        db.flush()
    conn.config = {"scenario": scenario}
    conn.capabilities = adapter.capabilities
    if conn.status == "disconnected":
        conn.status = "not_synced"
    if not loc.settings.get("hours_provider"):
        loc.settings = {**loc.settings, "hours_provider": provider}
    if booking_authority:
        if booking_authority not in ["internal", "staff_requests", provider] or booking_authority == provider and adapter.capabilities["bookings_write"] != "supported":
            raise DomainError("UNSUPPORTED_CAPABILITY", "This provider cannot be the restaurant booking authority", 422)
        choose_authority(db, loc, "booking_provider", booking_authority)
    elif adapter.capabilities["bookings_write"] == "supported":
        choose_authority(db, loc, "booking_provider", provider)
    if order_authority:
        if order_authority not in ["internal", provider] or order_authority == provider and adapter.capabilities["orders_write"] != "supported":
            raise DomainError("UNSUPPORTED_CAPABILITY", "This provider cannot accept order writes", 422)
        choose_authority(db, loc, "order_provider", order_authority)
    if adapter.capabilities["tables_read"] == "supported":
        if loc.settings.get("floor_provider") != provider and db.scalar(select(Allocation.id).join(Reservation, Reservation.id == Allocation.reservation_id).where(Reservation.location_id == loc.id, Reservation.status.in_(["confirmed", "seated"]), Reservation.end_at > now())):
            raise DomainError("FLOOR_IMPORT_CONFLICT", "Resolve active internal table assignments before replacing the floor source")
        loc.settings = {**loc.settings, "floor_provider": provider}
    if adapter.capabilities["menu_read"] == "supported":
        if loc.settings.get("order_provider", "internal") not in ["internal", provider]:
            raise DomainError("CATALOG_PROVIDER_CONFLICT", "The menu must come from the configured order provider")
        loc.settings = {**loc.settings, "catalog_provider": provider}
    # Onboarding uses explicit mock data and never converts a real location to demo.
    if not loc.mode == "demo":
        raise DomainError("MOCK_REQUIRES_DEMO", "Mock providers require an explicitly labeled demo location", 422)
    sync(db, loc, conn)
    return describe(db, loc, conn)


def sync(db, loc, conn):
    adapter, _ = require_adapter(db, loc, conn.provider, require_fresh=False)
    conn.last_attempt_at = now()
    try:
        bundle = adapter.fetch(db, context(loc, conn))
    except DomainError as error:
        conn.status, conn.error_code = "degraded", error.code
        emit(db, loc, "integration.sync_failed", {"provider": conn.provider, "error_code": error.code})
        return {"status": "degraded", "error_code": error.code}
    observed = min((r["observed_at"] for r in bundle), default=now())
    conn.last_sync_at, conn.error_code = observed, None
    conn.status = "stale" if now() - utc(observed) > timedelta(minutes=loc.settings.get("freshness_minutes", 30)) else "healthy"
    for record in sorted(bundle, key=lambda r: {"table": 0, "pos_signal": 2}.get(r["kind"], 1)):
        apply_record(db, loc, conn, record)
    reconcile(db, loc, conn)
    if conn.status == "healthy":
        resolved = set(db.scalars(select(AuditEvent.target_id).where(AuditEvent.tenant_id == loc.tenant_id, AuditEvent.location_id == loc.id, AuditEvent.action == "attention.reviewed")))
        for event in db.scalars(select(Event).where(Event.location_id == loc.id, Event.tenant_id == loc.tenant_id, Event.type == "integration.sync_failed")):
            target = f"event:{event.id}"
            if event.payload.get("provider") == conn.provider and target not in resolved:
                emit(db, loc, "attention.reviewed", {"decision": "sync_recovered", "target": target}, target=target)
    emit(db, loc, "integration.synced", {"provider": conn.provider, "mode": "mock", "status": conn.status, "records": len(bundle)})
    return {"status": conn.status, "records": len(bundle)}


def apply_record(db, loc, conn, record):
    kind, external_id, data = record["kind"], record["external_id"], dict(record["data"])
    row = db.scalar(select(OperationalProjection).where(OperationalProjection.tenant_id == loc.tenant_id, OperationalProjection.location_id == loc.id, OperationalProjection.provider == conn.provider, OperationalProjection.kind == kind, OperationalProjection.external_id == external_id))
    if not row:
        row = OperationalProjection(tenant_id=loc.tenant_id, location_id=loc.id, provider=conn.provider, kind=kind, external_id=external_id, data={}, observed_at=record["observed_at"])
        db.add(row)
    staff_disabled = row.data.get("staff_disabled", False)
    row.data, row.observed_at, row.received_at = data, record["observed_at"], now()
    if kind == "configuration" and loc.settings.get("hours_provider") == conn.provider and not loc.settings.get("hours_override"):
        loc.settings = {**loc.settings, "open_hour": data["open_hour"], "close_hour": data["close_hour"]}
    elif kind == "table":
        id_ = stable_id(loc, conn.provider, kind, external_id)
        table = db.get(Resource, id_)
        if not table:
            table = Resource(id=id_, tenant_id=loc.tenant_id, location_id=loc.id, kind="table", name=data["name"], capacity=data["capacity"], state="available", verified_at=now() - timedelta(days=365), attributes={"staff_verified": False})
            db.add(table)
        # Configuration import and POS sync never verify physical availability.
        table.name, table.capacity = data["name"], data["capacity"]
        table.attributes = {**table.attributes, "section": data.get("section"), "source_provider": conn.provider, "external_id": external_id, "configuration_observed_at": utc(record["observed_at"]).isoformat()}
    elif kind == "pos_signal":
        table = db.get(Resource, stable_id(loc, conn.provider, "table", data["table_external_id"]))
        if table:
            table.attributes = {**table.attributes, "pos_signal": data["signal"], "signal_observed_at": utc(record["observed_at"]).isoformat(), "signal_is_availability": False}
    elif kind == "menu":
        id_ = stable_id(loc, conn.provider, kind, external_id)
        item = db.get(MenuItem, id_)
        if not item:
            item = MenuItem(id=id_, tenant_id=loc.tenant_id, location_id=loc.id, name=data["name"], category=data["category"], price_minor=data["price_minor"])
            db.add(item)
        item.name, item.category, item.price_minor = data["name"], data["category"], data["price_minor"]
        item.available = data["available"] and not staff_disabled and conn.config.get("scenario") != "unavailable"
        item.modifiers, item.work_units = data["modifiers"], data.get("work_units", 1)
        row.data = {**data, "local_id": id_, "staff_disabled": staff_disabled}
    elif kind in ["reservation", "order"]:
        model = Reservation if kind == "reservation" else Order
        mirror = db.scalar(select(model).where(model.tenant_id == loc.tenant_id, model.location_id == loc.id, model.provider == conn.provider, model.external_id == external_id))
        if not mirror:
            customer = Customer(tenant_id=loc.tenant_id, display_name=data.get("customer_name") or data.get("name") or "Provider guest")
            db.add(customer)
            db.flush()
            args = {"tenant_id": loc.tenant_id, "location_id": loc.id, "customer_id": customer.id, "provider": conn.provider, "external_id": external_id, "idempotency_key": f"import:{conn.provider}:{external_id}"}
            if kind == "reservation":
                mirror = Reservation(**args, kind="reservation", start_at=utc(data["start_at"]), end_at=utc(data["end_at"]), party_size=data["party_size"])
            else:
                mirror = Order(**args, items=data["items"], total_minor=data["total_minor"], work_units=data.get("work_units", 0), bucket_at=utc(data["bucket_at"]), pickup_min=utc(data["pickup_min"]), pickup_max=utc(data["pickup_max"]))
            db.add(mirror)
            db.flush()
        if kind == "reservation":
            mirror.start_at, mirror.end_at, mirror.party_size, mirror.status = utc(data["start_at"]), utc(data["end_at"]), data["party_size"], data["status"]
        else:
            mirror.state, mirror.items, mirror.total_minor = data["state"], data["items"], data["total_minor"]
            mirror.bucket_at, mirror.pickup_min, mirror.pickup_max = utc(data["bucket_at"]), utc(data["pickup_min"]), utc(data["pickup_max"])
            mirror.work_units = data.get("work_units", mirror.work_units)
        if data.get("customer_name") or data.get("name"):
            db.get(Customer, mirror.customer_id).display_name = data.get("customer_name") or data["name"]
        row.data = {**data, "local_id": mirror.id}
    db.flush()


def catalog_ids(db, loc):
    provider = loc.settings.get("catalog_provider")
    if not provider:
        imported = {r.data.get("local_id") for r in db.scalars(select(OperationalProjection).where(OperationalProjection.location_id == loc.id, OperationalProjection.tenant_id == loc.tenant_id, OperationalProjection.kind == "menu"))}
        return {id_ for id_ in db.scalars(select(MenuItem.id).where(MenuItem.location_id == loc.id, MenuItem.tenant_id == loc.tenant_id)) if id_ not in imported}
    return {r.data.get("local_id") for r in db.scalars(select(OperationalProjection).where(OperationalProjection.location_id == loc.id, OperationalProjection.tenant_id == loc.tenant_id, OperationalProjection.provider == provider, OperationalProjection.kind == "menu"))}


def provider_availability(db, loc, query, exclude=None):
    provider = loc.settings.get("booking_provider", "internal")
    evidence = {"sources": [provider], "source_provider": provider, "as_of": now().isoformat(), "mode": "mock" if provider.startswith("mock_") else "live", "confidence": "low"}
    if provider == "staff_requests":
        return {"status": "UNKNOWN", "reason_codes": ["STAFF_APPROVAL_REQUIRED"], "alternatives": [], "evidence": evidence, "permitted_actions": ["request_staff_booking"]}
    from .domain import resources
    blocked = any(r.state == "out_of_service" for r in resources(db, loc))
    if blocked:
        return {"status": "UNKNOWN", "reason_codes": ["STAFF_OVERRIDE_REQUIRES_PROVIDER_REVIEW"], "alternatives": [], "evidence": evidence, "permitted_actions": ["request_staff_booking"]}
    try:
        adapter, conn = require_adapter(db, loc, provider, "availability_read")
        request = query.model_dump(mode="json")
        row = db.get(Reservation, exclude) if exclude else None
        result = adapter.availability(db, context(loc, conn), request, row.external_id if row and row.provider == provider else None)
        evidence.update(last_sync_at=utc(conn.last_sync_at).isoformat(), confidence="medium", limitations=["Provider availability does not prove current physical occupancy"])
        alternatives = []
        if result["status"] == "NO_CAPACITY":
            for minutes in range(30, 181, 30):
                alternative = adapter.availability(db, context(loc, conn), {**request, "start_at": (utc(query.start_at) + timedelta(minutes=minutes)).isoformat()})
                if alternative["status"] == "AVAILABLE":
                    alternatives.append({k: alternative[k] for k in ["start_at", "end_at", "resources"]})
                if len(alternatives) == 3:
                    break
        return {**result, "provider": provider, "alternatives": alternatives, "evidence": evidence, "permitted_actions": ["prepare_action"] if result["status"] == "AVAILABLE" else ["offer_alternative", "request_staff_booking"]}
    except DomainError as error:
        return {"status": "UNKNOWN", "reason_codes": [error.code], "provider": provider, "alternatives": [], "evidence": evidence, "permitted_actions": ["request_staff_booking"]}


def commit_provider(db, loc, action, request):
    provider = action.payload["authority"]
    key = "order_provider" if action.kind == "order" else "booking_provider"
    if loc.settings.get(key, "internal") != provider:
        raise DomainError("PROVIDER_CHANGED", "The authoritative provider changed; prepare a fresh proposal")
    capability = "orders_write" if action.kind == "order" else "bookings_cancel" if action.kind == "cancel_booking" else "bookings_modify" if action.kind == "reschedule_booking" else "bookings_write"
    adapter, conn = require_adapter(db, loc, provider, capability)
    if action.kind in ["reservation", "reschedule_booking"]:
        from .domain import availability
        from .schemas import Availability
        guarded = availability(db, loc, Availability(location_id=loc.id, start_at=request.start_at, party_size=request.party_size), alternatives=False, exclude=action.payload["decision"].get("booking_id"))
        if guarded["status"] != "AVAILABLE":
            raise DomainError("CAPACITY_CHANGED", "Staff constraints or provider availability changed; request a fresh proposal")
    if action.kind in ["cancel_booking", "reschedule_booking"]:
        prior = action.payload["decision"]
        record = db.get(Reservation, prior["booking_id"])
        remote = next((r for r in adapter.rows(db, context(loc, conn), "reservation") if record and r.external_id == record.external_id), None)
        if not record or record.provider != provider or not remote or remote.data["status"] != "confirmed" or remote.data["start_at"] != prior["expected_start_at"] or remote.data["end_at"] != prior["expected_end_at"]:
            raise DomainError("BOOKING_CHANGED", "Provider booking changed; verify a fresh proposal")
        result = adapter.cancel_booking(db, context(loc, conn), record.external_id) if action.kind == "cancel_booking" else adapter.modify_booking(db, context(loc, conn), record.external_id, request.model_dump(mode="json"))
        kind = "reservation"
    elif action.kind == "order":
        from .domain import quote
        from .schemas import QuoteRequest
        decision = quote(db, loc, QuoteRequest(location_id=loc.id, items=request.items))
        if any(decision[k] != action.payload["decision"][k] for k in ["items", "total_minor", "bucket_at", "pickup_min", "pickup_max"]):
            raise DomainError("QUOTE_CHANGED", "Confirm a fresh cart and pickup quote")
        for item in decision["items"]:
            projection = db.scalar(select(OperationalProjection).where(OperationalProjection.location_id == loc.id, OperationalProjection.tenant_id == loc.tenant_id, OperationalProjection.provider == provider, OperationalProjection.kind == "menu", OperationalProjection.data["local_id"].as_string() == item["item_id"]))
            remote_item = next((r for r in adapter.rows(db, context(loc, conn), "menu") if projection and r.external_id == projection.external_id), None)
            if not remote_item or not remote_item.data["available"] or remote_item.data["price_minor"] != item["unit_minor"]:
                raise DomainError("PROVIDER_CATALOG_CHANGED", "Item availability or price changed in the order provider")
        result = adapter.create_order(db, context(loc, conn), {**decision, "customer_name": request.name}, action.id)
        kind = "order"
    else:
        decision = adapter.availability(db, context(loc, conn), request.model_dump(mode="json"))
        if decision.get("unit_id") != action.payload["decision"].get("unit_id") or decision["status"] != "AVAILABLE":
            raise DomainError("PROVIDER_CONFLICT", "Provider capacity changed. Confirm a fresh proposal")
        result = adapter.create_booking(db, context(loc, conn), request.model_dump(mode="json"), action.id)
        kind = "reservation"
    remote = next(r for r in adapter.rows(db, context(loc, conn), kind) if r.external_id == result["external_id"])
    data = {**remote.data, "status" if kind == "reservation" else "state": result["status"]}
    apply_record(db, loc, conn, {"kind": kind, "external_id": remote.external_id, "data": data, "observed_at": now()})
    model = Reservation if kind == "reservation" else Order
    mirror = db.scalar(select(model).where(model.location_id == loc.id, model.tenant_id == loc.tenant_id, model.provider == provider, model.external_id == remote.external_id))
    if action.kind not in ["cancel_booking", "reschedule_booking"]:
        mirror.call_id, mirror.idempotency_key = action.call_id, action.id
    customer = db.get(Customer, mirror.customer_id)
    if action.kind not in ["cancel_booking", "reschedule_booking"]:
        customer.display_name, customer.phone = request.name, request.phone
    response = {**result, "id": mirror.id, "reference": result["external_id"], "kind": action.kind, "provider": provider, "mode": "mock"}
    action.state = "pending_confirmation" if result["status"] == "pending_confirmation" else "committed"
    action.result = response
    if action.call_id:
        call = db.get(Call, action.call_id)
        call.outcome = "pending" if action.state == "pending_confirmation" else "fulfilled"
        call.intent = action.kind
    emit(db, loc, "provider.pending_confirmation" if action.state == "pending_confirmation" else f"{kind}.created", {"provider": provider, "external_id": remote.external_id, "call_id": action.call_id, "mode": "mock"}, target=mirror.id)
    return response


def reconcile(db, loc, conn):
    if conn.status != "healthy":
        return
    for action in db.scalars(select(PendingAction).where(PendingAction.location_id == loc.id, PendingAction.tenant_id == loc.tenant_id, PendingAction.state == "pending_confirmation")):
        if action.payload.get("authority") != conn.provider or not action.result:
            continue
        remote = db.scalar(select(ProviderRecord).where(ProviderRecord.location_id == loc.id, ProviderRecord.tenant_id == loc.tenant_id, ProviderRecord.provider == conn.provider, ProviderRecord.external_id == action.result["external_id"]))
        if remote and remote.data.get("status", remote.data.get("state")) in ["confirmed", "accepted", "cancelled"]:
            action.state = "committed"
            action.result = {**action.result, "status": remote.data.get("status", remote.data.get("state"))}
            if action.call_id:
                db.get(Call, action.call_id).outcome = "fulfilled"
            emit(db, loc, "provider.reconciled", {"provider": conn.provider, "external_id": remote.external_id, "call_id": action.call_id}, target=action.id)
            for event in db.scalars(select(Event).where(Event.location_id == loc.id, Event.tenant_id == loc.tenant_id, Event.type == "provider.pending_confirmation")):
                if event.payload.get("provider") == conn.provider and event.payload.get("external_id") == remote.external_id:
                    emit(db, loc, "attention.reviewed", {"decision": "provider_confirmed", "target": f"event:{event.id}"}, target=f"event:{event.id}")


def describe(db, loc, conn):
    stale = not conn.last_sync_at or now() - utc(conn.last_sync_at) > timedelta(minutes=loc.settings.get("freshness_minutes", 30))
    return {"provider": conn.provider, "mode": conn.mode, "capabilities": conn.capabilities, "connection_status": conn.status, "sync_status": "stale" if stale and conn.status == "healthy" else conn.status, "last_sync_at": utc(conn.last_sync_at).isoformat() if conn.last_sync_at else None, "last_attempt_at": utc(conn.last_attempt_at).isoformat() if conn.last_attempt_at else None, "error_code": conn.error_code, "scenario": conn.config.get("scenario"), "booking_authority": loc.settings.get("booking_provider", "internal") == conn.provider, "order_authority": loc.settings.get("order_provider", "internal") == conn.provider, "detail": "Simulated provider with a separate persistent system of record. Not a live vendor connection."}


def change_booking(db, loc, row, request=None):
    if row.provider != loc.settings.get("booking_provider"):
        raise DomainError("PROVIDER_CHANGED", "Manage this booking in its authoritative provider")
    adapter, conn = require_adapter(db, loc, row.provider, "bookings_modify" if request else "bookings_cancel")
    if request:
        from .domain import availability
        if availability(db, loc, request, exclude=row.id, alternatives=False)["status"] != "AVAILABLE":
            raise DomainError("CAPACITY_CHANGED", "Staff constraints or provider availability block this change")
    result = adapter.modify_booking(db, context(loc, conn), row.external_id, request.model_dump(mode="json")) if request else adapter.cancel_booking(db, context(loc, conn), row.external_id)
    remote = next(r for r in adapter.rows(db, context(loc, conn), "reservation") if r.external_id == row.external_id)
    apply_record(db, loc, conn, {"kind": "reservation", "external_id": row.external_id, "data": remote.data, "observed_at": now()})
    emit(db, loc, "provider.booking_changed", {"provider": row.provider, "external_id": row.external_id, "status": result["status"]}, target=row.id)


def catalog():
    mocks = [{"provider": p, "mode": "mock", "capabilities": adapter.capabilities, "connection_status": "not_connected", "detail": "Explicitly simulated sample data; not live vendor capabilities."} for p, adapter in MOCK_ADAPTERS.items()]
    return mocks + [{"provider": "opentable", "mode": "not_configured", "capabilities": {key: "requires_permission" if key in LIVE_CAPABILITIES["opentable"] else "unsupported" for key in CAPABILITIES}, "detail": "OpenTable partner credentials and approved API access are required. No live endpoints are impersonated."}]


def request_staff_review(db, loc, request, call=None):
    if loc.vertical != "restaurant":
        raise DomainError("WRONG_VERTICAL", "Seating arrangement requests apply to restaurants", 422)
    if utc(request.start_at) < now() or utc(request.start_at) > now() + timedelta(days=90):
        raise DomainError("INVALID_TIME", "Staff requests require a future time within the booking horizon", 422)
    # Retry within a call is a single request, not repeated staff notifications.
    existing = list(db.scalars(select(Event).where(Event.location_id == loc.id, Event.tenant_id == loc.tenant_id, Event.type == "capacity.review_required")))
    if call:
        prior = next((e for e in existing if e.payload.get("call_id") == call.id and e.payload.get("party_size") == request.party_size and e.payload.get("start_at") == utc(request.start_at).isoformat()), None)
        if prior:
            return {"status": "awaiting_staff", "request_id": prior.id, "booking_confirmed": False}
    event = Event(tenant_id=loc.tenant_id, location_id=loc.id, type="capacity.review_required", payload={"party_size": request.party_size, "start_at": utc(request.start_at).isoformat(), "reason": request.reason, "call_id": call.id if call else None, "provider": loc.settings.get("booking_provider", "internal")})
    db.add(event)
    db.flush()
    emit(db, loc, "capacity.request_logged", {"request_id": event.id, "call_id": call.id if call else None})
    if call:
        call.outcome = "escalated"
    return {"status": "awaiting_staff", "request_id": event.id, "booking_confirmed": False, "callback_confirmed": False}
