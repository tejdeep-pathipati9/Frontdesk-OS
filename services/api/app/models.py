"""Persistent tenant-owned operational records. All times are stored in UTC."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Owned(Record):
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)


class Tenant(Record, Base):
    __tablename__ = "tenants"
    name: Mapped[str] = mapped_column(String(120))


class Membership(Owned, Base):
    __tablename__ = "memberships"
    email: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(30))


class Location(Owned, Base):
    __tablename__ = "locations"
    name: Mapped[str] = mapped_column(String(120))
    vertical: Mapped[str] = mapped_column(String(20))
    timezone: Mapped[str] = mapped_column(String(60), default="America/New_York")
    phone: Mapped[str] = mapped_column(String(30), default="")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    mode: Mapped[str] = mapped_column(String(20), default="demo")


class Resource(Owned, Base):
    __tablename__ = "resources"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(30), default="available")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    seated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    party_size: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Service(Owned, Base):
    __tablename__ = "services"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    duration_min: Mapped[int] = mapped_column(Integer)
    buffer_min: Mapped[int] = mapped_column(Integer, default=10)
    price_minor: Mapped[int] = mapped_column(Integer)
    eligible_staff: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Owned, Base):
    __tablename__ = "customers"
    display_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(30), default="")


class Reservation(Owned, Base):
    __tablename__ = "reservations"
    __table_args__ = (UniqueConstraint("location_id", "idempotency_key"), Index("ix_reservation_interval", "location_id", "start_at", "end_at", "status"))
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    kind: Mapped[str] = mapped_column(String(20))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    party_size: Mapped[int] = mapped_column(Integer, default=1)
    service_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="confirmed")
    provider: Mapped[str] = mapped_column(String(20), default="internal")
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Allocation(Owned, Base):
    __tablename__ = "reservation_resources"
    reservation_id: Mapped[str] = mapped_column(ForeignKey("reservations.id"), index=True)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WaitlistEntry(Owned, Base):
    __tablename__ = "waitlist_entries"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    party_size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="waiting")
    estimate_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimate_max: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MenuItem(Owned, Base):
    __tablename__ = "menu_items"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[str] = mapped_column(String(60))
    price_minor: Mapped[int] = mapped_column(Integer)
    work_units: Mapped[int] = mapped_column(Integer, default=1)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    modifiers: Mapped[list] = mapped_column(JSON, default=list)


class KitchenState(Owned, Base):
    __tablename__ = "kitchen_state"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), unique=True)
    kitchen_open: Mapped[bool] = mapped_column(Boolean, default=True)
    accepting_takeout: Mapped[bool] = mapped_column(Boolean, default=True)
    accepting_dine_in: Mapped[bool] = mapped_column(Boolean, default=True)
    capacity_per_bucket: Mapped[int] = mapped_column(Integer, default=12)
    delay_min: Mapped[int] = mapped_column(Integer, default=0)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Order(Owned, Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("location_id", "idempotency_key"),)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    items: Mapped[list] = mapped_column(JSON)
    total_minor: Mapped[int] = mapped_column(Integer)
    work_units: Mapped[int] = mapped_column(Integer)
    bucket_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    pickup_min: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pickup_max: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default="accepted")
    provider: Mapped[str] = mapped_column(String(20), default="internal")
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Call(Owned, Base):
    __tablename__ = "calls"
    __table_args__ = (UniqueConstraint("provider", "provider_call_id"),)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    provider_call_id: Mapped[str] = mapped_column(String(100))
    caller_masked: Mapped[str] = mapped_column(String(40), default="Web test")
    status: Mapped[str] = mapped_column(String(30), default="in_progress")
    outcome: Mapped[str] = mapped_column(String(30), default="pending")
    intent: Mapped[str] = mapped_column(String(60), default="unclassified")
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    transcript: Mapped[list] = mapped_column(JSON, default=list)
    transcript_status: Mapped[str] = mapped_column(String(20), default="disabled", server_default="disabled")
    summary: Mapped[str] = mapped_column(String(500), default="")


class PendingAction(Owned, Base):
    __tablename__ = "pending_actions"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    call_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    readback: Mapped[str] = mapped_column(String(600))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    readback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(30), default="awaiting_confirmation")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Event(Owned, Base):
    __tablename__ = "operational_events"
    __table_args__ = (UniqueConstraint("source", "source_event_id"),)
    location_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(30), default="internal")
    source_event_id: Mapped[str] = mapped_column(String(180), default=uid)
    type: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(64), default=uid)


class ToolExecution(Owned, Base):
    __tablename__ = "tool_executions"
    __table_args__ = (UniqueConstraint("call_id", "request_id"),)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), index=True)
    location_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(80))
    args: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    correlation_id: Mapped[str] = mapped_column(String(64), default=uid)


class AuditEvent(Owned, Base):
    __tablename__ = "audit_events"
    location_id: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(64), default=uid)


class IntegrationConnection(Owned, Base):
    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("location_id", "provider"),)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    mode: Mapped[str] = mapped_column(String(30), default="mock")
    status: Mapped[str] = mapped_column(String(30), default="not_synced")
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ProviderRecord(Owned, Base):
    """Independent simulated system of record; never used for a live provider."""
    __tablename__ = "mock_provider_records"
    __table_args__ = (UniqueConstraint("location_id", "provider", "kind", "external_id"),)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OperationalProjection(Owned, Base):
    """Read-only normalized copies, with stable provider identity and provenance."""
    __tablename__ = "operational_projections"
    __table_args__ = (UniqueConstraint("location_id", "provider", "kind", "external_id"),)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
