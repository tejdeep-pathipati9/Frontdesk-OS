import asyncio
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, Request, Response, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from .config import settings
from .db import Session, transaction, init_db
from .models import *
from . import schemas as s
from .auth import identity, require_role, secret_matches, token_for
from .domain import DomainError, availability, seating_wait, quote, prepare, commit, confirm_staff, observe_transcript, interrupt_readback, emit, utc, conflict, business_open
from .serialization import serialize, booking_dict
from .seed import seed, DEMO_TENANT, RESTAURANT, SALON
from .providers import SquareAdapter, ToastAdapter
from .tools import execute_tool, TOOLS, AGENT_PROMPT
from .transcripts import start_retention, prune as prune_transcripts, retention_limit
from .integration_normalization import Envelope as MockIntegrationEvent


@asynccontextmanager
async def lifespan(app):
    cfg = settings()
    if not cfg.demo_mode and (len(cfg.session_secret) < 32 or cfg.session_secret.startswith("local-demo") or not cfg.owner_password):
        raise RuntimeError("Live mode requires a strong SESSION_SECRET and OWNER_PASSWORD")
    init_db()
    with transaction() as db:
        if cfg.demo_mode:
            seed(db)
        elif not db.scalar(select(Membership).limit(1)):
            tenant = Tenant(name="My business")
            db.add(tenant)
            db.flush()
            db.add(Membership(tenant_id=tenant.id, email=cfg.owner_email, role="owner"))
    yield


app = FastAPI(title="FrontDesk OS", version="0.1.0", lifespan=lifespan)
request_windows = defaultdict(deque)


@app.middleware("http")
async def protect(request, call_next):
    correlation = secrets.token_hex(12)
    request.state.correlation_id = correlation
    origin = request.headers.get("origin")
    if request.method not in ["GET", "HEAD", "OPTIONS"] and origin:
        from urllib.parse import urlsplit
        if origin != settings().web_origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"code": "INVALID_ORIGIN", "message": "Cross-origin changes are disabled", "retryable": False, "correlation_id": correlation}, status_code=403)
    key = (request.client.host if request.client else "local", request.url.path == "/api/v1/auth/login")
    window = request_windows[key]
    current = time.monotonic()
    while window and window[0] < current - 60:
        window.popleft()
    limit = 20 if key[1] else 600
    if len(window) >= limit:
        return JSONResponse({"code": "RATE_LIMIT", "message": "Too many requests", "retryable": True, "correlation_id": correlation}, status_code=429)
    window.append(current)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(DomainError)
async def handle_domain(request, error):
    return JSONResponse({"code": error.code, "message": error.message, "retryable": error.retryable, "correlation_id": request.state.correlation_id, **({"decision": error.decision} if error.decision is not None else {})}, status_code=error.status)



@app.exception_handler(RequestValidationError)
async def handle_validation(request, error):
    return JSONResponse({"code": "VALIDATION_ERROR", "message": "Invalid request fields", "fields": [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in error.errors()], "retryable": False, "correlation_id": request.state.correlation_id}, status_code=422)


def get_location(db, location_id, user):
    loc = db.get(Location, location_id)
    if not loc or loc.tenant_id != user["tenant_id"]:
        raise DomainError("NOT_FOUND", "Location not found", 404)
    return loc


def owned(db, model, id_, user):
    row = db.get(model, id_)
    if not row or row.tenant_id != user["tenant_id"]:
        raise DomainError("NOT_FOUND", "Record not found", 404)
    return row


@app.get("/api/v1/health")
def health():
    with Session() as db:
        db.execute(select(1))
    from sqlalchemy.engine import make_url
    url = make_url(settings().database_url)
    host = url.host or ""
    provider = "supabase" if host.endswith("supabase.co") or host.endswith("supabase.com") else "sqlite" if url.drivername.startswith("sqlite") else "postgresql"
    return {"status": "ok", "demo_mode": settings().demo_mode, "demo_login_enabled": settings().demo_mode and settings().allow_demo_login, "database": {"provider": provider, "connected": True}}



@app.post("/api/v1/auth/login")
def login(body: s.Login, response: Response):
    cfg = settings()
    if body.demo:
        if not cfg.demo_mode or not cfg.allow_demo_login:
            raise DomainError("DEMO_DISABLED", "Demo login is disabled", 403)
        email = cfg.owner_email
    else:
        if not secret_matches(body.password, cfg.owner_password) or body.email != cfg.owner_email:
            raise DomainError("BAD_CREDENTIALS", "Email or password is incorrect", 401)
        email = body.email
    with Session() as db:
        member = db.scalar(select(Membership).where(Membership.email == email))
        if not member:
            raise DomainError("BAD_CREDENTIALS", "Membership not found", 401)
        token = token_for(member)
        data = {"tenant_id": member.tenant_id, "role": member.role, "email": member.email}
    response.set_cookie("frontdesk_session", token, httponly=True, secure=not cfg.demo_mode, samesite="strict", max_age=43200)
    return data



@app.get("/api/v1/auth/me")
def me(user=Depends(identity)):
    return user


@app.post("/api/v1/auth/logout")
def logout(response: Response):
    response.delete_cookie("frontdesk_session")
    return {"status": "signed_out"}


@app.get("/api/v1/locations")
def list_locations(user=Depends(identity)):
    with Session() as db:
        return [serialize(r) for r in db.scalars(select(Location).where(Location.tenant_id == user["tenant_id"]).order_by(Location.created_at))]


@app.post("/api/v1/locations")
def create_location(body: s.LocationCreate, user=Depends(identity)):
    require_role(user)
    try:
        ZoneInfo(body.timezone)
    except Exception:
        raise DomainError("INVALID_TIMEZONE", "Use an IANA timezone such as America/New_York", 422)
    with transaction() as db:
        loc = Location(tenant_id=user["tenant_id"], name=body.name, vertical=body.vertical, timezone=body.timezone, mode="demo" if settings().demo_mode else "live", settings={"open_hour": 9, "close_hour": 22, "turn_minutes": 75, "cleaning_minutes": 10, "freshness_minutes": 30, "tax_bps": 0, "intake_paused": False, "booking_provider": "internal", "order_provider": "internal", "combinations": [], "currency": "USD"})
        db.add(loc)
        db.flush()
        if body.vertical == "restaurant":
            db.add(KitchenState(tenant_id=loc.tenant_id, location_id=loc.id))
            db.flush()
            if body.existing_software == "staff_requests":
                loc.settings = {**loc.settings, "booking_provider": "staff_requests"}
            elif body.existing_software != "internal":
                from .integration_service import connect
                connect(db, loc, body.existing_software)
        emit(db, loc, "location.created", actor=user["id"])
        return serialize(loc)



@app.get("/api/v1/locations/{location_id}")
def read_location(location_id: str, user=Depends(identity)):
    with Session() as db:
        return serialize(get_location(db, location_id, user))


@app.patch("/api/v1/locations/{location_id}")
def patch_settings(location_id: str, body: s.SettingsPatch, user=Depends(identity)):
    require_role(user)
    with transaction() as db:
        loc = get_location(db, location_id, user)
        changes = body.model_dump(exclude_none=True)
        if "ai_actions" in changes:
            changes["ai_actions"] = {**loc.settings.get("ai_actions", {}), **changes["ai_actions"]}
        if "open_hour" in changes or "close_hour" in changes:
            changes["hours_override"] = True
        updated = {**loc.settings, **changes}
        if updated["open_hour"] >= updated["close_hour"]:
            raise DomainError("INVALID_HOURS", "Closing hour must be after opening hour; overnight hours are not supported in this prototype", 422)
        valid_ids = {r.id for r in db.scalars(select(Resource).where(Resource.location_id == loc.id, Resource.tenant_id == loc.tenant_id, Resource.kind == "table"))}
        if any(len(group) < 2 or len(set(group)) != len(group) or any(id_ not in valid_ids for id_ in group) for group in updated.get("combinations", [])):
            raise DomainError("INVALID_COMBINATION", "Table combinations must contain distinct tables at this location", 422)
        loc.settings = updated
        if body.transcript_retention_calls is not None:
            prune_transcripts(db, loc)
        emit(db, loc, "settings.updated", body.model_dump(exclude_none=True), user["id"])
        return serialize(loc)




def scoped_list(db, model, loc):
    return list(db.scalars(select(model).where(model.tenant_id == loc.tenant_id, model.location_id == loc.id).order_by(model.created_at.desc()).limit(500)))


@app.get("/api/v1/locations/{location_id}/snapshot")
def snapshot(location_id: str, user=Depends(identity)):
    with Session() as db:
        loc = get_location(db, location_id, user)
        bookings = scoped_list(db, Reservation, loc)
        if user["role"] == "stylist":
            # Prototype has no membership-to-staff assignment; restrict detailed
            # records until an owner assigns staff visibility.
            bookings = []
        kitchen = db.scalar(select(KitchenState).where(KitchenState.location_id == loc.id, KitchenState.tenant_id == loc.tenant_id))
        orders = []
        for row in scoped_list(db, Order, loc):
            data = serialize(row)
            customer = db.get(Customer, row.customer_id)
            data["customer_name"] = customer.display_name if customer else "Guest"
            data["can_manage"] = row.provider == "internal" or row.provider == "mock_square" and loc.settings.get("order_provider") == "mock_square"
            orders.append(data)
        calls = [{k: v for k, v in serialize(r).items() if k != "transcript"} for r in scoped_list(db, Call, loc)] if user["role"] in ["owner", "manager", "host"] else []
        from .domain import resources
        from .integration_service import catalog_ids, describe
        ids = catalog_ids(db, loc)
        connections = scoped_list(db, IntegrationConnection, loc)
        return {"location": serialize(loc), "resources": [serialize(r) for r in resources(db, loc)], "services": [serialize(r) for r in scoped_list(db, Service, loc)], "menu": [serialize(r) for r in scoped_list(db, MenuItem, loc) if ids is None or r.id in ids], "reservations": [booking_dict(db, r) for r in bookings], "waitlist": [serialize(r) for r in scoped_list(db, WaitlistEntry, loc)], "orders": orders, "kitchen": serialize(kitchen) if kitchen else None, "calls": calls, "events": [serialize(r) for r in scoped_list(db, Event, loc)[:50]], "integration_health": [describe(db, loc, c) for c in connections], "as_of": now().isoformat()}



@app.get("/api/v1/locations/{location_id}/tables")
def list_tables(location_id: str, user=Depends(identity)):
    with Session() as db:
        from .domain import resources
        return [serialize(r) for r in resources(db, get_location(db, location_id, user)) if r.kind == "table"]



@app.post("/api/v1/locations/{location_id}/tables/{table_id}/transitions")
def table_transition(location_id: str, table_id: str, body: s.TableTransition, user=Depends(identity)):
    require_role(user, "host")
    transitions = {"available": ["held", "seated", "cleaning", "out_of_service"], "held": ["available", "seated", "out_of_service"], "seated": ["cleaning", "out_of_service"], "cleaning": ["available", "out_of_service"], "out_of_service": ["available"]}
    with transaction() as db:
        loc = get_location(db, location_id, user)
        table = owned(db, Resource, table_id, user)
        if table.location_id != loc.id or table.kind != "table":
            raise DomainError("NOT_FOUND", "Table not found", 404)
        if body.state != table.state and body.state not in transitions.get(table.state, []):
            raise DomainError("INVALID_TRANSITION", "Clear an occupied table to cleaning before making it available")
        if body.state == "seated":
            if not body.party_size or body.party_size > table.capacity:
                raise DomainError("INVALID_PARTY", "Party size must fit the selected table", 422)
            end = now() + timedelta(minutes=loc.settings.get("turn_minutes", 75) + loc.settings.get("cleaning_minutes", 10))
            if conflict(db, loc, [table.id], now(), end, body.reservation_id):
                raise DomainError("UPCOMING_RESERVATION", "Seating would overlap a reservation")
            if body.reservation_id:
                reservation = owned(db, Reservation, body.reservation_id, user)
                allocated = db.scalar(select(Allocation.id).where(Allocation.reservation_id == reservation.id, Allocation.resource_id == table.id))
                if reservation.location_id != loc.id or not allocated or reservation.status != "confirmed":
                    raise DomainError("INVALID_RESERVATION", "Reservation does not belong to this table")
                reservation.status = "seated"
            table.seated_at, table.party_size = now(), body.party_size
        elif body.state in ["available", "cleaning"]:
            for r in db.scalars(select(Reservation).join(Allocation).where(Allocation.resource_id == table.id, Reservation.tenant_id == loc.tenant_id, Reservation.status == "seated")):
                r.status = "completed"
            table.seated_at, table.party_size = None, None
        before = table.state
        table.state, table.verified_at = body.state, now()
        table.attributes = {**table.attributes, "staff_verified": True}
        emit(db, loc, "table.transitioned", {"table_id": table.id, "before": before, "after": table.state}, user["id"], table.id)
        return serialize(table)



@app.post("/api/v1/availability/restaurant")
@app.post("/api/v1/availability/salon")
def check_availability(body: s.Availability, user=Depends(identity)):
    with Session() as db:
        return availability(db, get_location(db, body.location_id, user), body)


@app.post("/api/v1/wait-estimates/seating")
def wait_estimate(body: s.WaitlistCreate, user=Depends(identity)):
    with Session() as db:
        return seating_wait(db, get_location(db, body.location_id, user), body.party_size)


@app.post("/api/v1/orders/quote")
@app.post("/api/v1/wait-estimates/takeout")
def order_quote(body: s.QuoteRequest, user=Depends(identity)):
    with Session() as db:
        return quote(db, get_location(db, body.location_id, user), body)


@app.post("/api/v1/actions/prepare")
def prepare_action(body: s.PrepareAction, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        return prepare(db, get_location(db, body.location_id, user), body)


@app.post("/api/v1/actions/{action_id}/confirm")
def confirm_action(action_id: str, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        action = owned(db, PendingAction, action_id, user)
        if action.call_id:
            raise DomainError("VOICE_CONFIRMATION_REQUIRED", "Voice actions require customer transcript confirmation", 403)
        confirm_staff(db, action)
        emit(db, get_location(db, action.location_id, user), "action.confirmed_by_staff", {"action_id": action.id}, user["id"], action.id)
        return {"status": "confirmed_for_commit"}


@app.post("/api/v1/actions/commit")
@app.post("/api/v1/reservations")
@app.post("/api/v1/orders")
@app.post("/api/v1/appointments")
def commit_action(body: s.CommitAction, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        action = owned(db, PendingAction, body.action_id, user)
        return commit(db, get_location(db, action.location_id, user), action)


@app.get("/api/v1/reservations")
@app.get("/api/v1/appointments")
def list_reservations(location_id: str, user=Depends(identity)):
    with Session() as db:
        return [booking_dict(db, r) for r in scoped_list(db, Reservation, get_location(db, location_id, user))]


@app.patch("/api/v1/reservations/{id_}")
@app.patch("/api/v1/appointments/{id_}")
def change_reservation(id_: str, body: s.StatusPatch, user=Depends(identity)):
    require_role(user, "host")
    allowed = {"confirmed": ["cancelled", "no_show", "completed"], "seated": ["completed"], "cancelled": [], "no_show": [], "completed": []}
    with transaction() as db:
        row = owned(db, Reservation, id_, user)
        if row.provider != "internal":
            if body.status != "cancelled" or row.status != "confirmed":
                raise DomainError("PROVIDER_MANAGED_RECORD", "Only supported provider cancellation is available for this mirrored booking", 422)
            from .integration_service import change_booking
            change_booking(db, get_location(db, row.location_id, user), row)
            return booking_dict(db, row)
        if body.status not in allowed.get(row.status, []):
            raise DomainError("INVALID_TRANSITION", "Booking cannot move to this state")
        before = row.status
        row.status = body.status
        emit(db, get_location(db, row.location_id, user), "reservation.updated", {"before": before, "after": row.status}, user["id"], row.id)
        return booking_dict(db, row)



@app.post("/api/v1/reservations/{id_}/reschedule")
@app.post("/api/v1/appointments/{id_}/reschedule")
def reschedule(id_: str, body: s.Availability, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        row = owned(db, Reservation, id_, user)
        loc = get_location(db, row.location_id, user)
        if body.location_id != loc.id or row.status != "confirmed":
            raise DomainError("INVALID_RESCHEDULE", "Only confirmed internal bookings at this location can be rescheduled")
        if row.provider != "internal":
            from .integration_service import change_booking
            change_booking(db, loc, row, body)
            return booking_dict(db, row)
        if row.kind == "appointment" and body.service_id != row.service_id:
            raise DomainError("SERVICE_CHANGED", "Cancel and create a new appointment to change service", 422)
        result = availability(db, loc, body, exclude=row.id, force_internal=loc.settings.get("booking_provider") == "staff_requests")
        if result["status"] != "AVAILABLE":
            raise DomainError("NO_CAPACITY", "Requested replacement slot is unavailable")
        for allocation in db.scalars(select(Allocation).where(Allocation.reservation_id == row.id)):
            db.delete(allocation)
        row.start_at, row.end_at, row.party_size = utc(result["start_at"]), utc(result["end_at"]), body.party_size if row.kind == "reservation" else 1
        for resource_id in result["resource_ids"]:
            db.add(Allocation(tenant_id=loc.tenant_id, reservation_id=row.id, resource_id=resource_id, start_at=row.start_at, end_at=row.end_at))
        db.flush()
        emit(db, loc, "reservation.rescheduled", {"start_at": result["start_at"]}, user["id"], row.id)
        return booking_dict(db, row)



@app.get("/api/v1/waitlist")
def list_waitlist(location_id: str, user=Depends(identity)):
    with Session() as db:
        return [serialize(r) for r in scoped_list(db, WaitlistEntry, get_location(db, location_id, user))]


@app.post("/api/v1/waitlist")
def join_waitlist(body: s.WaitlistCreate, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        loc = get_location(db, body.location_id, user)
        estimate = seating_wait(db, loc, body.party_size)
        row = WaitlistEntry(tenant_id=loc.tenant_id, location_id=loc.id, name=body.name, party_size=body.party_size, estimate_min=estimate.get("wait_minutes", {}).get("min"), estimate_max=estimate.get("wait_minutes", {}).get("max"))
        db.add(row)
        db.flush()
        emit(db, loc, "waitlist.joined", {"id": row.id}, user["id"], row.id)
        return serialize(row)


@app.patch("/api/v1/waitlist/{id_}")
def change_waitlist(id_: str, body: s.StatusPatch, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        row = owned(db, WaitlistEntry, id_, user)
        if row.status != "waiting" or body.status not in ["removed", "seated"]:
            raise DomainError("INVALID_TRANSITION", "Waitlist entry cannot move to this state")
        # Staff marks a party seated only after the separate floor seat action.
        row.status = body.status
        emit(db, get_location(db, row.location_id, user), "waitlist.updated", {"status": row.status}, user["id"], row.id)
        return serialize(row)


@app.patch("/api/v1/locations/{location_id}/operational-state")
def update_kitchen(location_id: str, body: s.KitchenPatch, user=Depends(identity)):
    require_role(user, "kitchen")
    with transaction() as db:
        loc = get_location(db, location_id, user)
        kitchen = db.scalar(select(KitchenState).where(KitchenState.location_id == loc.id, KitchenState.tenant_id == loc.tenant_id))
        if not kitchen:
            raise DomainError("NOT_FOUND", "Kitchen not found", 404)
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(kitchen, key, value)
        kitchen.verified_at = now()
        emit(db, loc, "kitchen.updated", body.model_dump(exclude_none=True), user["id"], kitchen.id)
        return serialize(kitchen)


@app.get("/api/v1/locations/{location_id}/operational-state")
def operational_state(location_id: str, user=Depends(identity)):
    return snapshot(location_id, user)


@app.get("/api/v1/menu")
def menu(location_id: str, user=Depends(identity)):
    with Session() as db:
        from .integration_service import catalog_ids
        loc = get_location(db, location_id, user)
        ids = catalog_ids(db, loc)
        return [serialize(r) for r in scoped_list(db, MenuItem, loc) if r.id in ids]



@app.post("/api/v1/resources")
def create_resource(body: s.ResourceCreate, user=Depends(identity)):
    require_role(user)
    with transaction() as db:
        loc = get_location(db, body.location_id, user)
        if loc.vertical == "restaurant" and loc.settings.get("floor_provider"):
            raise DomainError("PROVIDER_MANAGED_CONFIGURATION", "Import table configuration from the selected floor provider", 422)
        if db.scalar(select(Resource.id).where(Resource.tenant_id == loc.tenant_id, Resource.location_id == loc.id, Resource.name == body.name)):
            raise DomainError("DUPLICATE_NAME", "Resource name already exists", 422)
        row = Resource(tenant_id=loc.tenant_id, location_id=loc.id, name=body.name, kind="table" if loc.vertical == "restaurant" else "stylist", capacity=body.capacity if loc.vertical == "restaurant" else 1, attributes={"section": "Dining room"} if loc.vertical == "restaurant" else {"shift_start": 9, "shift_end": 18, "blocked": [], "color": "#849477"})
        db.add(row)
        db.flush()
        emit(db, loc, "resource.created", {"id": row.id, "kind": row.kind}, user["id"], row.id)
        return serialize(row)



@app.post("/api/v1/services")
def create_service(body: s.ServiceCreate, user=Depends(identity)):
    require_role(user)
    with transaction() as db:
        loc = get_location(db, body.location_id, user)
        if loc.vertical != "salon":
            raise DomainError("WRONG_VERTICAL", "Services require a salon location", 422)
        for id_ in body.eligible_staff:
            row = owned(db, Resource, id_, user)
            if row.location_id != loc.id or row.kind != "stylist":
                raise DomainError("INVALID_STAFF", "Staff must belong to this salon", 422)
        service = Service(tenant_id=loc.tenant_id, **body.model_dump())
        db.add(service)
        db.flush()
        emit(db, loc, "service.created", {"id": service.id}, user["id"], service.id)
        return serialize(service)


@app.patch("/api/v1/services/{id_}")
def patch_service(id_: str, body: s.ServicePatch, user=Depends(identity)):
    require_role(user)
    with transaction() as db:
        service = owned(db, Service, id_, user)
        service.enabled = body.enabled
        emit(db, get_location(db, service.location_id, user), "service.updated", {"enabled": service.enabled}, user["id"], service.id)
        return serialize(service)


@app.patch("/api/v1/menu/{id_}")
def update_menu(id_: str, body: dict, user=Depends(identity)):
    require_role(user, "kitchen")
    if set(body) != {"available"} or not isinstance(body["available"], bool):
        raise DomainError("INVALID_ARGUMENTS", "Provide an available boolean", 422)
    with transaction() as db:
        row = owned(db, MenuItem, id_, user)
        row.available = body["available"]
        projection = db.scalar(select(OperationalProjection).where(OperationalProjection.tenant_id == row.tenant_id, OperationalProjection.location_id == row.location_id, OperationalProjection.kind == "menu", OperationalProjection.data["local_id"].as_string() == row.id))
        if projection:
            projection.data = {**projection.data, "staff_disabled": not body["available"]}
            row.available = body["available"] and projection.data.get("available", False)
        emit(db, get_location(db, row.location_id, user), "menu.availability_changed", {"available": row.available}, user["id"], row.id)
        return serialize(row)



@app.patch("/api/v1/orders/{id_}")
def change_order(id_: str, body: s.StatusPatch, user=Depends(identity)):
    require_role(user, "kitchen")
    transitions = {"accepted": ["preparing", "cancelled"], "preparing": ["ready", "cancelled"], "ready": ["picked_up", "cancelled"]}
    with transaction() as db:
        row = owned(db, Order, id_, user)
        if body.status not in transitions.get(row.state, []):
            raise DomainError("INVALID_TRANSITION", "Order cannot move to this state")
        if row.provider != "internal":
            loc = get_location(db, row.location_id, user)
            from .integration_service import require_adapter, context, apply_record
            if row.provider != loc.settings.get("order_provider"):
                raise DomainError("PROVIDER_MANAGED_RECORD", "This imported order is read-only; manage it in its source system", 422)
            adapter, conn = require_adapter(db, loc, row.provider, "orders_write")
            remote = next((r for r in adapter.rows(db, context(loc, conn), "order") if r.external_id == row.external_id), None)
            if not remote or remote.data["state"] != row.state:
                raise DomainError("PROVIDER_CONFLICT", "Order changed in its provider; synchronize first")
            remote.data = {**remote.data, "state": body.status}
            apply_record(db, loc, conn, {"kind": "order", "external_id": remote.external_id, "data": remote.data, "observed_at": now()})
            emit(db, loc, "provider.order_updated", {"provider": row.provider, "external_id": row.external_id, "state": body.status}, user["id"], row.id)
            return serialize(row)
        row.state = body.status
        emit(db, get_location(db, row.location_id, user), "order.updated", {"state": row.state}, user["id"], row.id)
        return serialize(row)



@app.patch("/api/v1/resources/{id_}")
def change_resource(id_: str, body: s.ResourcePatch, user=Depends(identity)):
    require_role(user)
    with transaction() as db:
        row = owned(db, Resource, id_, user)
        if row.kind != "stylist":
            raise DomainError("USE_TABLE_TRANSITION", "Use floor actions to update table state", 422)
        if body.state:
            row.state = body.state
        if body.attributes:
            attrs = body.attributes
            if set(attrs) - {"shift_start", "shift_end", "blocked"}:
                raise DomainError("INVALID_SCHEDULE", "Only shifts and blocked intervals are editable", 422)
            merged = {**row.attributes, **attrs}
            if not 0 <= merged["shift_start"] < merged["shift_end"] <= 24:
                raise DomainError("INVALID_SCHEDULE", "Shift hours must be ordered between 0 and 24", 422)
            for block in merged.get("blocked", []):
                try:
                    if utc(block["start_at"]) >= utc(block["end_at"]):
                        raise ValueError()
                except Exception:
                    raise DomainError("INVALID_SCHEDULE", "Blocked intervals need valid start/end times", 422)
            row.attributes = merged
        row.verified_at = now()
        emit(db, get_location(db, row.location_id, user), "staff.updated", {"state": row.state}, user["id"], row.id)
        return serialize(row)


@app.get("/api/v1/calls")
def calls(location_id: str, q: str = "", outcome: str = "", limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(identity)):
    require_role(user, "host")
    with Session() as db:
        loc = get_location(db, location_id, user)
        rows = scoped_list(db, Call, loc)
        rows = [r for r in rows if (not outcome or r.outcome == outcome) and (not q or q.lower() in (r.id + r.intent + r.summary).lower())]
        return {"items": [{k: v for k, v in serialize(r).items() if k != "transcript"} for r in rows[offset:offset + limit]], "total": len(rows)}


@app.get("/api/v1/calls/{id_}")
def call_detail(id_: str, user=Depends(identity)):
    require_role(user, "host")
    with Session() as db:
        row = owned(db, Call, id_, user)
        result = serialize(row)
        result["tools"] = [serialize(t) for t in db.scalars(select(ToolExecution).where(ToolExecution.call_id == row.id, ToolExecution.tenant_id == row.tenant_id).order_by(ToolExecution.created_at))]
        actions = list(db.scalars(select(PendingAction).where(PendingAction.call_id == row.id, PendingAction.tenant_id == row.tenant_id, PendingAction.location_id == row.location_id).order_by(PendingAction.created_at)))
        linked_ids = [a.result['id'] for a in actions if a.result and a.result.get('id')]
        result["reservations"] = [booking_dict(db, r) for r in db.scalars(select(Reservation).where(Reservation.tenant_id == row.tenant_id, Reservation.location_id == row.location_id, (Reservation.call_id == row.id) | Reservation.id.in_(linked_ids)))]
        result["orders"] = [serialize(r) for r in db.scalars(select(Order).where(Order.tenant_id == row.tenant_id, Order.location_id == row.location_id, (Order.call_id == row.id) | Order.id.in_(linked_ids)))]
        result['actions'] = [{'id': a.id, 'kind': a.kind, 'state': a.state, 'created_at': utc(a.created_at).isoformat(), 'confirmed_at': utc(a.confirmed_at).isoformat() if a.confirmed_at else None, 'result': a.result} for a in actions]
        events = list(db.scalars(select(Event).where(Event.tenant_id == row.tenant_id, Event.location_id == row.location_id, Event.payload['call_id'].as_string() == row.id).order_by(Event.created_at)))
        result['events'] = [serialize(e) for e in events]
        targets = [f'action:{a.id}' for a in actions] + [f'event:{e.id}' for e in events] + [f"tool:{t['id']}" for t in result['tools']]
        result['reviews'] = [serialize(r) for r in db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == row.tenant_id, AuditEvent.location_id == row.location_id, AuditEvent.action == 'attention.reviewed', AuditEvent.target_id.in_(targets)).order_by(AuditEvent.created_at))]
        result['transcript_retention_calls'] = retention_limit(db.get(Location, row.location_id))
        return result



@app.get("/api/v1/audit-events")
def audits(location_id: str, user=Depends(identity)):
    require_role(user)
    with Session() as db:
        return [serialize(r) for r in scoped_list(db, AuditEvent, get_location(db, location_id, user))]


@app.get("/api/v1/integrations")
def integrations(location_id: str, user=Depends(identity)):
    require_role(user)
    with Session() as db:
        loc = get_location(db, location_id, user)
        tests = list(db.scalars(select(Event).where(Event.location_id == loc.id, Event.tenant_id == loc.tenant_id, Event.type == "integration.tested").order_by(Event.created_at.desc(), Event.id)))
        from .integration_service import catalog, describe
        stored = {c.provider: describe(db, loc, c) for c in scoped_list(db, IntegrationConnection, loc)}
        additional = [stored.get(item["provider"], item) for item in catalog()]
    # Environment credentials are scoped to the seeded demo locations, never all
    # tenants. New locations cannot implicitly inherit another seller's token.
    allowed = user["tenant_id"] == DEMO_TENANT and location_id in [RESTAURANT, SALON]
    result = [{"provider": "internal", "mode": "demo" if settings().demo_mode else "connected_read_write", "capabilities": {"bookings_write": "supported", "orders_write": "supported"}}]
    if loc.vertical == "restaurant":
        from .integration_adapters import PROVIDERS
        result[0].update(capabilities=PROVIDERS["internal"].capabilities, booking_authority=loc.settings.get("booking_provider", "internal") in ["internal", "staff_requests"], order_authority=loc.settings.get("order_provider", "internal") == "internal")
    for provider, adapter, capability in [("square", SquareAdapter(), "bookings_write"), ("toast", ToastAdapter(), "orders_write")]:
        row = {"provider": provider, "mode": "configured_untested" if allowed and adapter.configured else "not_configured", "capabilities": {capability: "requires_permission", "table_occupancy": "unsupported"}}
        latest = next((event for event in tests if event.payload.get("provider") == provider), None)
        if latest and allowed:
            row["last_tested_at"] = utc(latest.created_at).isoformat()
            if latest.payload.get("_configuration_ref") == integration_configuration_ref(provider):
                row.update({key: value for key, value in latest.payload.items() if not key.startswith("_")})
        result.append(row)
        if loc.vertical == "restaurant":
            row["capabilities"] = {**PROVIDERS[provider].capabilities, **row["capabilities"]}
            # Square Bookings supports appointments, not our restaurant booking
            # contract. A location probe never activates operational writes.
            row["capabilities"]["bookings_write"] = "unsupported"
    return result + additional



def integration_configuration_ref(provider):
    """Invalidate cached health when credentials or account configuration change."""
    cfg = settings()
    fields = [cfg.square_access_token, cfg.square_location_id, cfg.square_environment, cfg.square_api_version] if provider == "square" else [cfg.toast_client_id, cfg.toast_client_secret, cfg.toast_restaurant_guid, cfg.toast_api_url]
    return hmac.new(cfg.session_secret.encode(), json.dumps(fields).encode(), hashlib.sha256).hexdigest()


@app.post("/api/v1/integrations/{provider}/test")
def test_integration(provider: str, location_id: str, user=Depends(identity)):
    require_role(user)
    with Session() as db:
        get_location(db, location_id, user)
    if user["tenant_id"] != DEMO_TENANT or location_id not in [RESTAURANT, SALON]:
        raise DomainError("NOT_CONFIGURED", "Configure tenant-specific credentials before testing this location", 503)
    if provider not in ["square", "toast"]:
        raise DomainError("INVALID_PROVIDER", "Choose Square or Toast", 422)
    result = (SquareAdapter() if provider == "square" else ToastAdapter()).test()
    with transaction() as db:
        emit(db, get_location(db, location_id, user), "integration.tested", {**result, "_configuration_ref": integration_configuration_ref(provider)}, user["id"])
    return {**result, "last_tested_at": now().isoformat()}


@app.get("/api/v1/voice/config")
def voice_config(location_id: str, user=Depends(identity)):
    with Session() as db:
        loc = get_location(db, location_id, user)
    cfg = settings()
    assistant = cfg.vapi_restaurant_assistant_id if loc.vertical == "restaurant" else cfg.vapi_salon_assistant_id
    allowed = user["tenant_id"] == DEMO_TENANT and location_id in [RESTAURANT, SALON]
    return {"preferred_provider": loc.settings.get("preferred_voice_provider", "vapi"), "vapi": {"configured": allowed and bool(cfg.vapi_public_key and assistant and cfg.vapi_webhook_secret), "public_key": cfg.vapi_public_key if allowed else "", "assistant_id": assistant if allowed else "", "purpose": "Phone and web calls"}, "livekit": {"configured": allowed and bool(cfg.livekit_url and cfg.livekit_api_key and cfg.livekit_api_secret and cfg.agent_api_token), "purpose": "Self-hosted voice"}, "ollama": {"model": cfg.ollama_model}, "recordings_retained": False, "transcripts_retained": bool(retention_limit(loc)), "transcript_retention_calls": retention_limit(loc), "tools": TOOLS, "prompt": AGENT_PROMPT}



@app.post("/api/v1/voice/test-calls")
def start_test_call(body: dict, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        loc = get_location(db, body.get("location_id"), user)
        call = Call(tenant_id=loc.tenant_id, location_id=loc.id, provider="tool_test", provider_call_id=uid(), caller_masked="Tool test · no audio")
        db.add(call)
        db.flush()
        return serialize(call)


@app.post("/api/v1/voice/test-calls/{id_}/tools")
def test_tool(id_: str, body: s.ToolRequest, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        call = owned(db, Call, id_, user)
        if call.provider != "tool_test":
            raise DomainError("FORBIDDEN", "Use authenticated provider tools for voice calls", 403)
        return execute_tool(db, call, body.name, body.arguments, body.request_id)


@app.post("/api/v1/voice/test-calls/{id_}/confirm")
def confirm_test_call(id_: str, body: s.CommitAction, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        call = owned(db, Call, id_, user)
        action = owned(db, PendingAction, body.action_id, user)
        if call.provider != "tool_test" or action.call_id != call.id or call.status != "in_progress":
            raise DomainError("FORBIDDEN", "Only tool test actions can use this confirmation button", 403)
        confirm_staff(db, action)
        return {"status": "confirmed_for_commit"}


def end_call(db, call):
    call.status, call.ended_at = "ended", now()
    actions = list(db.scalars(select(PendingAction).where(PendingAction.call_id == call.id, PendingAction.tenant_id == call.tenant_id)))
    uncertain = any(a.state == 'pending_confirmation' for a in actions)
    if uncertain:
        call.outcome = 'pending'
    elif call.outcome == "pending":
        failed = db.scalar(select(ToolExecution.id).where(ToolExecution.call_id == call.id, ToolExecution.status == 'failed', ToolExecution.name.in_(['prepare_action', 'commit_action'])))
        call.outcome = "failed" if failed else "information_only"
    for action in actions:
        if action.state == 'awaiting_confirmation':
            from .confirmation_intent import discard
            discard(action)
            action.state = 'abandoned'
    completed = [a for a in actions if a.state == 'committed']
    if uncertain:
        call.summary = 'Awaiting provider confirmation. Staff review required.'
    elif completed:
        last = completed[-1]
        call.summary = f"{last.kind.replace('_', ' ').capitalize()} {last.result.get('status', 'completed').replace('_', ' ')}. Reference {last.result.get('reference', '')}."
    else:
        call.summary = {'failed': 'Request not completed. Staff review required.', 'escalated': 'Staff assistance requested.', 'information_only': 'Information provided. No booking or order created.'}.get(call.outcome, 'Call ended.')
    prune_transcripts(db, db.get(Location, call.location_id))




@app.post("/api/v1/voice/test-calls/{id_}/end")
def end_test_call(id_: str, user=Depends(identity)):
    with transaction() as db:
        call = owned(db, Call, id_, user)
        if call.provider != "tool_test":
            raise DomainError("FORBIDDEN", "Cannot end a provider call here", 403)
        end_call(db, call)
        return serialize(call)


@app.post("/api/v1/voice/vapi")
def vapi_webhook(body: dict, request: Request):
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not secret_matches(supplied, settings().vapi_webhook_secret):
        raise DomainError("UNAUTHENTICATED_WEBHOOK", "Webhook credential missing or invalid", 401)
    message = body.get("message", {})
    provider_call = message.get("call", {})
    assistant = provider_call.get("assistantId") or message.get("assistant", {}).get("id")
    cfg = settings()
    mapping = {id_: loc for id_, loc in [(cfg.vapi_restaurant_assistant_id, RESTAURANT), (cfg.vapi_salon_assistant_id, SALON)] if id_}
    if assistant not in mapping or not provider_call.get("id"):
        raise DomainError("UNKNOWN_ASSISTANT", "Assistant is not mapped to an authorized location", 403)
    if message.get("type") == "tool-calls":
        # Transcript webhooks and tool requests can arrive out of order. Give a
        # delayed final transcript a short chance to arrive, WITHOUT holding the
        # SQLite writer lock that its handler needs. No model-supplied consent.
        deadline = time.monotonic() + 1.5
        for item in message.get("toolCallList", []):
            function = item.get("function", {})
            if function.get("name") != "commit_action":
                continue
            args = function.get("arguments", {})
            try:
                args = json.loads(args) if isinstance(args, str) else args
                action_id = args.get("action_id") if isinstance(args, dict) else None
            except ValueError:
                continue
            if not action_id:
                continue
            while True:
                with Session() as db:
                    action = db.scalar(select(PendingAction).join(Call, PendingAction.call_id == Call.id).where(PendingAction.id == action_id, PendingAction.location_id == mapping[assistant], Call.provider == "vapi", Call.provider_call_id == provider_call["id"]))
                    waiting = bool(action and action.state == "awaiting_confirmation" and not action.confirmed_at and utc(action.expires_at) > now() and action.payload.get("confirmation", {}).get("intent") not in ["interpreting", "change", "reject", "question", "unclear"])
                if not waiting or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        # Interpret the authenticated final user turn outside the SQLite lock.
        # The resolver rechecks call/action/readback/turn identity before applying.
        if any(item.get("function", {}).get("name") == "commit_action" for item in message.get("toolCallList", [])):
            with Session() as db:
                authorized_call = db.scalar(select(Call).where(Call.provider == "vapi", Call.provider_call_id == provider_call["id"], Call.location_id == mapping[assistant]))
                call_id = authorized_call.id if authorized_call else None
            if call_id:
                from .confirmation_intent import resolve
                resolve(call_id, Session)
    with transaction() as db:
        loc = db.get(Location, mapping[assistant])
        if not loc:
            raise DomainError("NOT_CONFIGURED", "Assistant location does not exist", 503)
        call = db.scalar(select(Call).where(Call.provider == "vapi", Call.provider_call_id == provider_call["id"]))
        if not call:
            number = provider_call.get("customer", {}).get("number", "")
            call = Call(tenant_id=loc.tenant_id, location_id=loc.id, provider="vapi", provider_call_id=provider_call["id"], caller_masked=f"•••• {number[-4:]}" if number else "Vapi web call")
            db.add(call)
            db.flush()
            start_retention(db, call, loc)
        type_ = message.get("type", "unknown")
        source_id = message.get("id") or hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()
        source_id = f"{call.provider_call_id}:{source_id}"
        replayed = db.scalar(select(Event.id).where(Event.source == "vapi", Event.source_event_id == source_id))
        if type_ == "tool-calls":
            results = []
            for item in message.get("toolCallList", []):
                function = item.get("function", {})
                args = function.get("arguments", {})
                try:
                    if isinstance(args, str):
                        args = json.loads(args)
                    if not isinstance(args, dict):
                        raise ValueError()
                    result = execute_tool(db, call, function.get("name", ""), args, item["id"])
                except (ValueError, KeyError, DomainError) as error:
                    result = {"status": "failed", "code": getattr(error, "code", "INVALID_ARGUMENTS"), "message": getattr(error, "message", "Malformed tool request")}
                results.append({"toolCallId": item.get("id", ""), "result": json.dumps(result)})
            if not replayed:
                db.add(Event(tenant_id=loc.tenant_id, location_id=loc.id, source="vapi", source_event_id=source_id, type="agent.tool_requested", payload={"call_id": call.id, "tool_count": len(results)}))
            return {"results": results}
        if replayed:
            return {"status": "already_processed"}
        if type_ == "user-interrupted":
            for action in db.scalars(select(PendingAction).where(PendingAction.call_id == call.id, PendingAction.tenant_id == loc.tenant_id, PendingAction.state == "awaiting_confirmation")):
                interrupt_readback(action)
        if (type_ == "transcript" and message.get("transcriptType") == "final") or type_ == 'transcript[transcriptType="final"]':
            observe_transcript(db, call, message.get("role", ""), message.get("transcript", ""))
        if type_ == "end-of-call-report" or (type_ == "status-update" and message.get("status") == "ended"):
            end_call(db, call)
        db.add(Event(tenant_id=loc.tenant_id, location_id=loc.id, source="vapi", source_event_id=source_id, type=f"call.{type_}", payload={"call_id": call.id, "status": call.status}))
        return {"status": "processed"}




@app.get("/api/v1/realtime")
async def realtime(location_id: str, request: Request, user=Depends(identity)):
    with Session() as db:
        get_location(db, location_id, user)
    async def stream():
        last = object()  # Send initial state even for a newly created location.
        while not await request.is_disconnected():
            with Session() as db:
                latest = db.scalar(select(Event.id).where(Event.location_id == location_id, Event.tenant_id == user["tenant_id"]).order_by(Event.created_at.desc(), Event.id).limit(1))
            if latest != last:
                last = latest
                yield f"event: state\ndata: {json.dumps({'event_id': latest})}\n\n"
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    # Next.js compression would otherwise buffer these tiny, long-lived events.
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store, no-transform"})


@app.post("/api/v1/voice/livekit/session")
def livekit_session(body: s.VoiceSession, user=Depends(identity)):
    require_role(user, "host")
    cfg = settings()
    if not all([cfg.livekit_url, cfg.livekit_api_key, cfg.livekit_api_secret, cfg.agent_api_token]):
        raise DomainError("NOT_CONFIGURED", "Configure LiveKit and the agent API credential first", 503)
    from livekit.api import AccessToken, VideoGrants, RoomConfiguration, RoomAgentDispatch
    with transaction() as db:
        loc = get_location(db, body.location_id, user)
        if loc.tenant_id != DEMO_TENANT or loc.id not in [RESTAURANT, SALON]:
            raise DomainError("NOT_CONFIGURED", "Local worker credentials are scoped to the demo locations", 503)
        room_name = f"frontdesk-{uid()}"
        call = Call(tenant_id=loc.tenant_id, location_id=loc.id, provider="livekit", provider_call_id=room_name, caller_masked="LiveKit web call")
        db.add(call)
        db.flush()
        start_retention(db, call, loc)
        metadata = json.dumps({"call_id": call.id})
        token = (AccessToken(cfg.livekit_api_key, cfg.livekit_api_secret).with_identity(f"guest-{uid()}").with_ttl(timedelta(minutes=10)).with_grants(VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True, can_publish_data=False)).with_room_config(RoomConfiguration(agents=[RoomAgentDispatch(agent_name="frontdesk-local", metadata=metadata)])).to_jwt())
        emit(db, loc, "call.livekit_session_created", {"call_id": call.id}, user["id"], call.id)
        return {"token": token, "url": cfg.livekit_url, "call_id": call.id}



def agent_call(db, id_, request):
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not secret_matches(supplied, settings().agent_api_token):
        raise DomainError("UNAUTHENTICATED_AGENT", "Agent credential is invalid", 401)
    call = db.get(Call, id_)
    if not call or call.provider != "livekit" or call.tenant_id != DEMO_TENANT or call.location_id not in [RESTAURANT, SALON]:
        raise DomainError("NOT_FOUND", "Authorized agent call not found", 404)
    return call


@app.get("/api/v1/voice/agent/calls/{id_}")
def read_agent_call(id_: str, request: Request):
    with Session() as db:
        call = agent_call(db, id_, request)
        notice = "This call is transcribed for service review. You can ask me not to save the transcript." if call.transcript_status == 'retained' else ""
        return {"call_id": call.id, "status": call.status, "location_id": call.location_id, "prompt": AGENT_PROMPT, "transcript_notice": notice, "tools": TOOLS}



@app.post("/api/v1/voice/agent/calls/{id_}/tools")
def agent_tool(id_: str, body: s.ToolRequest, request: Request):
    if body.name == "commit_action":
        with Session() as db:
            agent_call(db, id_, request)
        from .confirmation_intent import resolve
        resolve(id_, Session)
    with transaction() as db:
        return execute_tool(db, agent_call(db, id_, request), body.name, body.arguments, body.request_id)



@app.post("/api/v1/voice/agent/calls/{id_}/transcript")
def agent_transcript(id_: str, body: s.TranscriptEvent, request: Request):
    with transaction() as db:
        call = agent_call(db, id_, request)
        key = f"{call.id}:{body.event_id}"
        if db.scalar(select(Event.id).where(Event.source == "livekit", Event.source_event_id == key)):
            return {"status": "already_processed"}
        if body.interrupted:
            from .transcripts import capture
            capture(db, call, body.role, body.text, interrupted=True)
            for action in db.scalars(select(PendingAction).where(PendingAction.call_id == call.id, PendingAction.state == "awaiting_confirmation")):
                interrupt_readback(action)
        else:
            observe_transcript(db, call, body.role, body.text)
        db.add(Event(tenant_id=call.tenant_id, location_id=call.location_id, source="livekit", source_event_id=key, type="call.transcript_processed", payload={"call_id": call.id, "role": body.role, "interrupted": body.interrupted}))
        return {"status": "processed"}




@app.post("/api/v1/voice/agent/calls/{id_}/end")
def agent_end(id_: str, request: Request):
    with transaction() as db:
        call = agent_call(db, id_, request)
        end_call(db, call)
        emit(db, db.get(Location, call.location_id), "call.ended", {"call_id": call.id})
        return {"status": "ended"}


@app.post("/api/v1/voice/ollama/session")
def ollama_session(body: s.VoiceSession, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        loc = get_location(db, body.location_id, user)
        call = Call(tenant_id=loc.tenant_id, location_id=loc.id, provider="ollama_text", provider_call_id=uid(), caller_masked="Ollama text test · no audio")
        db.add(call)
        db.flush()
        emit(db, loc, "call.local_text_started", {"call_id": call.id}, user["id"], call.id)
        return {"call_id": call.id, "model": settings().ollama_model}


@app.post("/api/v1/voice/ollama/{id_}/chat")
def ollama_turn(id_: str, body: s.ChatTurn, user=Depends(identity)):
    require_role(user, "host")
    with Session() as db:
        call = owned(db, Call, id_, user)
        if call.provider != "ollama_text":
            raise DomainError("INVALID_SESSION", "Session must be an Ollama text experiment", 422)
    from .ollama import run_chat
    return run_chat(id_, body.text, factory=Session)


@app.post("/api/v1/voice/ollama/{id_}/end")
def end_ollama(id_: str, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        call = owned(db, Call, id_, user)
        if call.provider != "ollama_text":
            raise DomainError("INVALID_SESSION", "Session must be an Ollama text experiment", 422)
        end_call(db, call)
        emit(db, db.get(Location, call.location_id), "call.local_text_ended", {"call_id": call.id}, user["id"], call.id)
    from .ollama import chats
    chats.pop(id_, None)
    return {"status": "ended"}


@app.get("/api/v1/customers")
def customers(location_id: str, user=Depends(identity)):
    require_role(user, "host")
    with Session() as db:
        loc = get_location(db, location_id, user)
        bookings = scoped_list(db, Reservation, loc)
        orders = scoped_list(db, Order, loc)
        customer_ids = {r.customer_id for r in [*bookings, *orders]}
        rows = list(db.scalars(select(Customer).where(Customer.tenant_id == loc.tenant_id, Customer.id.in_(customer_ids)).order_by(Customer.display_name))) if customer_ids else []
        result = []
        for row in rows:
            visits = [r for r in [*bookings, *orders] if r.customer_id == row.id]
            result.append({"id": row.id, "display_name": row.display_name, "phone_masked": f"•••• {row.phone[-4:]}" if row.phone else "Not provided", "booking_count": sum(r.customer_id == row.id for r in bookings), "order_count": sum(r.customer_id == row.id for r in orders), "last_activity_at": max(utc(r.created_at) for r in visits).isoformat(), "created_at": utc(row.created_at).isoformat()})
        return result


@app.patch("/api/v1/customers/{id_}")
def update_customer(id_: str, body: s.CustomerPatch, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        row = owned(db, Customer, id_, user)
        row.display_name = body.display_name
        if body.phone is not None:
            row.phone = body.phone
        locations = set(db.scalars(select(Reservation.location_id).where(Reservation.customer_id == row.id, Reservation.tenant_id == row.tenant_id))) | set(db.scalars(select(Order.location_id).where(Order.customer_id == row.id, Order.tenant_id == row.tenant_id)))
        for id_ in locations:
            emit(db, get_location(db, id_, user), "customer.updated", {"customer_id": row.id}, user["id"], row.id)
        return {"id": row.id, "display_name": row.display_name, "phone_masked": f"•••• {row.phone[-4:]}" if row.phone else "Not provided"}


@app.get("/api/v1/attention")
def attention_queue(location_id: str, user=Depends(identity)):
    require_role(user, "host")
    from .domain import resources, table_needs_verification
    with Session() as db:
        loc = get_location(db, location_id, user)
        resolved = set(db.scalars(select(AuditEvent.target_id).where(AuditEvent.tenant_id == loc.tenant_id, AuditEvent.location_id == loc.id, AuditEvent.action == "attention.reviewed")))
        result = []
        for row in resources(db, loc):
            if row.kind == "table" and table_needs_verification(loc, row):
                result.append({"id": f"table:{row.id}", "kind": "stale_state", "title": f"Verify table {row.name}", "description": f"Current staff state is {row.state.replace('_', ' ')}. Verify it before committing availability.", "created_at": utc(row.verified_at).isoformat(), "source": "staff_update", "resource_id": row.id, "can_approve": False, "can_reject": False})
        for row in scoped_list(db, ToolExecution, loc):
            target = f"tool:{row.id}"
            if row.status == "failed" and target not in resolved:
                result.append({"id": target, "kind": "failed_action", "title": row.name.replace("_", " ").capitalize(), "description": row.result.get("message", "Business action needs staff review"), "created_at": utc(row.created_at).isoformat(), "source": "backend_check", "call_id": row.call_id, "error_code": row.result.get("code"), "can_approve": False, "can_reject": True})
        for row in scoped_list(db, Event, loc):
            target = f"event:{row.id}"
            if target in resolved:
                continue
            if row.type in ["capacity.review_required", "provider.pending_confirmation", "integration.sync_failed"]:
                result.append({"id": target, "kind": "capacity_review" if row.type == "capacity.review_required" else "integration_failure", "title": f"Seating arrangement for {row.payload['party_size']} guests" if row.type == "capacity.review_required" else "Provider action needs synchronization", "description": row.payload.get("reason") or row.payload.get("error_code") or "Provider write is awaiting authoritative confirmation. Synchronize before retrying.", "created_at": utc(row.created_at).isoformat(), "source": row.payload.get("provider", "staff_update"), "call_id": row.payload.get("call_id"), "party_size": row.payload.get("party_size"), "can_approve": False, "can_reject": True, "arrangement_allowed": row.type == "capacity.review_required" and loc.settings.get("booking_provider", "internal") in ["internal", "staff_requests"]})
            elif row.type == "call.escalation_requested" or row.type == "integration.tested" and row.payload.get("mode") in ["degraded", "needs_attention"]:
                result.append({"id": target, "kind": "staff_followup" if row.type.startswith("call") else "integration_failure", "title": "Staff assistance requested" if row.type.startswith("call") else f"{row.payload.get('provider', 'Provider').capitalize()} connection needs attention", "description": row.payload.get("reason") or row.payload.get("error_code") or "Review the associated record", "created_at": utc(row.created_at).isoformat(), "source": "voice_agent" if row.type.startswith("call") else "connected_system", "call_id": row.payload.get("call_id"), "can_approve": False, "can_reject": True})
        for row in scoped_list(db, PendingAction, loc):
            if row.state == "awaiting_confirmation" and utc(row.expires_at) > now():
                call = db.get(Call, row.call_id) if row.call_id else None
                if call and call.status != "in_progress":
                    continue
                result.append({"id": f"action:{row.id}", "kind": "pending_approval", "title": row.kind.replace("_", " ").capitalize(), "description": row.readback, "created_at": utc(row.created_at).isoformat(), "source": "voice_agent" if call else "staff_update", "call_id": row.call_id, "can_approve": not call or row.confirmed_at is not None, "can_reject": True, "confirmation_required": bool(call and not row.confirmed_at), "expires_at": utc(row.expires_at).isoformat()})
        return sorted(result, key=lambda item: item["created_at"], reverse=True)



@app.post("/api/v1/attention/{target_id}/decision")
def review_attention(target_id: str, body: s.AttentionDecision, user=Depends(identity)):
    require_role(user, "host")
    kind, _, record_id = target_id.partition(":")
    with transaction() as db:
        model = {"tool": ToolExecution, "event": Event, "action": PendingAction}.get(kind)
        if not model:
            raise DomainError("INVALID_REVIEW", "Verify table states using floor controls", 422)
        row = owned(db, model, record_id, user)
        loc = get_location(db, row.location_id, user)
        if kind == "event" and row.type == "provider.pending_confirmation":
            unresolved = any(a.result and a.result.get("external_id") == row.payload.get("external_id") and a.result.get("provider") == row.payload.get("provider") for a in scoped_list(db, PendingAction, loc) if a.state == "pending_confirmation")
            if unresolved:
                raise DomainError("PENDING_PROVIDER_CONFIRMATION", "Synchronize the provider to verify its write before resolving this request")
        result = {"status": "reviewed", "decision": body.decision}
        if kind == "action":
            if row.state != "awaiting_confirmation" or utc(row.expires_at) <= now():
                raise DomainError("ACTION_EXPIRED", "This proposal is no longer awaiting approval")
            if body.decision == "approve":
                if not row.call_id:
                    confirm_staff(db, row)
                # Voice proposals retain the customer-confirmation and active-call
                # requirements. Staff approval cannot invent customer consent.
                result = commit(db, loc, row)
            elif body.decision == "reject":
                row.state = "rejected"
                result = {"status": "rejected"}
            else:
                raise DomainError("INVALID_REVIEW", "Approve or reject the proposal", 422)
        elif body.decision == "approve":
            raise DomainError("NO_PENDING_TRANSACTION", "Use a new manual booking to complete this request", 422)
        emit(db, loc, "attention.reviewed", {"decision": body.decision, "target": target_id}, user["id"], target_id)
        return result



@app.post("/api/v1/integrations/{provider}/connect")
def connect_integration(provider: str, body: s.IntegrationConnect, user=Depends(identity)):
    require_role(user)
    from .integration_service import connect
    with transaction() as db:
        loc = get_location(db, body.location_id, user)
        return connect(db, loc, provider, body.booking_authority, body.order_authority, body.scenario)


@app.post("/api/v1/integrations/{provider}/sync")
def sync_integration(provider: str, location_id: str, user=Depends(identity)):
    require_role(user)
    from .integration_service import connection, sync, describe
    with transaction() as db:
        loc = get_location(db, location_id, user)
        conn = connection(db, loc, provider)
        if not conn:
            raise DomainError("NOT_CONFIGURED", "Select/import a provider first", 422)
        sync(db, loc, conn)
        return describe(db, loc, conn)


@app.post("/api/v1/integrations/{provider}/mock-events")
def ingest_mock_event(provider: str, body: MockIntegrationEvent, user=Depends(identity)):
    require_role(user)
    from .integration_service import require_adapter, context, apply_record
    from .integration_normalization import normalize
    from pydantic import ValidationError
    with transaction() as db:
        loc = get_location(db, body.location_id, user)
        adapter, conn = require_adapter(db, loc, provider, require_fresh=False)
        if loc.mode != "demo":
            raise DomainError("MOCK_REQUIRES_DEMO", "Mock events are restricted to demo locations", 422)
        key = f"{loc.id}:{body.event_id}"
        source = f"mock:{provider}"
        if db.scalar(select(Event.id).where(Event.source == source, Event.source_event_id == key, Event.tenant_id == loc.tenant_id)):
            return {"status": "already_processed"}
        if utc(body.observed_at) > now() + timedelta(seconds=30):
            raise DomainError("INVALID_EVENT_TIME", "Future event timestamps are not accepted", 422)
        try:
            data = normalize(provider, body)
        except ValidationError:
            raise DomainError("INVALID_PROVIDER_PAYLOAD", "Provider event schema is invalid", 422)
        remote = next((r for r in adapter.rows(db, context(loc, conn), body.kind) if r.external_id == body.external_id), None)
        late = remote is not None and utc(remote.observed_at) > utc(body.observed_at)
        if not late:
            if not remote:
                remote = adapter.add(db, context(loc, conn), body.kind, body.external_id, data)
            remote.data, remote.observed_at = data, body.observed_at
            apply_record(db, loc, conn, {"kind": body.kind, "external_id": body.external_id, "data": data, "observed_at": body.observed_at})
        db.add(Event(tenant_id=loc.tenant_id, location_id=loc.id, source=source, source_event_id=key, type="integration.event_received", occurred_at=body.observed_at, payload={"provider": provider, "kind": body.kind, "external_id": body.external_id, "late": late}))
        return {"status": "ignored_older_event" if late else "processed", "full_sync_refreshed": False}


@app.post("/api/v1/integrations/{provider}/disconnect")
def disconnect_integration(provider: str, location_id: str, user=Depends(identity)):
    require_role(user)
    from .integration_service import connection, choose_authority
    with transaction() as db:
        loc = get_location(db, location_id, user)
        conn = connection(db, loc, provider)
        if not conn:
            raise DomainError("NOT_FOUND", "Connection not found", 404)
        for key in ["booking_provider", "order_provider"]:
            if loc.settings.get(key) == provider:
                choose_authority(db, loc, key, "internal")
        loc.settings = {k: v for k, v in loc.settings.items() if k not in ["floor_provider", "catalog_provider"] or v != provider}
        conn.status = "disconnected"
        emit(db, loc, "integration.disconnected", {"provider": provider}, user["id"])
        return {"status": "disconnected"}


@app.post("/api/v1/locations/{location_id}/booking-authority")
def set_booking_authority(location_id: str, body: s.AuthoritySelection, user=Depends(identity)):
    require_role(user)
    from .integration_service import require_adapter, choose_authority
    with transaction() as db:
        loc = get_location(db, location_id, user)
        for key, provider in body.model_dump().items():
            if provider not in ["internal", "staff_requests"]:
                require_adapter(db, loc, provider, "orders_write" if key == "order_provider" else "bookings_write")
            if key == "order_provider" and provider != "internal" and loc.settings.get("catalog_provider") != provider:
                raise DomainError("CATALOG_PROVIDER_CONFLICT", "Import the order provider's menu before selecting it")
            choose_authority(db, loc, key, provider)
        return serialize(loc)


@app.post("/api/v1/capacity-requests")
def staff_booking_request(body: s.StaffBookingRequest, user=Depends(identity)):
    require_role(user, "host")
    from .integration_service import request_staff_review
    with transaction() as db:
        return request_staff_review(db, get_location(db, body.location_id, user), body)


@app.post("/api/v1/capacity-requests/{id_}/arrangement")
def verify_arrangement(id_: str, body: s.SeatingArrangementReview, user=Depends(identity)):
    require_role(user, "host")
    with transaction() as db:
        event = owned(db, Event, id_, user)
        loc = get_location(db, event.location_id, user)
        if event.type != "capacity.review_required" or loc.settings.get("booking_provider", "internal") not in ["internal", "staff_requests"]:
            raise DomainError("PROVIDER_ARRANGEMENT_REQUIRED", "Manage seating arrangements in the configured booking provider", 422)
        tables = [owned(db, Resource, id_, user) for id_ in body.resource_ids]
        if len(set(body.resource_ids)) != len(body.resource_ids) or any(t.location_id != loc.id or t.kind != "table" or t.state == "out_of_service" for t in tables) or sum(t.capacity for t in tables) < event.payload["party_size"]:
            raise DomainError("INVALID_ARRANGEMENT", "Select usable tables at this location that fit the requested party", 422)
        groups = loc.settings.get("combinations", [])
        if len(tables) > 1 and not any(set(group) == set(body.resource_ids) for group in groups):
            groups = [*groups, body.resource_ids]
        loc.settings = {**loc.settings, "combinations": groups, "arrangement_verifications": {**loc.settings.get("arrangement_verifications", {}), "+".join(sorted(body.resource_ids)): {"actor": user["id"], "verified_at": now().isoformat(), "request_id": event.id}}}
        emit(db, loc, "attention.reviewed", {"decision": "arrangement_verified", "target": f"event:{event.id}", "resource_ids": body.resource_ids}, user["id"], f"event:{event.id}")
        return {"status": "arrangement_verified", "booking_confirmed": False, "message": "Arrangement saved. Table-state verification and a confirmed booking are still required."}

