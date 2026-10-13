from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(StrictModel):
    email: str
    password: str = ""
    demo: bool = False


class Availability(StrictModel):
    location_id: str
    start_at: datetime
    party_size: int = Field(default=2, ge=1, le=30)
    service_id: str | None = None
    staff_id: str | None = None

    @field_validator("start_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Explicit timezone offset is required")
        return value


class CartItem(StrictModel):
    item_id: str
    quantity: int = Field(default=1, ge=1, le=20)
    modifiers: list[str] = Field(default_factory=list, max_length=10)


class QuoteRequest(StrictModel):
    location_id: str
    items: list[CartItem] = Field(min_length=1, max_length=30)


class PrepareAction(StrictModel):
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
        return "" if value is None else value

    @field_validator("start_at")
    @classmethod
    def aware(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("Explicit timezone offset is required")
        return value


class CommitAction(StrictModel):
    action_id: str


class TableTransition(StrictModel):
    state: Literal["available", "held", "seated", "cleaning", "out_of_service"]
    party_size: int | None = Field(default=None, ge=1, le=30)
    reservation_id: str | None = None


class KitchenPatch(StrictModel):
    kitchen_open: bool | None = None
    accepting_takeout: bool | None = None
    accepting_dine_in: bool | None = None
    capacity_per_bucket: int | None = Field(default=None, ge=1, le=1000)
    delay_min: int | None = Field(default=None, ge=0, le=120)


class StatusPatch(StrictModel):
    status: str = Field(max_length=30)


class WaitlistCreate(StrictModel):
    location_id: str
    name: str = Field(min_length=1, max_length=100)
    party_size: int = Field(ge=1, le=30)


class ResourcePatch(StrictModel):
    state: Literal["available", "out_of_service"] | None = None
    attributes: dict | None = None


class LocationCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    vertical: Literal["restaurant", "salon"]
    timezone: str = "America/New_York"
    existing_software: Literal["internal", "staff_requests", "mock_opentable", "mock_toast", "mock_square"] = "internal"


class SettingsPatch(StrictModel):
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
    location_id: str
    booking_authority: Literal["internal", "staff_requests", "mock_opentable"] | None = None
    order_authority: Literal["internal", "mock_square"] | None = None
    scenario: Literal["normal", "unavailable", "stale", "failure", "ambiguous"] = "normal"


class AuthoritySelection(StrictModel):
    booking_provider: Literal["internal", "staff_requests", "mock_opentable"]
    order_provider: Literal["internal", "mock_square"] = "internal"


class StaffBookingRequest(StrictModel):
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
    resource_ids: list[str] = Field(min_length=1, max_length=10)
    verified_arrangement: Literal[True]


class ToolRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=80)
    arguments: dict = Field(default_factory=dict)


class ResourceCreate(StrictModel):
    location_id: str
    name: str = Field(min_length=1, max_length=100)
    capacity: int = Field(default=1, ge=1, le=30)


class ServiceCreate(StrictModel):
    location_id: str
    name: str = Field(min_length=1, max_length=100)
    duration_min: int = Field(ge=5, le=480)
    buffer_min: int = Field(default=10, ge=0, le=120)
    price_minor: int = Field(ge=0, le=1000000)
    eligible_staff: list[str] = Field(min_length=1, max_length=100)


class ServicePatch(StrictModel):
    enabled: bool


class VoiceSession(StrictModel):
    location_id: str


class TranscriptEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=100)
    role: Literal["assistant", "user"]
    text: str = Field(max_length=4000)
    interrupted: bool = False


class ChatTurn(StrictModel):
    text: str = Field(min_length=1, max_length=2000)


class LookupRequest(StrictModel):
    reference: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    phone: str = Field(min_length=1, max_length=80, pattern=r"^[+\d\s().-]+$")


class AttentionDecision(StrictModel):
    decision: Literal["approve", "reject", "resolved"]


class CustomerPatch(StrictModel):
    display_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, pattern=r"^(\+[1-9]\d{7,14})?$")
