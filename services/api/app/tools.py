import json
from pydantic import ValidationError
from sqlalchemy import select
from . import schemas as s
from .domain import DomainError, availability, quote, seating_wait, prepare, commit, emit, lookup_order, freshness, business_open, utc
from .models import Location, PendingAction, MenuItem, Service, ToolExecution, WaitlistEntry, Resource, KitchenState, now
from .serialization import serialize


TOOLS = [
    {"name": "get_business_info", "description": "Read business-local date/time, hours, policies and status.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_operating_status", "description": "Explain what is known and what needs staff verification: opening hours, intake pause, floor and kitchen freshness. Use for status questions, not a guessed food cart.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_menu_or_services", "description": "Read actual menu/modifiers or salon services and qualified staff.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "check_availability", "description": "Check a future time. Use explicit ISO date/time with timezone offset; never invent a slot.", "parameters": {"type": "object", "properties": {"start_at": {"type": "string"}, "party_size": {"type": "integer"}, "service_id": {"type": "string"}, "staff_id": {"type": "string"}}, "required": ["start_at"], "additionalProperties": False}},
    {"name": "get_wait_estimate", "description": "Get seating wait estimate with range/provenance, or unknown.", "parameters": {"type": "object", "properties": {"party_size": {"type": "integer", "minimum": 1, "maximum": 30}}, "required": ["party_size"], "additionalProperties": False}},
    {"name": "check_order_capacity", "description": "Price a catalog cart and check kitchen pickup capacity. Does not submit an order.", "parameters": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"item_id": {"type": "string"}, "quantity": {"type": "integer"}, "modifiers": {"type": "array", "items": {"type": "string"}}}, "required": ["item_id", "quantity"], "additionalProperties": False}}}, "required": ["items"], "additionalProperties": False}},
    {"name": "prepare_action", "description": "Call BEFORE asking for confirmation. Phone is optional for new bookings/orders; omit it if declined. Backend normalizes formatted numbers using the business country. On success read the returned readback once, unchanged, then wait for agreement and commit. Does not book anything itself.", "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["reservation", "appointment", "order"]}, "name": {"type": "string"}, "phone": {"type": "string", "description": "Optional contact phone as provided, e.g. 202 555 0123 for a US business; omit if declined. Required for booking changes."}, "start_at": {"type": "string"}, "party_size": {"type": "integer"}, "service_id": {"type": "string"}, "staff_id": {"type": "string"}, "items": {"type": "array", "items": {"type": "object", "properties": {"item_id": {"type": "string"}, "quantity": {"type": "integer"}, "modifiers": {"type": "array", "items": {"type": "string"}}}, "required": ["item_id", "quantity"], "additionalProperties": False}}}, "required": ["kind", "name"], "additionalProperties": False}},
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
TOOLS.append({"name": "request_staff_booking", "description": "After caller permission, log an uncertain/large-party seating request for staff. This never confirms a reservation, table arrangement, or callback.", "parameters": {"type": "object", "properties": {"party_size": {"type": "integer", "minimum": 1, "maximum": 30}, "start_at": {"type": "string"}, "reason": {"type": "string"}}, "required": ["party_size", "start_at"], "additionalProperties": False}})

AGENT_PROMPT = """You are this business's automated front desk. Introduce yourself as an automated assistant. Use get_business_info first for the business-local date/time and policies. Use backend tools for every availability, menu, price and status claim. Use explicit dates and timezone offsets. Never invent capacity, pickup estimates, or success.

Booking/order sequence — follow this order:
1. If the caller first asks whether a specific time/party is available, check_availability before collecting their name or phone. Explain AVAILABLE, UNKNOWN or NO_CAPACITY accurately; unknown staff state is not a broken reservation system. For a booking, collect the request details and name. Ask for contact phone once; it is OPTIONAL for a new reservation/appointment/order. Pass ordinary digit/space-formatted numbers to the backend as given. If the caller declines, omit phone and continue by name. Do not escalate because phone is omitted. Changes and private order lookups require a matching phone.
2. Call prepare_action BEFORE asking the customer to confirm. Do not invent your own preliminary confirmation/readback. A failed prepare has no action to confirm: explain the specific error and ask only for the missing detail. If phone is invalid, offer to omit it for a new booking; never ask for the same phone repeatedly.
3. On successful prepare_action, speak the returned readback ONCE and unchanged. No phone-number recital or extra summary. Wait for the caller's answer.
4. Interpret agreement by meaning in context, not an exact phrase list. When the caller clearly agrees to proceed with the unchanged readback, call commit_action with the EXISTING action_id immediately. The backend independently interprets authenticated final speech. For example, 'That is right' can express agreement without the word yes. NEVER coach the caller to say a magic phrase or demand the word 'yes'. A correction, condition, refusal, question or ambiguity is not agreement. Do not prepare again or ask another confirmation unless details change. An interruption alone is not a detail change.
5. Only confirmed/accepted tool results allow a success statement. Give the returned reference. Never say 'booked' or promise a follow-up before a successful tool result.
6. On CONFIRMATION_REQUIRED, obey confirmation_state/next_action. Await transcript or interpretation delivery and retry once with a new request ID without asking for another yes. On change_requested, collect only the changed detail and prepare a new proposal; on declined, stop this action. On clarification_required, ask one brief natural question about proceeding; do not repeat the full readback or demand prescribed words. On readback_incomplete, read the returned readback once. If still unclear after one clarification, offer staff assistance once and do not loop.

Cancellation/rescheduling: collect booking_reference and matching phone; prepare_action verifies identity and creates a fresh readback before commit. If verification fails or no phone exists, offer staff assistance. Changed details require a fresh proposal/readback. Pauses and unknown state block commitments. Never collect cards or assure allergen safety. Staff escalation records a request ONLY: no phone transfer or callback is scheduled. Never say staff 'will follow up'."""
AGENT_PROMPT += """
Conversation rules:
- Stay within the selected business. You cannot search or book another restaurant.
- Keep answers brief, ask one question at a time, and do not repeat acknowledgments.
- 'Dine-in availability' means seating: use get_wait_estimate for now, check_availability for a requested date/time. Do not read the menu unless asked for food/menu information.
- A changed party size replaces the previous party size. Clarify an unclear number before checking.
- Never describe a business-rule refusal or unknown availability as an inability to access the reservation system. Explain the returned reason; retry only when the tool marks the failure retryable.
- UNKNOWN means staff data need verification. Explain that briefly; do not present it as a known wait or permanent lack of capacity. Use get_operating_status when asked what is known.
- NO_COMPATIBLE_RESOURCE means the party does not fit configured resources. Explain that staff may need to arrange a larger group; do not keep proposing the same unsupported party size.
- Ambiguous speech is not an order. Ask 'Did you mean a drink order or a question about availability?' when unclear. Never turn water into lemonade, invent an item, or select a substitute without the caller choosing it.
- Offer staff assistance once. If requested, use transfer_to_human once and explain that the request appears in the staff attention queue. Do not promise a direct transfer or callback; neither is implemented.
- If the caller corrects details, prepare a fresh proposal. If the caller simply agrees, commit the existing action instead of restarting the conversation.
"""


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
            if name == "get_operating_status":
                from datetime import timedelta
                instant = now()
                from .domain import resources, table_needs_verification
                from .integration_service import describe
                from .models import IntegrationConnection
                floor = [r for r in resources(db, loc) if r.kind == "table"]
                verified = sum(not table_needs_verification(loc, row) for row in floor)
                kitchen = db.scalar(select(KitchenState).where(KitchenState.location_id == loc.id, KitchenState.tenant_id == loc.tenant_id))
                reliable = bool(kitchen and freshness(loc, kitchen.verified_at))
                result = {"name": loc.name, "mode": loc.mode, "as_of": instant.isoformat(), "timezone": loc.timezone, "scheduled_open": business_open(loc, instant, instant + timedelta(seconds=1)), "intake_paused": loc.settings.get("intake_paused", False), "known_information": ["business_identity", "configured_hours", "catalog_items_and_prices"], "floor": {"source": "staff_update", "table_count": len(floor), "verified_count": verified, "verification_required": verified != len(floor), "availability_requires_capacity_check": True}, "kitchen": {"source": "staff_update", "status": "VERIFIED" if reliable else "UNKNOWN", "verified_at": utc(kitchen.verified_at).isoformat() if kitchen else None, "kitchen_open": kitchen.kitchen_open if reliable else None, "accepting_takeout": kitchen.accepting_takeout if reliable else None, "verification_required": not reliable}}
                result["booking_provider"] = loc.settings.get("booking_provider", "internal")
                result["integrations"] = [describe(db, loc, c) for c in db.scalars(select(IntegrationConnection).where(IntegrationConnection.location_id == loc.id, IntegrationConnection.tenant_id == loc.tenant_id))]
                if loc.settings.get("catalog_provider") and any(c["provider"] == loc.settings["catalog_provider"] and c["sync_status"] != "healthy" for c in result["integrations"]):
                    result["known_information"].remove("catalog_items_and_prices")
            elif name == "get_business_info":
                from zoneinfo import ZoneInfo
                result = {"name": loc.name, "vertical": loc.vertical, "timezone": loc.timezone, "local_time": now().astimezone(ZoneInfo(loc.timezone)).isoformat(), "policies": loc.settings, "mode": loc.mode}
            elif name == "get_menu_or_services":
                model = MenuItem if loc.vertical == "restaurant" else Service
                from .integration_service import catalog_ids, require_adapter
                if model is MenuItem and loc.settings.get("catalog_provider"):
                    require_adapter(db, loc, loc.settings["catalog_provider"], "menu_read")
                ids = catalog_ids(db, loc) if model is MenuItem else None
                result = {"items": [serialize(row) for row in db.scalars(select(model).where(model.location_id == loc.id, model.tenant_id == loc.tenant_id)) if ids is None or row.id in ids], "mode": loc.mode, "source_provider": loc.settings.get("catalog_provider", "internal")}
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
                call.outcome, call.intent = 'fulfilled', 'waitlist'
                emit(db, loc, "waitlist.joined", {"id": row.id, "call_id": call.id}, target=row.id)
            elif name == "transfer_to_human":
                reason = str(args.get("reason", "Staff assistance requested"))[:300]
                emit(db, loc, "call.escalation_requested", {"call_id": call.id, "reason": reason}, target=call.id)
                call.outcome = "escalated" if call.outcome != "fulfilled" else call.outcome
                result = {"status": "staff_followup_required", "message": "Your request is in the staff attention queue. No reservation, phone transfer, or callback has been scheduled.", "callback_scheduled": False, "transfer_completed": False, "transfer_phone_configured": bool(loc.settings.get("transfer_phone"))}
            elif name == "request_staff_booking":
                from .integration_service import request_staff_review
                result = request_staff_review(db, loc, s.StaffBookingRequest.model_validate(data), call)
            else:
                raise DomainError("TOOL_NOT_ENABLED", "This tool is not enabled; ask staff for assistance", 422)
    except DomainError as error:
        result = {"status": "failed", "code": error.code, "message": error.message, "retryable": error.retryable}
        if error.decision is not None:
            result["availability_status"] = error.decision["status"]
            result["reason_codes"] = error.decision.get("reason_codes", [])
            result["alternatives"] = error.decision.get("alternatives", [])
            result["evidence"] = error.decision.get("evidence", {})
            result["next_action"] = "Explain that staff must verify seating availability; this is not a system-access failure. Offer staff assistance once; do not retry unchanged." if error.decision["status"] == "UNKNOWN" else "Explain that the requested time is unavailable. Offer only returned alternatives; do not retry unchanged."
        if error.code == "PAST_TIME":
            from zoneinfo import ZoneInfo
            result["business_local_time"] = now().astimezone(ZoneInfo(loc.timezone)).isoformat()
            result["timezone"] = loc.timezone
            result["next_action"] = "Recalculate the caller's requested relative date using business_local_time, then retry. Do not ask them to repeat an already clear 'tomorrow at 7 PM'."
        if error.code == "CONFIRMATION_REQUIRED" and name == "commit_action" and action.call_id == call.id:
            intent = action.payload.get("confirmation", {}).get("intent")
            if intent in ["change", "reject"]:
                result["confirmation_state"] = "change_requested" if intent == "change" else "declined"
                result["next_action"] = "Collect the changed detail and prepare a new proposal. Do not commit the old action." if intent == "change" else "Stop this action. Nothing was completed; do not ask for confirmation again."
            elif intent in ["unclear", "question"] and action.readback_at:
                attempts = int(action.payload.get("confirmation_clarifications", 0))
                action.payload = {**action.payload, "confirmation_clarifications": attempts + 1}
                result["confirmation_state"] = "clarification_required" if attempts == 0 else "staff_assistance_required"
                result["next_action"] = "Answer any caller question, then ask one short natural clarification about proceeding with this action. Do not repeat the readback or prescribe words." if attempts == 0 else "Do not ask again. Explain that nothing was completed and offer staff assistance once."
            else:
                result["confirmation_state"] = "interpreting_reply" if intent == "interpreting" else "awaiting_customer" if action.readback_at else "readback_incomplete"
                result["next_action"] = "Wait for authenticated final transcript/interpretation delivery, then retry commit once with a new request ID; do not ask the caller again." if action.readback_at else "Speak this readback unchanged, wait for agreement, then commit the same action_id."
                if not action.readback_at:
                    result["readback"] = action.readback

    except ValidationError as error:
        result = {"status": "failed", "code": "INVALID_ARGUMENTS", "message": "Correct only the listed fields; optional phone can be omitted for a new booking/order", "invalid_fields": sorted({str(e["loc"][0]) for e in error.errors() if e["loc"]}), "retryable": False}
    except (ValueError, TypeError):
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
    intent = {"prepare_action": args.get('kind'), "get_wait_estimate": "seating_wait", "join_waitlist": "waitlist", "get_menu_or_services": "menu" if loc.vertical == 'restaurant' else "services", "get_order_status": "order_status", "check_order_capacity": "takeout", "check_availability": "availability", "request_staff_booking": "seating_request"}.get(name)
    if intent and call.outcome != 'fulfilled':
        call.intent = intent
    return result
