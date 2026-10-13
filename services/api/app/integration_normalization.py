"""Validated mock event envelopes; real webhooks need vendor signature adapters.

Different mock payloads exercise the same normalized projection pipeline.
POS payment/check activity deliberately carries no verified occupancy state.
"""
from datetime import datetime, timedelta
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .domain import utc, DomainError


class Envelope(BaseModel):
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
    model_config = ConfigDict(extra="forbid")


class OpenTableReservation(StrictPayload):
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
    table_guid: str
    check_state: Literal["OPEN", "CLOSED", "PAID"]


class SquareCatalogItem(StrictPayload):
    item_name: str = Field(min_length=1, max_length=100)
    category: str = Field(max_length=100)
    amount_minor: int = Field(ge=0, le=1000000)
    in_stock: bool


def normalize(provider, event):
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
