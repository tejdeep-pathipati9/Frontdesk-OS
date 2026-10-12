import httpx
import pytest
from app.providers import SquareAdapter, ToastAdapter, provider_request
from app.config import settings
from app.domain import DomainError


def test_ambiguous_write_timeout_is_pending(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("uncertain")
    monkeypatch.setattr(httpx, "request", timeout)
    assert provider_request("POST", "https://example.invalid")["status"] == "pending_confirmation"
    assert provider_request("GET", "https://example.invalid")["status"] == "degraded"


def test_permission_failure_is_not_success(monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda *a, **k: httpx.Response(403))
    assert provider_request("POST", "https://example.invalid")["status"] == "needs_attention"


def test_square_create_contract(monkeypatch):
    monkeypatch.setattr(settings(), "square_access_token", "fixture-token")
    monkeypatch.setattr(settings(), "square_location_id", "fixture-location")
    captured = {}
    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return httpx.Response(200, json={"booking": {"id": "external-fixture"}})
    monkeypatch.setattr(httpx, "request", request)
    result = SquareAdapter().create_booking({"location_id": "fixture-location"}, "stable-key")
    assert result["data"]["booking"]["id"] == "external-fixture"
    assert captured["json"]["idempotency_key"] == "stable-key"
    assert captured["url"].endswith("/v2/bookings")


def test_toast_write_requires_feature_gate(monkeypatch):
    monkeypatch.setattr(settings(), "toast_order_write_enabled", False)
    with pytest.raises(DomainError) as error:
        ToastAdapter().submit_order({}, "stable")
    assert error.value.code == "PROVIDER_WRITE_GATED"
