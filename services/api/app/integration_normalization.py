"""Validated mock event envelopes; real webhooks need vendor signature adapters.

Different mock payloads exercise the same normalized projection pipeline.
POS payment/check activity deliberately carries no verified occupancy state.

Fit: inbound mock webhook events are wrapped in an Envelope, validated against a
strict per-vendor payload model (extra fields forbidden), and translated by
normalize() into the provider-neutral record `data` shape consumed by
integration_service.apply_record. Invalid payloads raise pydantic validation
errors; unsupported provider/kind pairs raise UNSUPPORTED_CAPABILITY.
"""
from datetime import datetime, timedelta
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .domain import utc, DomainError


class Envelope(BaseModel):
    """Common event wrapper: location, unique event_id (for dedupe), kind, timezone-aware observed_at and raw vendor data."""
    model_config = ConfigDict(extra="forbid")
    location_id: str
    event_id: str = Field(min_length=1, max_length=90)
    external_id: str = Field(min_length=1, max_length=100)
    kind: Literal["reservation", "pos_signal", "menu"]
    observed_at: datetime
    data: dict
    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if not value.tzinfo:
            raise ValueError("Timezone offset required")
        return value


class StrictPayload(BaseModel):
    """Base for vendor payloads; unknown fields are rejected rather than silently ignored."""
    model_config = ConfigDict(extra="forbid")


class OpenTableReservation(StrictPayload):
    """Mock OpenTable reservation webhook body."""
    guest_name: str = Field(min_length=1, max_length=100)
    party_size: int = Field(ge=1, le=30)
    scheduled_at: datetime
    duration_minutes: int = Field(default=90, ge=15, le=240)
    seating_id: str
    status: Literal["confirmed", "cancelled"]
    @field_validator("scheduled_at")
    @classmethod
    def aware(cls, value):
        if not value.tzinfo:
            raise ValueError("Timezone offset required")
        return value


class ToastSignal(StrictPayload):
    """Mock Toast check-state signal; informational only, never proof a table is free or occupied."""
    table_guid: str
    check_state: Literal["OPEN", "CLOSED", "PAID"]


class SquareCatalogItem(StrictPayload):
    """Mock Square catalog item with integer minor-unit price and stock flag."""
    item_name: str = Field(min_length=1, max_length=100)
    category: str = Field(max_length=100)
    amount_minor: int = Field(ge=0, le=1000000)
    in_stock: bool


def normalize(provider, event):
    """Translate a vendor-specific event into the normalized record data for its kind.

    Args: provider (mock provider id) and event (an Envelope). Returns the dict stored in
    OperationalProjection.data. Pure function; no DB access. POS signals always set
    availability_verified=False and only OPEN checks hint at possible occupancy.
    """
    if provider == "mock_opentable" and event.kind == "reservation":
        body = OpenTableReservation.model_validate(event.data)
        return {"customer_name": body.guest_name, "party_size": body.party_size, "start_at": utc(body.scheduled_at).isoformat(), "end_at": utc(body.scheduled_at + timedelta(minutes=body.duration_minutes)).isoformat(), "unit_id": body.seating_id, "resources": [body.seating_id], "status": body.status}
    if provider == "mock_toast" and event.kind == "pos_signal":
        body = ToastSignal.model_validate(event.data)
        return {"table_external_id": body.table_guid, "signal": {"OPEN": "check_open", "CLOSED": "check_closed", "PAID": "bill_paid"}[body.check_state], "occupancy_hint": "possibly_occupied" if body.check_state == "OPEN" else "unknown", "availability_verified": False}
    if provider == "mock_square" and event.kind == "menu":
        body = SquareCatalogItem.model_validate(event.data)
        return {"name": body.item_name, "category": body.category, "price_minor": body.amount_minor, "available": body.in_stock, "work_units": 1, "modifiers": []}
    raise DomainError("UNSUPPORTED_CAPABILITY", "This mock does not expose that event type", 422)
