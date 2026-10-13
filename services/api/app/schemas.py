"""Pydantic request/response models for the HTTP API and agent tools.

All models inherit `StrictModel` (unknown fields rejected). Datetimes must carry an
explicit timezone offset; numeric bounds here are the first line of input validation,
business rules still live in `domain.py`.
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator, ConfigDict


class StrictModel(BaseModel):
    """Base for all request models: rejects unknown fields."""
    model_config = ConfigDict(extra="forbid")


class Login(StrictModel):
    """Staff sign-in body (or demo login)."""
    email: str
    password: str = ""
    demo: bool = False


class Availability(StrictModel):
    """Availability query for a time, party size or salon service/staff."""
    location_id: str
    start_at: datetime
    party_size: int = Field(default=2, ge=1, le=30)
    service_id: str | None = None
    staff_id: str | None = None
    search: Literal["exact", "earliest"] = "exact"
    end_at: datetime | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Explicit timezone offset is required")
        return value


class CartItem(StrictModel):
    """One takeout cart line: menu item, quantity and chosen modifier ids."""
    item_id: str
    quantity: int = Field(default=1, ge=1, le=20)
    modifiers: list[str] = Field(default_factory=list, max_length=10)


class QuoteRequest(StrictModel):
    """Takeout cart to price and capacity-check."""
    location_id: str
    items: list[CartItem] = Field(min_length=1, max_length=30)


class PrepareAction(StrictModel):
    """Proposal request: booking, order, cancel or reschedule; phone optional, normalized server-side."""
    location_id: str
    kind: Literal["reservation", "appointment", "order", "cancel_booking", "reschedule_booking"]
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(default="", max_length=80, pattern=r"^[+\d\s().-]*$")
    start_at: datetime | None = None
    party_size: int = Field(default=2, ge=1, le=30)
    service_id: str | None = None
    staff_id: str | None = None
    items: list[CartItem] = Field(default_factory=list, max_length=30)
    booking_reference: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")

    @field_validator("phone", mode="before")
    @classmethod
    def optional_phone(cls, value):
        from .phone import spoken_digits
        return "" if value is None else spoken_digits(value)

    @field_validator("start_at")
    @classmethod
    def aware(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("Explicit timezone offset is required")
        return value


class CommitAction(StrictModel):
    """Commit of a previously prepared action by id."""
    action_id: str


class TableTransition(StrictModel):
    """Staff floor-state change for a table."""
    state: Literal["available", "held", "seated", "cleaning", "out_of_service"]
    party_size: int | None = Field(default=None, ge=1, le=30)
    reservation_id: str | None = None


class KitchenPatch(StrictModel):
    """Partial kitchen state update from staff."""
    kitchen_open: bool | None = None
    accepting_takeout: bool | None = None
    accepting_dine_in: bool | None = None
    capacity_per_bucket: int | None = Field(default=None, ge=1, le=1000)
    delay_min: int | None = Field(default=None, ge=0, le=120)


class StatusPatch(StrictModel):
    """Generic status change."""
    status: str = Field(max_length=30)


class WaitlistCreate(StrictModel):
    """New waitlist entry."""
    location_id: str
    name: str = Field(min_length=1, max_length=100)
    party_size: int = Field(ge=1, le=30)


class ResourcePatch(StrictModel):
    """Partial resource update (state limited to available/out_of_service, attributes)."""
    state: Literal["available", "out_of_service"] | None = None
    attributes: dict | None = None


class LocationCreate(StrictModel):
    """New location with its booking-authority choice."""
    name: str = Field(min_length=1, max_length=120)
    vertical: Literal["restaurant", "salon"]
    timezone: str = "America/New_York"
    existing_software: Literal["internal", "staff_requests", "mock_opentable", "mock_toast", "mock_square"] = "internal"


class SettingsPatch(StrictModel):
    """Partial location policy update; bounds keep policies sane."""
    transcript_retention_calls: int | None = Field(default=None, ge=0, le=10)
    intake_paused: bool | None = None
    turn_minutes: int | None = Field(default=None, ge=15, le=240)
    cleaning_minutes: int | None = Field(default=None, ge=0, le=60)
    freshness_minutes: int | None = Field(default=None, ge=1, le=240)
    tax_bps: int | None = Field(default=None, ge=0, le=3000)
    open_hour: int | None = Field(default=None, ge=0, le=23)
    close_hour: int | None = Field(default=None, ge=1, le=24)
    combinations: list[list[str]] | None = None
    transfer_phone: str | None = Field(default=None, pattern=r"^(\+[1-9]\d{7,14})?$")
    preferred_voice_provider: Literal["vapi", "livekit"] | None = None
    ai_actions: dict[Literal["bookings", "takeout", "waitlist", "booking_changes"], bool] | None = None


class IntegrationConnect(StrictModel):
    """Connect a (mock) provider and pick its simulation scenario."""
    location_id: str
    booking_authority: Literal["internal", "staff_requests", "mock_opentable"] | None = None
    order_authority: Literal["internal", "mock_square"] | None = None
    scenario: Literal["normal", "unavailable", "stale", "failure", "ambiguous"] = "normal"


class AuthoritySelection(StrictModel):
    """Choose the system of record for bookings and orders."""
    booking_provider: Literal["internal", "staff_requests", "mock_opentable"]
    order_provider: Literal["internal", "mock_square"] = "internal"


class StaffBookingRequest(StrictModel):
    """Uncertain/large-party seating request logged for staff review."""
    location_id: str
    party_size: int = Field(ge=1, le=30)
    start_at: datetime
    reason: str = Field(default="Staff seating review requested", max_length=300)
    @field_validator("start_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Explicit timezone offset is required")
        return value


class SeatingArrangementReview(StrictModel):
    """Staff-verified table arrangement for a staff booking request."""
    resource_ids: list[str] = Field(min_length=1, max_length=10)
    verified_arrangement: Literal[True]


class ToolRequest(StrictModel):
    """Agent tool invocation; request_id gives idempotency."""
    request_id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=80)
    arguments: dict = Field(default_factory=dict)


class ResourceCreate(StrictModel):
    """New table/resource."""
    location_id: str
    name: str = Field(min_length=1, max_length=100)
    capacity: int = Field(default=1, ge=1, le=30)


class ServiceCreate(StrictModel):
    """New salon service (minor-unit price)."""
    location_id: str
    name: str = Field(min_length=1, max_length=100)
    duration_min: int = Field(ge=5, le=480)
    buffer_min: int = Field(default=10, ge=0, le=120)
    price_minor: int = Field(ge=0, le=1000000)
    eligible_staff: list[str] = Field(min_length=1, max_length=100)


class ServicePatch(StrictModel):
    """Enable/disable a service."""
    enabled: bool


class VoiceSession(StrictModel):
    """Start a voice/test session for a location."""
    location_id: str


class TranscriptEvent(StrictModel):
    """Final transcript segment from the voice provider; feeds confirmation checks."""
    event_id: str = Field(min_length=1, max_length=100)
    role: Literal["assistant", "user"]
    text: str = Field(max_length=4000)
    interrupted: bool = False


class ChatTurn(StrictModel):
    """One user text turn in Ollama chat."""
    text: str = Field(min_length=1, max_length=2000)


class AssistantQuery(StrictModel):
    """Natural-language dashboard assistant question."""
    location_id: str
    question: str = Field(min_length=1, max_length=2000)


class LookupRequest(StrictModel):
    """Order lookup: reference plus matching phone (verification)."""
    reference: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    phone: str = Field(min_length=1, max_length=80, pattern=r"^[+\d\s().-]+$")


class AttentionDecision(StrictModel):
    """Staff decision on an attention-queue item."""
    decision: Literal["approve", "reject", "resolved"]


class CustomerPatch(StrictModel):
    """Customer rename / E.164 phone update."""
    display_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, pattern=r"^(\+[1-9]\d{7,14})?$")
