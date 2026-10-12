import json
from pydantic import ValidationError
from sqlalchemy import select
from . import schemas as s
from .domain import DomainError, availability, quote, seating_wait, prepare, commit, emit, lookup_order
from .models import Location, PendingAction, MenuItem, Service, ToolExecution, WaitlistEntry, now
from .serialization import serialize


TOOLS = [
    {"name": "get_business_info", "description": "Read business-local date/time, hours, policies and status.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_menu_or_services", "description": "Read actual menu/modifiers or salon services and qualified staff.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "check_availability", "description": "Check a future time. Use explicit ISO date/time with timezone offset; never invent a slot.", "parameters": {"type": "object", "properties": {"start_at": {"type": "string"}, "party_size": {"type": "integer"}, "service_id": {"type": "string"}, "staff_id": {"type": "string"}}, "required": ["start_at"], "additionalProperties": False}},
    {"name": "get_wait_estimate", "description": "Get seating wait estimate with range/provenance, or unknown.", "parameters": {"type": "object", "properties": {"party_size": {"type": "integer", "minimum": 1, "maximum": 30}}, "required": ["party_size"], "additionalProperties": False}},
    {"name": "check_order_capacity", "description": "Price a catalog cart and check kitchen pickup capacity. Does not submit an order.", "parameters": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"item_id": {"type": "string"}, "quantity": {"type": "integer"}, "modifiers": {"type": "array", "items": {"type": "string"}}}, "required": ["item_id", "quantity"], "additionalProperties": False}}}, "required": ["items"], "additionalProperties": False}},
    {"name": "prepare_action", "description": "Prepare reservation/appointment/order after collecting details. Read readback EXACTLY; wait for caller yes. Does not commit.", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["reservation", "appointment", "order"]}, "name": {"type": "string"}, "phone": {"type": "string"}, "start_at": {"type": "string"}, "party_size": {"type": "integer"}, "service_id": {"type": "string"}, "staff_id": {"type": "string"}, "items": {"type": "array", "items": {"type": "object", "properties": {"item_id": {"type": "string"}, "quantity": {"type": "integer"}, "modifiers": {"type": "array", "items": {"type": "string"}}}, "required": ["item_id", "quantity"], "additionalProperties": False}}}, "required": ["kind", "name"], "additionalProperties": False}},
    {"name": "commit_action", "description": "Commit only after the caller confirms the prepared readback. Backend verifies authenticated final transcript. If waiting, retry with a NEW request ID after transcript delivery. Never report pending as successful.", "parameters": {"type": "object", "properties": {"action_id": {"type": "string"}}, "required": ["action_id"], "additionalProperties": False}},
    {"name": "join_waitlist", "description": "Join restaurant waitlist after asking caller permission. Does not promise a seating time.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "party_size": {"type": "integer"}}, "required": ["name", "party_size"], "additionalProperties": False}},
    {"name": "transfer_to_human", "description": "Request staff escalation; this creates an attention item, not a successful phone transfer. Explain the returned status.", "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"], "additionalProperties": False}},
]

# Creation and changes use the same server-held proposal and confirmation guard.
prepare_definition = next(t for t in TOOLS if t["name"] == "prepare_action")
prepare_definition["parameters"]["properties"]["kind"]["enum"] += ["cancel_booking", "reschedule_booking"]
prepare_definition["parameters"]["properties"]["booking_reference"] = {"type": "string", "description": "Customer's booking reference; required with matching phone for changes"}
prepare_definition["description"] += " For cancel_booking/reschedule_booking, collect the reference and contact phone for verification, then read back and commit as usual."
TOOLS.append({"name": "get_order_status", "description": "Look up a customer's order using its reference and matching contact phone. Do not disclose an unverified order.", "parameters": {"type": "object", "properties": {"reference": {"type": "string"}, "phone": {"type": "string"}}, "required": ["reference", "phone"], "additionalProperties": False}})

AGENT_PROMPT = """You are this business's automated front desk. Introduce yourself as an automated assistant. Use get_business_info first for the business-local time and policies. Use backend tools for every availability, menu, price and status claim. Never invent table capacity, pickup estimates or booking success. Use explicit dates and timezone offsets. For bookings/orders: collect details, including a contact phone if the customer wants future lookup/changes, call prepare_action, say the entire returned readback EXACTLY, and wait for an unambiguous yes from the caller. Then call commit_action. A confirmed/accepted result and reference are required before you say it is booked/accepted. If CONFIRMATION_REQUIRED, explain you are checking and retry once after final transcript delivery, then escalate. For cancellation/rescheduling, collect booking_reference and matching phone; prepare_action kind cancel_booking/reschedule_booking verifies identity and creates a new readback. If verification fails or no contact phone exists, request staff assistance. If a proposal changes, prepare and read back again. A pause or unknown state blocks new commitments. Never collect cards or assure allergen safety. A transfer request is pending unless the actual phone transfer succeeds. Do not claim a callback was scheduled without consent."""


def execute_tool(db, call, name, args, request_id):
    previous = db.scalar(select(ToolExecution).where(ToolExecution.call_id == call.id, ToolExecution.request_id == request_id))
    if previous:
        result = dict(previous.result)
        if previous.name == "prepare_action" and result.get("action_id"):
            action = db.get(PendingAction, result["action_id"])
            if action and action.call_id == call.id:
                result["readback"] = action.readback
        return result
    loc = db.get(Location, call.location_id)
    if not loc or loc.tenant_id != call.tenant_id:
        raise DomainError("FORBIDDEN", "Call location is invalid", 403)
    if call.status != "in_progress":
        raise DomainError("CALL_ENDED", "Call has already ended")
    try:
        with db.begin_nested():
            action_kind = args.get("kind") if name == "prepare_action" else None
            if name == "commit_action":
                pending = db.get(PendingAction, args.get("action_id", ""))
                action_kind = pending.kind if pending and pending.call_id == call.id else None
            capability = ("takeout" if action_kind == "order" else "booking_changes" if action_kind in ["cancel_booking", "reschedule_booking"] else "bookings" if action_kind in ["reservation", "appointment"] else "waitlist" if name == "join_waitlist" else None)
            if capability and not loc.settings.get("ai_actions", {}).get(capability, True):
                raise DomainError("AI_ACTION_PAUSED", f"Staff have paused AI {capability.replace('_', ' ')}. Request staff assistance")
            data = {**args, "location_id": loc.id}
            if name in ["get_business_info", "get_operating_status"]:
                from zoneinfo import ZoneInfo
                result = {"name": loc.name, "vertical": loc.vertical, "timezone": loc.timezone, "local_time": now().astimezone(ZoneInfo(loc.timezone)).isoformat(), "policies": loc.settings, "mode": loc.mode}
            elif name == "get_menu_or_services":
                model = MenuItem if loc.vertical == "restaurant" else Service
                result = {"items": [serialize(row) for row in db.scalars(select(model).where(model.location_id == loc.id, model.tenant_id == loc.tenant_id))], "mode": loc.mode}
            elif name == "check_availability":
                result = availability(db, loc, s.Availability.model_validate(data))
            elif name == "get_wait_estimate":
                party = int(args.get("party_size", 2))
                if not 1 <= party <= 30:
                    raise DomainError("INVALID_PARTY", "Party size must be between 1 and 30", 422)
                result = seating_wait(db, loc, party)
            elif name == "check_order_capacity":
                result = quote(db, loc, s.QuoteRequest.model_validate(data))
            elif name == "prepare_action":
                result = prepare(db, loc, s.PrepareAction.model_validate(data), call.id)
            elif name == "get_order_status":
                request = s.LookupRequest.model_validate(args)
                result = lookup_order(db, loc, request.reference, request.phone)
            elif name == "commit_action":
                request = s.CommitAction.model_validate(args)
                action = db.get(PendingAction, request.action_id)
                if not action or action.call_id != call.id or action.tenant_id != call.tenant_id or action.location_id != loc.id:
                    raise DomainError("NOT_FOUND", "Action not found for this call", 404)
                result = commit(db, loc, action)
            elif name == "join_waitlist":
                if loc.vertical != "restaurant":
                    raise DomainError("WRONG_VERTICAL", "Waitlist only available at restaurants", 422)
                request = s.WaitlistCreate.model_validate(data)
                estimate = seating_wait(db, loc, request.party_size)
                row = WaitlistEntry(tenant_id=loc.tenant_id, location_id=loc.id, name=request.name, party_size=request.party_size, estimate_min=estimate.get("wait_minutes", {}).get("min"), estimate_max=estimate.get("wait_minutes", {}).get("max"))
                db.add(row)
                db.flush()
                result = {"status": "waiting", "id": row.id, "estimate": estimate}
                emit(db, loc, "waitlist.joined", {"id": row.id}, target=row.id)
            elif name == "transfer_to_human":
                reason = str(args.get("reason", "Staff assistance requested"))[:300]
                emit(db, loc, "call.escalation_requested", {"call_id": call.id, "reason": reason}, target=call.id)
                call.outcome = "escalated" if call.outcome != "fulfilled" else call.outcome
                result = {"status": "staff_followup_required", "message": "Staff assistance requested. No phone transfer has been completed.", "transfer_phone_configured": bool(loc.settings.get("transfer_phone"))}
            else:
                raise DomainError("TOOL_NOT_ENABLED", "This tool is not enabled; ask staff for assistance", 422)
    except DomainError as error:
        result = {"status": "failed", "code": error.code, "message": error.message, "retryable": error.retryable}
    except (ValidationError, ValueError, TypeError):
        result = {"status": "failed", "code": "INVALID_ARGUMENTS", "message": "Tool arguments did not match the required schema", "retryable": False}
    # Do not retain contact details or freeform customer text in tool logs.
    safe_args = {k: v for k, v in args.items() if k not in ["name", "phone", "reason"]}
    safe_result = {k: v for k, v in result.items() if k != "readback"}
    row = ToolExecution(tenant_id=call.tenant_id, location_id=call.location_id, call_id=call.id, request_id=request_id, name=name, args=safe_args, result=safe_result, status="failed" if result.get("status") == "failed" else "succeeded")
    # Results must be replayable for idempotency. Redaction for dashboard reads is
    # applied by API; prepared readback contains only booking-required identity.
    db.add(row)
    if call.intent == "unclassified":
        call.intent = name
    return result
