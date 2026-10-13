"""Deterministic capacity and confirmation logic, shared by UI/Vapi/LiveKit.

The single business-rule engine (the LLM only proposes). Flow: `availability`/`slot`
find resources (honouring hours, table combos, floor-state freshness, conflicts) ->
`prepare` stores a server-held PendingAction with a readback -> `observe_transcript` /
`confirm_staff` set `confirmed_at` -> `commit` re-validates and writes atomically.
Functions take a SQLAlchemy session and must run inside `db.transaction()` for writes.
Statuses: AVAILABLE / NO_CAPACITY / UNKNOWN (stale or unverified state: never assume free).
"""
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, or_
from .models import Resource, Reservation, Allocation, Service, MenuItem, KitchenState, Order, Customer, PendingAction, WaitlistEntry, Event, AuditEvent, Call, now
from .schemas import Availability, PrepareAction, QuoteRequest


class DomainError(Exception):
    """Business-rule failure: stable `code`, human message, HTTP `status`, optional retryable flag and availability `decision`."""
    def __init__(self, code, message, status=409, retryable=False, decision=None):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable
        self.decision = decision
        super().__init__(message)


def utc(value):
    """Coerce an ISO string/naive/aware datetime to aware UTC (naive is assumed UTC)."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def emit(db, loc, type_, payload=None, actor="system", target=""):
    """Record an operational Event and matching AuditEvent in the caller's transaction."""
    event = Event(tenant_id=loc.tenant_id, location_id=loc.id, type=type_, payload=payload or {})
    db.add(event)
    db.add(AuditEvent(tenant_id=loc.tenant_id, location_id=loc.id, actor=actor, action=type_, target_id=target or loc.id, detail=payload or {}))


def resources(db, loc):
    """Location resources; tables are limited to the active floor source (manual vs `floor_provider`)."""
    rows = list(db.scalars(select(Resource).where(Resource.tenant_id == loc.tenant_id, Resource.location_id == loc.id).order_by(Resource.name)))
    provider = loc.settings.get("floor_provider")
    return [r for r in rows if r.kind != "table" or (r.attributes.get("source_provider") == provider if provider else not r.attributes.get("source_provider"))]


def business_open(loc, start, end):
    """True if [start, end] fits inside configured open/close hours in the location's timezone (0-24 = always open)."""
    zone = ZoneInfo(loc.timezone)
    local_start, local_end = utc(start).astimezone(zone), utc(end).astimezone(zone)
    opening, closing = loc.settings.get("open_hour", 9), loc.settings.get("close_hour", 22)
    if opening == 0 and closing == 24:
        return local_end > local_start  # Continuous 24-hour service spans midnight.
    midnight = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
    close_at = midnight + timedelta(hours=closing)
    return local_start >= midnight + timedelta(hours=opening) and local_end <= close_at


def freshness(loc, verified, *, staff_managed=False):
    """True if staff-verified state is recent enough (`freshness_minutes`), or persistent when staff-managed."""
    if staff_managed and loc.settings.get("staff_state_persistent", False):
        return True
    return (now() - utc(verified)).total_seconds() <= loc.settings.get("freshness_minutes", 30) * 60


def conflict(db, loc, ids, start, end, exclude=None):
    """True if any resource id has an overlapping live allocation; `exclude` ignores one reservation (reschedule)."""
    # Half-open overlap test: existing.start < end and existing.end > start.
    query = select(Allocation.id).join(Reservation, Reservation.id == Allocation.reservation_id).where(
        Allocation.tenant_id == loc.tenant_id, Allocation.resource_id.in_(ids),
        Reservation.location_id == loc.id, Reservation.status.in_(["confirmed", "seated", "pending_confirmation"]),
        Allocation.start_at < end, Allocation.end_at > start,
    )
    if exclude:
        query = query.where(Reservation.id != exclude)
    return db.scalar(query.limit(1)) is not None


def table_sets(db, loc, party):
    """Candidate table groups seating `party`: single tables plus configured combinations, smallest first."""
    tables = [r for r in resources(db, loc) if r.kind == "table"]
    by_id = {r.id: r for r in tables}
    candidates = [[r] for r in tables if r.capacity >= party]
    for group in loc.settings.get("combinations", []):
        if len(group) > 1 and len(group) == len(set(group)) and all(id_ in by_id for id_ in group):
            group_tables = [by_id[id_] for id_ in group]
            if sum(r.capacity for r in group_tables) >= party:
                candidates.append(group_tables)
    return sorted(candidates, key=lambda rs: (sum(r.capacity for r in rs), len(rs), tuple(r.id for r in rs)))


def table_needs_verification(loc, resource):
    """True if a table's state cannot be trusted (staff-unverified, stale, or contradicted by a newer POS signal)."""
    if resource.attributes.get("staff_verified") is False:
        return True
    staff_managed = not loc.settings.get("floor_provider") and not resource.attributes.get("source_provider")
    if not freshness(loc, resource.verified_at, staff_managed=staff_managed):
        return True
    # A newer open POS check conflicts with an earlier staff-free observation.
    # It requests verification; it does not establish occupied or free status.
    if resource.state == "available" and resource.attributes.get("pos_signal") == "check_open" and resource.attributes.get("signal_observed_at") and utc(resource.attributes["signal_observed_at"]) > utc(resource.verified_at):
        return True
    return False


def release_window(loc, resource):
    """(earliest, latest) time a table is expected free, or None when unknown/unavailable. Never assumes an overdue table left."""
    if resource.state in ["out_of_service", "held"] or table_needs_verification(loc, resource):
        return None
    current = now()
    if resource.state == "available":
        return current, current
    cleaning = loc.settings.get("cleaning_minutes", 10)
    if resource.state == "cleaning":
        release = utc(resource.verified_at) + timedelta(minutes=cleaning)
        if current >= release + timedelta(minutes=5):
            return None  # An overdue cleaning state needs an explicit staff update.
        return max(current + timedelta(minutes=1), release), max(current + timedelta(minutes=1), release + timedelta(minutes=5))
    if resource.state == "seated" and resource.seated_at:
        base = utc(resource.seated_at) + timedelta(minutes=loc.settings.get("turn_minutes", 75) + cleaning)
        if current >= base + timedelta(minutes=10):
            return None  # Still seated after the forecast: never assume departure.
        return max(current + timedelta(minutes=1), base - timedelta(minutes=10)), max(current + timedelta(minutes=1), base + timedelta(minutes=10))
    return None


def slot(db, loc, query, exclude=None):
    """Find a free resource group for `query`.

    Returns (group | None, end_at, reason). reason is OUTSIDE_HOURS, STALE_STATE (some table state unknown)
    or NO_COMPATIBLE_RESOURCE. Restaurants use turn+cleaning time and table release windows; salons use
    service duration+buffer, stylist shifts and blocked ranges.
    """
    start = utc(query.start_at)
    if loc.vertical == "restaurant":
        duration = loc.settings.get("turn_minutes", 75) + loc.settings.get("cleaning_minutes", 10)
        candidates = table_sets(db, loc, query.party_size)
    else:
        service = db.get(Service, query.service_id) if query.service_id else None
        if not service or service.tenant_id != loc.tenant_id or service.location_id != loc.id or not service.enabled:
            raise DomainError("INVALID_SERVICE", "Choose an enabled service at this location", 422)
        duration = service.duration_min + service.buffer_min
        candidates = [[r] for r in resources(db, loc) if r.kind == "stylist" and r.id in service.eligible_staff and (not query.staff_id or query.staff_id == r.id)]
    end = start + timedelta(minutes=duration)
    if not business_open(loc, start, end):
        return None, end, "OUTSIDE_HOURS"
    unknown = False
    for group in candidates:
        usable = True
        for r in group:
            if r.state in ["out_of_service", "held"]:
                usable = False
            if loc.vertical == "restaurant":
                window = release_window(loc, r)
                if window is None:
                    unknown = unknown or (r.state not in ["out_of_service", "held"])
                    usable = False
                elif start < window[1] - timedelta(seconds=5):
                    usable = False
            else:
                local = start.astimezone(ZoneInfo(loc.timezone))
                midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
                attrs = r.attributes
                if not (start >= utc(midnight + timedelta(hours=attrs.get("shift_start", 9))) and end <= utc(midnight + timedelta(hours=attrs.get("shift_end", 18)))):
                    usable = False
                for block in attrs.get("blocked", []):
                    if utc(block["start_at"]) < end and utc(block["end_at"]) > start:
                        usable = False
        if usable and not conflict(db, loc, [r.id for r in group], start, end, exclude):
            return group, end, None
    return None, end, "STALE_STATE" if unknown else "NO_COMPATIBLE_RESOURCE"


def local_slot_labels(loc, result):
    """Voice-ready business times alongside canonical timestamps; never reinterpret UTC as local."""
    result = {**result, "timezone": loc.timezone}
    for key in ["start_at", "end_at"]:
        if not result.get(key):
            continue
        local = utc(result[key]).astimezone(ZoneInfo(loc.timezone))
        hour = local.hour % 12 or 12
        clock = f"{hour}:{local.minute:02d}" if local.minute else str(hour)
        result[f"local_{key}"] = local.isoformat()
        result[f"{key}_label"] = f"{local.strftime('%A, %B')} {local.day} at {clock} {'AM' if local.hour < 12 else 'PM'} {local.tzname()}"
    result["alternatives"] = [local_slot_labels(loc, option) for option in result.get("alternatives", [])]
    return result


def availability(db, loc, query, exclude=None, alternatives=True, force_internal=False):
    if query.search == "earliest":
        return earliest_availability(db, loc, query, exclude, force_internal)
    return local_slot_labels(loc, _availability(db, loc, query, exclude, alternatives, force_internal))


def earliest_availability(db, loc, query, exclude=None, force_internal=False):
    """Find the first slot on a 15-minute grid, using the exact-check authority.

    An unknown earlier slot prevents claiming that a later slot is the earliest.
    The full service duration must fit the requested window.
    """
    start = utc(query.start_at)
    if query.end_at is None:
        raise DomainError("SEARCH_WINDOW_REQUIRED", "Specify the end of the requested booking window", 422)
    end = utc(query.end_at)
    if end <= start or end - start > timedelta(days=1):
        raise DomainError("INVALID_SEARCH_WINDOW", "Choose a search window of up to 24 hours", 422)
    if start < now() - timedelta(seconds=30):
        raise DomainError("PAST_TIME", "Choose a future search window", 422)
    if start > now() + timedelta(days=90):
        raise DomainError("BOOKING_HORIZON", "Bookings can be made up to 90 days ahead", 422)
    cursor, checked, result = start, 0, None
    while cursor < end:
        candidate = query.model_copy(update={"start_at": cursor, "search": "exact", "end_at": None})
        result = availability(db, loc, candidate, exclude, alternatives=False, force_internal=force_internal)
        checked += 1
        if result["status"] == "AVAILABLE" and utc(result["end_at"]) <= end:
            return {**result, "search": "earliest", "search_start_at": start.isoformat(), "search_end_at": end.isoformat(), "slot_interval_minutes": 15, "slots_checked": checked}
        if result["status"] == "UNKNOWN" or "INTAKE_PAUSED" in result.get("reason_codes", []):
            return {**result, "search": "earliest", "slots_checked": checked}
        cursor += timedelta(minutes=15)
    return {"status": "NO_CAPACITY", "reason_codes": ["NO_SLOT_IN_WINDOW"], "alternatives": [], "timezone": loc.timezone, "search": "earliest", "search_start_at": start.isoformat(), "search_end_at": end.isoformat(), "slot_interval_minutes": 15, "slots_checked": checked, "evidence": result.get("evidence", {}) if result else {}, "permitted_actions": ["request_staff_booking"]}


def _availability(db, loc, query, exclude=None, alternatives=True, force_internal=False):
    """Availability decision dict: status, reason_codes, alternatives (up to 3, +15..240 min), evidence, permitted_actions.

    Raises PAST_TIME / BOOKING_HORIZON. Delegates to the provider when booking authority is external unless
    `force_internal`. `exclude` is a reservation id being moved.
    """
    start = utc(query.start_at)
    if start < now() - timedelta(seconds=30):
        raise DomainError("PAST_TIME", "Choose a future time", 422)
    if start > now() + timedelta(days=90):
        raise DomainError("BOOKING_HORIZON", "Bookings can be made up to 90 days ahead", 422)
    evidence = {"sources": ["manual_floor" if loc.vertical == "restaurant" else "staff_schedule", "internal_reservations"], "as_of": now().isoformat(), "confidence": "medium", "mode": loc.mode}
    if loc.settings.get("intake_paused"):
        return {"status": "NO_CAPACITY", "reason_codes": ["INTAKE_PAUSED"], "alternatives": [], "evidence": evidence, "permitted_actions": ["request_staff_callback"]}
    if loc.vertical == "restaurant" and loc.settings.get("booking_provider", "internal") != "internal" and not force_internal:
        from .integration_service import provider_availability
        return provider_availability(db, loc, query, exclude)
    group, end, reason = slot(db, loc, query, exclude)
    if group:
        return {"status": "AVAILABLE", "start_at": start.isoformat(), "end_at": end.isoformat(), "resource_ids": [r.id for r in group], "resources": [r.name for r in group], "reason_codes": [], "alternatives": [], "evidence": evidence, "permitted_actions": ["prepare_action"]}
    options = []
    if alternatives and reason != "STALE_STATE":
        for offset in range(15, 241, 15):
            candidate = query.model_copy(update={"start_at": start + timedelta(minutes=offset)})
            found, candidate_end, _ = slot(db, loc, candidate, exclude)
            if found:
                options.append({"start_at": candidate.start_at.isoformat(), "end_at": candidate_end.isoformat(), "resources": [r.name for r in found]})
            if len(options) == 3:
                break
    needs_arrangement = loc.vertical == "restaurant" and query.party_size > 8 and reason == "NO_COMPATIBLE_RESOURCE"
    return {"status": "UNKNOWN" if reason == "STALE_STATE" or needs_arrangement else "NO_CAPACITY", "reason_codes": ["STAFF_ARRANGEMENT_REQUIRED" if needs_arrangement else reason], "alternatives": options, "evidence": {**evidence, "confidence": "low"}, "permitted_actions": ["offer_alternative", "join_waitlist", "request_staff_booking"]}


def seating_wait(db, loc, party):
    """Estimate restaurant wait as a min/max range by simulating the waitlist queue against table release windows.

    Returns UNKNOWN rather than guessing when state is stale or no reliable release time exists.
    """
    if loc.vertical != "restaurant":
        raise DomainError("WRONG_VERTICAL", "Seating wait is only available for restaurants", 422)
    if loc.settings.get("intake_paused"):
        return {"status": "NO_CAPACITY", "reason_codes": ["INTAKE_PAUSED"]}
    queue = list(db.scalars(select(WaitlistEntry).where(WaitlistEntry.location_id == loc.id, WaitlistEntry.tenant_id == loc.tenant_id, WaitlistEntry.status == "waiting").order_by(WaitlistEntry.created_at)))
    if any((now() - utc(w.created_at)) > timedelta(hours=3) for w in queue):
        return {"status": "UNKNOWN", "reason_codes": ["STALE_WAITLIST"], "permitted_actions": ["request_staff_callback"]}
    # Run two simulations with optimistic/conservative release windows. Queue entries
    # claim compatible resources before this caller; seat count alone is never used.
    sets = table_sets(db, loc, party)
    reliable = any(all(release_window(loc, r) is not None for r in group) for group in sets)
    if not reliable:
        return {"status": "UNKNOWN", "reason_codes": ["NO_RELIABLE_RELEASE_STATE"], "permitted_actions": ["request_staff_callback"]}
    estimates = []
    for bound in (0, 1):
        simulated = {}
        final = None
        for size in [w.party_size for w in queue] + [party]:
            candidates = []
            for group in table_sets(db, loc, size):
                windows = [release_window(loc, r) for r in group]
                if any(w is None for w in windows):
                    continue
                earliest = max([w[bound] for w in windows] + [simulated.get(r.id, now()) for r in group])
                duration = timedelta(minutes=loc.settings.get("turn_minutes", 75) + loc.settings.get("cleaning_minutes", 10))
                for step in range(0, loc.settings.get("max_wait_minutes", 180) + 1, 5):
                    candidate = earliest + timedelta(minutes=step)
                    if business_open(loc, candidate, candidate + duration) and not conflict(db, loc, [r.id for r in group], candidate, candidate + duration):
                        candidates.append((candidate, group))
                        break
            if not candidates:
                final = None
                break
            final, chosen = min(candidates, key=lambda entry: entry[0])
            for r in chosen:
                simulated[r.id] = final + duration
        if final is None:
            return {"status": "NO_CAPACITY", "reason_codes": ["MAX_WAIT_EXCEEDED"]}
        estimates.append(max(0, math.ceil((final - now()).total_seconds() / 300) * 5))
    return {"status": "AVAILABLE_NOW" if estimates[1] == 0 else "ESTIMATED_WAIT", "wait_minutes": {"min": estimates[0], "max": max(estimates)}, "evidence": {"sources": ["manual_floor", "configured_turn_time", "waitlist", "internal_reservations"], "confidence": "medium" if estimates[0] == estimates[1] else "low", "as_of": now().isoformat(), "mode": loc.mode}, "permitted_actions": ["join_waitlist", "request_staff_callback"]}


def quote(db, loc, request):
    """Price a takeout cart and pick a 15-minute kitchen bucket with capacity (tax in basis points, minor units).

    Raises DomainError for unknown/paused kitchen, invalid items/modifiers, or no capacity within 2 hours.
    No writes; `commit` re-quotes to detect drift.
    """
    if loc.vertical != "restaurant":
        raise DomainError("WRONG_VERTICAL", "Takeout is only supported at restaurant locations", 422)
    if loc.settings.get("catalog_provider"):
        from .integration_service import require_adapter
        require_adapter(db, loc, loc.settings["catalog_provider"], "menu_read")
    kitchen = db.scalar(select(KitchenState).where(KitchenState.location_id == loc.id, KitchenState.tenant_id == loc.tenant_id))
    if not kitchen or not freshness(loc, kitchen.verified_at, staff_managed=True):
        raise DomainError("UNKNOWN_KITCHEN", "Kitchen state needs staff verification")
    if loc.settings.get("intake_paused") or not kitchen.kitchen_open or not kitchen.accepting_takeout:
        raise DomainError("KITCHEN_PAUSED", "The kitchen is not accepting takeout")
    if not request.items:
        raise DomainError("EMPTY_CART", "Choose at least one menu item", 422)
    items, subtotal, units = [], 0, 0
    from .integration_service import catalog_ids
    selected_catalog = catalog_ids(db, loc)
    for line in request.items:
        item = db.get(MenuItem, line.item_id)
        if selected_catalog is not None and line.item_id not in selected_catalog:
            raise DomainError("CATALOG_CHANGED", "Choose an item from the selected provider catalog", 422)
        if not item or item.tenant_id != loc.tenant_id or item.location_id != loc.id or not item.available:
            raise DomainError("ITEM_UNAVAILABLE", "A selected menu item is unavailable", 422)
        modifiers = {m["id"]: m for m in item.modifiers}
        if len(set(line.modifiers)) != len(line.modifiers) or any(m not in modifiers for m in line.modifiers):
            raise DomainError("INVALID_MODIFIER", "Choose modifiers from the menu only", 422)
        selected = [modifiers[m] for m in line.modifiers]
        price = item.price_minor + sum(m["price_minor"] for m in selected)
        subtotal += price * line.quantity
        units += item.work_units * line.quantity
        items.append({"item_id": item.id, "name": item.name, "quantity": line.quantity, "modifiers": selected, "unit_minor": price, "total_minor": price * line.quantity})
    if units > kitchen.capacity_per_bucket:
        raise DomainError("CART_TOO_LARGE", "This order exceeds a single pickup window's capacity")
    current = now() + timedelta(minutes=kitchen.delay_min + 15)
    bucket = datetime.fromtimestamp(math.ceil(current.timestamp() / 900) * 900, timezone.utc)
    orders = list(db.scalars(select(Order).where(Order.location_id == loc.id, Order.tenant_id == loc.tenant_id, Order.state.in_(["accepted", "preparing", "ready", "pending_confirmation"]))))
    # Overdue unfinished work occupies current/future buckets before new work.
    backlog = sum(o.work_units for o in orders if utc(o.bucket_at) < bucket and o.state in ["accepted", "preparing", "pending_confirmation"])
    for offset in range(0, 121, 15):
        candidate = bucket + timedelta(minutes=offset)
        queued = sum(o.work_units for o in orders if utc(o.bucket_at) == candidate)
        carry = min(backlog, max(0, kitchen.capacity_per_bucket - queued))
        backlog -= carry
        if units + queued + carry <= kitchen.capacity_per_bucket and business_open(loc, now(), candidate + timedelta(minutes=10)):
            tax = (subtotal * loc.settings.get("tax_bps", 0) + 5000) // 10000
            return {"status": "QUOTED", "items": items, "subtotal_minor": subtotal, "tax_minor": tax, "total_minor": subtotal + tax, "currency": loc.settings.get("currency", "USD"), "work_units": units, "bucket_at": candidate.isoformat(), "pickup_min": (candidate + timedelta(minutes=5)).isoformat(), "pickup_max": (candidate + timedelta(minutes=10)).isoformat(), "evidence": {"sources": ["internal_menu", "manual_kitchen", "capacity_buckets"], "confidence": "medium", "as_of": now().isoformat(), "mode": loc.mode}}
    raise DomainError("NO_KITCHEN_CAPACITY", "No pickup window is available within two hours")


def prepare(db, loc, request, call_id=None):
    """Validate a request and create a PendingAction (5-minute expiry) with the spoken readback.

    Handles reservation/appointment/order/cancel/reschedule. Change requests require matching reference and
    phone. Supersedes earlier awaiting proposals on the same call. Nothing is booked here.
    """
    from .phone import normalize_phone
    try:
        request = request.model_copy(update={"phone": normalize_phone(request.phone, loc.settings.get("phone_country_code"))})
    except ValueError as error:
        raise DomainError("PHONE_FORMAT_REQUIRED", str(error), 422) from error
    # Live adapters are exposed separately; never silently make an internal booking
    # for a location configured to use an external authoritative provider.
    provider_key = "order_provider" if request.kind == "order" else "booking_provider"
    authority = loc.settings.get(provider_key, "internal")
    if authority not in ["internal", "staff_requests"]:
        from .integration_service import require_adapter
        require_adapter(db, loc, authority, "orders_write" if request.kind == "order" else "bookings_write")
    if authority == "staff_requests" and call_id:
        raise DomainError("STAFF_APPROVAL_REQUIRED", "Log a staff booking request; do not create an automatic reservation")
    if request.kind in ["cancel_booking", "reschedule_booking"]:
        if not request.booking_reference or not request.phone:
            raise DomainError("VERIFICATION_REQUIRED", "Provide the booking reference and matching contact phone", 422)
        matches = list(db.scalars(select(Reservation).join(Customer).where(Reservation.tenant_id == loc.tenant_id, Reservation.location_id == loc.id, or_(Reservation.id.like(request.booking_reference.lower() + "%"), Reservation.external_id == request.booking_reference), Customer.phone == request.phone, Customer.tenant_id == loc.tenant_id)))
        if len(matches) != 1:
            raise DomainError("VERIFICATION_FAILED", "Booking reference or phone could not be verified", 404)
        record = matches[0]
        if record.status != "confirmed" or record.provider != ("internal" if authority == "staff_requests" else authority):
            raise DomainError("CHANGE_NOT_PERMITTED", "Only confirmed bookings in the configured authoritative system can be changed")
        customer = db.get(Customer, record.customer_id)
        expected = {"booking_id": record.id, "expected_start_at": utc(record.start_at).isoformat(), "expected_end_at": utc(record.end_at).isoformat()}
        if request.kind == "cancel_booking":
            decision = expected
            summary = f"cancel booking {record.id[:8].upper()} for {customer.display_name} on {utc(record.start_at).astimezone(ZoneInfo(loc.timezone)).strftime('%A %B %d at %I:%M %p')}."
        else:
            if not request.start_at:
                raise DomainError("TIME_REQUIRED", "Choose a replacement time", 422)
            if record.kind == "appointment":
                if request.service_id and request.service_id != record.service_id:
                    raise DomainError("SERVICE_CHANGED", "Staff assistance is required to change the service", 422)
                request = request.model_copy(update={"service_id": record.service_id})
            decision = availability(db, loc, Availability(location_id=loc.id, start_at=request.start_at, party_size=request.party_size, service_id=request.service_id, staff_id=request.staff_id), exclude=record.id, force_internal=authority == "staff_requests")
            if decision["status"] != "AVAILABLE":
                raise DomainError("NO_CAPACITY", "Replacement slot is unavailable. Check alternatives")
            decision.update(expected)
            summary = f"move booking {record.id[:8].upper()} for {customer.display_name} to {utc(request.start_at).astimezone(ZoneInfo(loc.timezone)).strftime('%A %B %d at %I:%M %p')} with {', '.join(decision['resources'])}."
    elif request.kind == "order":
        decision = quote(db, loc, QuoteRequest(location_id=loc.id, items=request.items))
        zone = ZoneInfo(loc.timezone)
        pickup = utc(decision["pickup_min"]).astimezone(zone).strftime("%I:%M %p")
        detail = ", ".join(f"{i['quantity']} {i['name']}" + (" with " + ", ".join(m["name"] for m in i["modifiers"]) if i["modifiers"] else "") for i in decision["items"])
        summary = f"{detail} for {request.name}, total {decision['total_minor'] / 100:.2f} {decision['currency']}, pickup {pickup} to {utc(decision['pickup_max']).astimezone(zone).strftime('%I:%M %p')}."
    else:
        if (request.kind == "reservation") != (loc.vertical == "restaurant") or not request.start_at:
            raise DomainError("INVALID_ACTION", "Action and time must match the location's vertical", 422)
        decision = availability(db, loc, Availability(location_id=loc.id, start_at=request.start_at, party_size=request.party_size, service_id=request.service_id, staff_id=request.staff_id), force_internal=authority == "staff_requests" and not call_id)
        if decision["status"] != "AVAILABLE":
            unknown = decision["status"] == "UNKNOWN"
            raise DomainError("AVAILABILITY_UNKNOWN" if unknown else "NO_CAPACITY", "Seating availability needs staff verification; no booking was created" if unknown else "That time is unavailable; offer the returned alternatives", decision=decision)
        local = utc(request.start_at).astimezone(ZoneInfo(loc.timezone))
        clock = f"{local.hour % 12 or 12}" + (f":{local.minute:02}" if local.minute else "") + local.strftime(" %p")
        when = f"{local.strftime('%A, %B')} {local.day} at {clock}"
        detail = f"table for {request.party_size}" if request.kind == "reservation" else db.get(Service, request.service_id).name
        summary = f"{detail} for {request.name} on {when}."
    action = PendingAction(tenant_id=loc.tenant_id, location_id=loc.id, call_id=call_id, kind=request.kind, payload={"request": request.model_dump(mode="json"), "decision": decision, "authority": authority}, readback=f"Please confirm: {summary}", expires_at=now() + timedelta(minutes=5))
    db.add(action)
    db.flush()
    # Supersede earlier proposals; an affirmative always applies to just this proposal.
    if call_id:
        for old in db.scalars(select(PendingAction).where(PendingAction.call_id == call_id, PendingAction.id != action.id, PendingAction.state == "awaiting_confirmation")):
            from .confirmation_intent import discard
            discard(old)
            old.state = "superseded"
    emit(db, loc, "action.prepared", {"action_id": action.id, "kind": action.kind}, target=action.id)
    return {"action_id": action.id, "status": action.state, "readback": action.readback, "next_action": "Read readback once, then commit_action after the caller agrees. Do not prepare again unless details change.", "expires_at": action.expires_at.isoformat(), "decision": decision}


def normalize(text):
    """Lowercase and strip everything but a-z0-9 for lenient comparison."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def speech_key(text):
    """Tolerate transcription of numbers, never changes to the proposal's terms."""
    text = text.lower()
    cardinals = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    ordinals = "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split()
    replacements = {word: str(i) for words in [cardinals, ordinals] for i, word in enumerate(words)}
    for tens, value in [("twenty", 20), ("thirty", 30), ("forty", 40), ("fifty", 50)]:
        replacements[tens] = str(value)
        for unit in range(1, 10):
            replacements[f"{tens} {cardinals[unit]}"] = str(value + unit)
            replacements[f"{tens} {ordinals[unit]}"] = str(value + unit)
    pattern = r"\b(?:" + "|".join(re.escape(word).replace(r"\ ", r"[\s-]+") for word in sorted(replacements, key=len, reverse=True)) + r")\b"
    text = re.sub(pattern, lambda m: replacements[re.sub(r"[\s-]+", " ", m[0])], text)
    # TTS/transcribers expand USD to "US dollars". Treat these as the same
    # currency, without dropping currency or changing the authoritative amount.
    text = re.sub(r"\b(?:u\.?\s*s\.?\s*dollars?|united states dollars?|usd)\b", "usd", text)
    # Only normalize monetary phrases after 'total'; item names stay literal.
    # Forty dollars and twenty-eight cents == 40.28 USD, not 40.028/40.82.
    def spoken_total(match):
        dollars, cents = match.group(1), match.group(2)
        return f"total {int(dollars)}.{int(cents):02d} usd"
    text = re.sub(r"\btotal\s+(\d+)\s+(?:usd|dollars?)\s+(?:and\s+)?(\d{1,2})\s+cents?\b", spoken_total, text)
    text = re.sub(r"\btotal\s+(\d+(?:\.\d{1,2})?)\s+dollars?\b", r"total \1 usd", text)
    def minor_total(match):
        dollars, _, cents = match.group(1).partition('.')
        return f"total {int(dollars) * 100 + int(cents.ljust(2, '0') or '0')} usd"
    # Preserve leading cent zeros before generic clock/date zero normalization.
    text = re.sub(r"\btotal\s+(\d+(?:\.\d{1,2})?)\s+usd\b", minor_total, text)
    text = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"\b(\d{1,2}):00\s*(?=[ap][.\s]*m\b)", r"\1 ", text)
    text = re.sub(r"\b0+(\d+)", r"\1", text)
    return normalize(text)


def explicit_agreement(text, kind=None):
    """True only if the whole utterance is a closed-grammar agreement (see below); questions never qualify."""
    # A closed grammar accepts natural/repeated agreement, never arbitrary speech
    # containing "yes", corrections, conditions, or questions.
    if "?" in text:
        return False
    clauses = ["yes", "yeah", "yep", "yesplease", "yesconfirm", "correct", "right", "confirm", "confirmed", "thatiscorrect", "thatscorrect", "thatisright", "thatsright", "soundsright", "soundsgood", "okay", "ok", "sure", "iwanttobook", "iwanttobookit", "iwannabook", "iwannabookit", "bookit", "pleasebookit", "goahead", "goaheadandbookit", "proceed", "pleaseproceed", "proceedwiththat"]
    commands = {"reservation": ["bookthetable", "bookthereservation", "confirmthereservation"], "appointment": ["booktheappointment", "confirmtheappointment"], "order": ["placetheorder", "pleaseplacetheorder", "confirmtheorder"]}
    clauses += commands.get(kind, []) if kind else [phrase for phrases in commands.values() for phrase in phrases]
    return re.fullmatch("(?:" + "|".join(clauses) + ")+", normalize(text)) is not None


def confirmation_help(text):
    """True if the caller is asking how to confirm (not agreeing)."""
    return normalize(text) in {"howdoiconfirm", "howshouldiconfirm", "whatshouldisay", "whatdoisay", "doiconfirmyes", "anddoiconfirmyes", "doijustsayyes", "howmanytimesmusticonfirm"}


def reset_readback(action):
    """Discard confirmation progress so the readback must be heard in full again."""
    from .confirmation_intent import discard
    discard(action)
    action.readback_at, action.confirmed_at = None, None
    action.payload = {**action.payload, "readback_progress": 0, "readback_states": [], "confirmation": {}}


def interrupt_readback(action):
    """Handle caller barge-in: keep a completed readback but clear consent, otherwise restart the readback."""
    # A caller can barge in with "yes" after the full readback. An interruption
    # alone is never consent, and incomplete readbacks must start over.
    if action.readback_at:
        from .confirmation_intent import record
        action.confirmed_at = None
        record(action, "unclear", "interrupted")
    else:
        reset_readback(action)


def observe_transcript(db, call, role, text):
    """Use authenticated provider final transcripts, never a tool's confirmed flag.

    Require the entire prepared readback across consecutive assistant final segments,
    followed by an unambiguous agreement turn. Clear replies take a fast path;
    unfamiliar replies can be interpreted outside this transaction at commit time.
    Speech stays transient when retention is off; sanitized decision metadata persists.
    """
    from .transcripts import capture
    capture(db, call, role, text)
    if call.status != "in_progress":
        return
    action = db.scalar(select(PendingAction).where(PendingAction.call_id == call.id, PendingAction.state == "awaiting_confirmation").order_by(PendingAction.created_at.desc()))
    if not action or utc(action.expires_at) <= now():
        return
    if role == "assistant" and not action.readback_at:
        from .speech_confirmation import match_segment
        if match_segment(action, text, speech_key):
            action.readback_at = now()
    elif role == "user":
        from .confirmation_intent import record, stage, veto
        from .config import settings
        action.confirmed_at = None
        if not action.readback_at:
            reset_readback(action)
            record(action, "readback_incomplete", "policy")
        elif explicit_agreement(text, action.kind) and veto(text, action.kind) is None:
            action.confirmed_at = now()
            record(action, "confirm", "fast_path")
        elif confirmation_help(text):
            record(action, "question", "policy")
        else:
            blocked = veto(text, action.kind)
            if blocked in ["change", "reject"]:
                reset_readback(action)
            if blocked:
                record(action, blocked, "policy_veto")
            elif settings().ollama_confirmation_enabled:
                stage(action, text)
            else:
                record(action, "unclear", "model_disabled")


def confirm_staff(db, action):
    """Staff-side confirmation: mark an unexpired awaiting action confirmed (bypasses the transcript check)."""
    if action.state != "awaiting_confirmation" or utc(action.expires_at) <= now():
        raise DomainError("ACTION_EXPIRED", "Prepare a fresh action before confirming")
    action.confirmed_at = now()


def commit(db, loc, action):
    """Execute a confirmed PendingAction; idempotent (a committed action returns its stored result).

    Guards, in order: not expired/superseded, confirmed_at set, call still live and AI action not paused,
    provider authority unchanged. Then re-checks capacity/quote/booking state at write time and raises
    CAPACITY_CHANGED / RESOURCE_CHANGED / QUOTE_CHANGED / BOOKING_CHANGED if it drifted since the readback.
    Writes the booking/order + Allocation rows, updates the Call and emits an event.
    """
    if action.state in ["committed", "pending_confirmation"]:
        return action.result  # Idempotent replay.
    if action.state != "awaiting_confirmation" or utc(action.expires_at) <= now():
        raise DomainError("ACTION_EXPIRED", "The proposal has expired or was superseded")
    # Confirmation guard: only confirmed_at (set from authenticated transcript or staff) permits a write.
    if not action.confirmed_at:
        raise DomainError("CONFIRMATION_REQUIRED", "Waiting for customer confirmation of the readback", retryable=True)
    if action.call_id:
        call = db.get(Call, action.call_id)
        if not call or call.status != "in_progress":
            raise DomainError("CALL_ENDED", "A disconnected call cannot commit a new action")
        capability = "takeout" if action.kind == "order" else "booking_changes" if action.kind in ["cancel_booking", "reschedule_booking"] else "bookings"
        if not loc.settings.get("ai_actions", {}).get(capability, True):
            raise DomainError("AI_ACTION_PAUSED", "Staff have paused this AI action. Request staff assistance")
    request = PrepareAction.model_validate(action.payload["request"])
    provider_key = "order_provider" if action.kind == "order" else "booking_provider"
    authority = loc.settings.get(provider_key, "internal")
    if action.payload.get("authority", "internal") != authority:
        raise DomainError("PROVIDER_CHANGED", "Provider selection changed; prepare and confirm a fresh action")
    if authority not in ["internal", "staff_requests"]:
        from .integration_service import commit_provider
        return commit_provider(db, loc, action, request)
    if authority == "staff_requests" and action.call_id:
        raise DomainError("STAFF_APPROVAL_REQUIRED", "A staff member must manage this booking request")
    if action.kind in ["cancel_booking", "reschedule_booking"]:
        prior = action.payload["decision"]
        record = db.get(Reservation, prior["booking_id"])
        if not record or record.tenant_id != loc.tenant_id or record.location_id != loc.id or record.status != "confirmed" or utc(record.start_at).isoformat() != prior["expected_start_at"] or utc(record.end_at).isoformat() != prior["expected_end_at"]:
            raise DomainError("BOOKING_CHANGED", "Booking changed since the proposal. Verify and confirm a fresh action")
        if action.kind == "cancel_booking":
            record.status = "cancelled"
            response = {"status": "cancelled", "id": record.id, "reference": record.id[:8].upper(), "kind": record.kind, "provider": "internal", "mode": loc.mode}
        else:
            result = availability(db, loc, Availability(location_id=loc.id, start_at=request.start_at, party_size=request.party_size, service_id=request.service_id, staff_id=request.staff_id), exclude=record.id, alternatives=False, force_internal=authority == "staff_requests")
            if result["status"] != "AVAILABLE" or result["resource_ids"] != prior["resource_ids"]:
                raise DomainError("CAPACITY_CHANGED", "Replacement slot changed. Prepare and confirm a fresh proposal")
            for allocation in db.scalars(select(Allocation).where(Allocation.reservation_id == record.id, Allocation.tenant_id == loc.tenant_id)):
                db.delete(allocation)
            record.start_at, record.end_at = utc(result["start_at"]), utc(result["end_at"])
            record.party_size = request.party_size if record.kind == "reservation" else 1
            for id_ in result["resource_ids"]:
                db.add(Allocation(tenant_id=loc.tenant_id, reservation_id=record.id, resource_id=id_, start_at=record.start_at, end_at=record.end_at))
            response = {"status": "confirmed", "id": record.id, "reference": record.id[:8].upper(), "kind": record.kind, "start_at": result["start_at"], "resources": result["resources"], "provider": "internal", "mode": loc.mode}
        action.state, action.result = "committed", response
        if action.call_id:
            call = db.get(Call, action.call_id)
            call.outcome, call.intent = "fulfilled", action.kind
        emit(db, loc, "reservation.cancelled" if action.kind == "cancel_booking" else "reservation.rescheduled", {"id": record.id, "call_id": action.call_id}, target=record.id)
        return response
    if action.kind == "order":
        result = quote(db, loc, QuoteRequest(location_id=loc.id, items=request.items))
        prior = action.payload["decision"]
        # Price/pickup drift invalidates the readback, so do not silently change it.
        if any(result[k] != prior[k] for k in ["items", "total_minor", "bucket_at", "pickup_min", "pickup_max"]):
            raise DomainError("QUOTE_CHANGED", "Price or pickup time changed. Prepare and confirm a fresh quote")
        customer = Customer(tenant_id=loc.tenant_id, display_name=request.name, phone=request.phone)
        db.add(customer)
        db.flush()
        record = Order(tenant_id=loc.tenant_id, location_id=loc.id, customer_id=customer.id, items=result["items"], total_minor=result["total_minor"], work_units=result["work_units"], bucket_at=utc(result["bucket_at"]), pickup_min=utc(result["pickup_min"]), pickup_max=utc(result["pickup_max"]), idempotency_key=action.id, call_id=action.call_id)
        db.add(record)
        db.flush()
        response = {"status": "accepted", "id": record.id, "reference": record.id[:8].upper(), "kind": "order", "pickup_min": result["pickup_min"], "pickup_max": result["pickup_max"], "total_minor": record.total_minor, "provider": "internal", "mode": loc.mode}
    else:
        query = Availability(location_id=loc.id, start_at=request.start_at, party_size=request.party_size, service_id=request.service_id, staff_id=request.staff_id)
        result = availability(db, loc, query, alternatives=False, force_internal=authority == "staff_requests")
        if result["status"] != "AVAILABLE":
            raise DomainError("CAPACITY_CHANGED", "The requested slot is no longer available")
        if result["resource_ids"] != action.payload["decision"]["resource_ids"]:
            raise DomainError("RESOURCE_CHANGED", "The assigned resource changed. Prepare a fresh proposal")
        customer = Customer(tenant_id=loc.tenant_id, display_name=request.name, phone=request.phone)
        db.add(customer)
        db.flush()
        record = Reservation(tenant_id=loc.tenant_id, location_id=loc.id, customer_id=customer.id, kind=action.kind, start_at=utc(result["start_at"]), end_at=utc(result["end_at"]), party_size=request.party_size if action.kind == "reservation" else 1, service_id=request.service_id, idempotency_key=action.id, call_id=action.call_id)
        db.add(record)
        db.flush()
        for id_ in result["resource_ids"]:
            db.add(Allocation(tenant_id=loc.tenant_id, reservation_id=record.id, resource_id=id_, start_at=record.start_at, end_at=record.end_at))
        response = {"status": "confirmed", "id": record.id, "reference": record.id[:8].upper(), "kind": action.kind, "start_at": result["start_at"], "resources": result["resources"], "provider": "internal", "mode": loc.mode}
    action.state, action.result = "committed", response
    if action.call_id:
        call = db.get(Call, action.call_id)
        call.outcome, call.intent = "fulfilled", action.kind
    emit(db, loc, f"{action.kind}.created", {"id": record.id, "call_id": action.call_id, "provider": "internal", "evidence": result.get("evidence", {})}, target=record.id)
    return response


def lookup_order(db, loc, reference, phone):
    """Return order status only when reference and normalized phone match exactly one order (else VERIFICATION_FAILED)."""
    from sqlalchemy import or_
    from .phone import normalize_phone
    try:
        phone = normalize_phone(phone, loc.settings.get("phone_country_code"))
    except ValueError as error:
        raise DomainError("PHONE_FORMAT_REQUIRED", str(error), 422) from error
    matches = list(db.scalars(select(Order).join(Customer).where(Order.tenant_id == loc.tenant_id, Order.location_id == loc.id, or_(Order.id.like(reference.lower() + "%"), Order.external_id == reference), Customer.phone == phone, Customer.tenant_id == loc.tenant_id)))
    if len(matches) != 1:
        raise DomainError("VERIFICATION_FAILED", "Order reference or phone could not be verified", 404)
    row = matches[0]
    if row.provider != "internal":
        from .integration_service import require_adapter
        require_adapter(db, loc, row.provider, "orders_read")
    return {"status": row.state, "reference": row.external_id or row.id[:8].upper(), "pickup_min": utc(row.pickup_min).isoformat(), "pickup_max": utc(row.pickup_max).isoformat(), "provider": row.provider, "mode": loc.mode}
