"""Real HTTP adapters. Capability checks do not equate to write authorization.

    Local mutations remain internal until a reconciliation worker and mappings
    have been configured; provider writes never fall back to demo success.
"""
from typing import Protocol
import httpx
from .config import settings
from .domain import DomainError


class BookingProvider(Protocol):
    def get_availability(self, request: dict) -> dict: ...
    def create_booking(self, request: dict, idempotency_key: str) -> dict: ...
    def cancel_booking(self, reference: str, version: int) -> dict: ...


class OrderProvider(Protocol):
    def get_menu(self) -> dict: ...
    def submit_order(self, cart: dict, idempotency_key: str) -> dict: ...
    def get_order_status(self, reference: str) -> dict: ...


def provider_request(method, url, *, headers=None, json=None):
    try:
        response = httpx.request(method, url, headers=headers, json=json, timeout=10)
    except httpx.TimeoutException:
        return {"status": "pending_confirmation" if method != "GET" else "degraded", "error_code": "PROVIDER_TIMEOUT"}
    except httpx.RequestError:
        return {"status": "degraded", "error_code": "PROVIDER_UNREACHABLE"}
    if response.status_code in [401, 403]:
        return {"status": "needs_attention", "error_code": "PERMISSION_OR_CREDENTIAL_ERROR"}
    if not response.is_success:
        return {"status": "degraded", "error_code": f"PROVIDER_HTTP_{response.status_code}"}
    try:
        return {"status": "confirmed", "data": response.json()}
    except ValueError:
        return {"status": "degraded", "error_code": "INVALID_PROVIDER_RESPONSE"}


class SquareAdapter:
    def __init__(self):
        cfg = settings()
        self.configured = bool(cfg.square_access_token and cfg.square_location_id)
        self.base = "https://connect.squareupsandbox.com" if cfg.square_environment == "sandbox" else "https://connect.squareup.com"
        self.headers = {"Authorization": f"Bearer {cfg.square_access_token}", "Square-Version": cfg.square_api_version, "Content-Type": "application/json"}

    def request(self, method, route, body=None):
        if not self.configured:
            raise DomainError("NOT_CONFIGURED", "Square credentials and location are required", 503)
        return provider_request(method, self.base + route, headers=self.headers, json=body)

    def test(self):
        if not self.configured:
            return {"provider": "square", "mode": "not_configured", "capabilities": {"bookings_read": "not_configured", "bookings_write": "requires_permission", "table_occupancy": "unsupported"}}
        result = self.request("GET", f"/v2/locations/{settings().square_location_id}")
        return {"provider": "square", "mode": "sandbox" if result["status"] == "confirmed" and settings().square_environment == "sandbox" else "connected_read_only" if result["status"] == "confirmed" else "degraded", "capabilities": {"locations_read": "supported" if result["status"] == "confirmed" else "degraded", "bookings_read": "requires_permission", "bookings_write": "requires_permission", "table_occupancy": "unsupported"}, "error_code": result.get("error_code"), "detail": "Connection test verifies location reads only; booking scopes/subscription and staff/service mappings require separate validation."}

    def get_availability(self, request):
        return self.request("POST", "/v2/bookings/availability/search", request)

    def create_booking(self, request, idempotency_key):
        return self.request("POST", "/v2/bookings", {"idempotency_key": idempotency_key, "booking": request})

    def update_booking(self, reference, request, idempotency_key):
        return self.request("PUT", f"/v2/bookings/{reference}", {"idempotency_key": idempotency_key, "booking": request})

    def cancel_booking(self, reference, version):
        return self.request("POST", f"/v2/bookings/{reference}/cancel", {"booking_version": version})

    def get_menu(self):
        return self.request("GET", "/v2/catalog/list?types=ITEM,MODIFIER,MODIFIER_LIST")


class ToastAdapter:
    def __init__(self):
        cfg = settings()
        self.configured = bool(cfg.toast_client_id and cfg.toast_client_secret and cfg.toast_restaurant_guid)
        self.base = cfg.toast_api_url.rstrip("/")

    def authenticate(self):
        cfg = settings()
        if not self.configured:
            raise DomainError("NOT_CONFIGURED", "Toast authorized credentials are required", 503)
        result = provider_request("POST", self.base + "/authentication/v1/authentication/login", json={"clientId": cfg.toast_client_id, "clientSecret": cfg.toast_client_secret, "userAccessType": "TOAST_MACHINE_CLIENT"})
        token = result.get("data", {}).get("token", {}).get("accessToken")
        if not token:
            raise DomainError("PROVIDER_AUTH_FAILED", "Toast authentication failed; inspect credential permissions", 503)
        return token

    def request(self, method, route, body=None):
        headers = {"Authorization": f"Bearer {self.authenticate()}", "Toast-Restaurant-External-ID": settings().toast_restaurant_guid, "Content-Type": "application/json"}
        return provider_request(method, self.base + route, headers=headers, json=body)

    def test(self):
        if not self.configured:
            return {"provider": "toast", "mode": "not_configured", "capabilities": {"menu_read": "not_configured", "orders_write": "requires_permission", "table_occupancy": "unsupported"}}
        try:
            result = self.get_menu()
        except DomainError:
            result = {"status": "needs_attention", "error_code": "PROVIDER_AUTH_FAILED"}
        return {"provider": "toast", "mode": "connected_read_only" if result["status"] == "confirmed" else "degraded", "capabilities": {"menu_read": "supported" if result["status"] == "confirmed" else "degraded", "orders_write": "requires_permission", "table_occupancy": "unsupported"}, "error_code": result.get("error_code"), "detail": "Standard access is read-only. Order writes require verified partner/custom access and catalog mappings."}

    def get_menu(self):
        return self.request("GET", "/menus/v2/menus")

    def submit_order(self, cart, idempotency_key):
        if not settings().toast_order_write_enabled:
            raise DomainError("PROVIDER_WRITE_GATED", "Toast order writes are disabled until authorized scopes are verified", 403)
        # Caller must pass a stable externalId in provider-format payload for lookup.
        return self.request("POST", "/orders/v2/orders", {**cart, "externalId": idempotency_key})

    def get_order_status(self, reference):
        return self.request("GET", f"/orders/v2/orders/{reference}")
