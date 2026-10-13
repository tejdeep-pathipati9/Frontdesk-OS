"""Lost public webhook regressions: no microphone start or business side effects."""
import io
import sys
from pathlib import Path
import httpx
import pytest
from sqlalchemy import select, func
from app import voice_readiness
from app.config import settings
from app.models import Call, PendingAction

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import start_vapi_demo as manager


@pytest.fixture
def configured(client, monkeypatch):
    cfg = settings()
    for field, value in [('vapi_public_key', 'fixture-public'), ('vapi_restaurant_assistant_id', 'fixture-assistant'), ('vapi_salon_assistant_id', 'fixture-salon'), ('vapi_server_url', 'https://webhook.example'), ('vapi_webhook_secret', 'fixture-webhook-secret')]:
        monkeypatch.setattr(cfg, field, value)
    return client


def test_ready_probe_authenticates_without_creating_call(configured, database, monkeypatch):
    attempts = []
    def probe(url, **kwargs):
        attempts.append((url, kwargs))
        return httpx.Response(403, json={'code': 'UNKNOWN_ASSISTANT'})
    monkeypatch.setattr(voice_readiness.httpx, 'post', probe)
    result = configured.post('/api/v1/voice/vapi/readiness', json={'location_id': 'demo-restaurant'})
    assert result.status_code == 200 and result.json()['status'] == 'ready'
    connection = result.json()['connection_id']
    assert attempts[0][0] == 'https://webhook.example/api/v1/voice/vapi'
    assert attempts[0][1]['follow_redirects'] is False
    assert attempts[0][1]['headers']['Authorization'] == 'Bearer fixture-webhook-secret'
    assert attempts[0][1]['json']['message']['call']['assistantId'] == '__frontdesk_readiness__'
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Call)) == 0
        assert db.scalar(select(func.count()).select_from(PendingAction)) == 0
    monkeypatch.setattr(settings(), 'vapi_server_url', 'https://replacement.example')
    result = configured.post('/api/v1/voice/vapi/readiness', json={'location_id': 'demo-restaurant'})
    assert result.json()['connection_id'] != connection


@pytest.mark.parametrize('failure', ['dns', 'timeout', 'unauthenticated', 'proxy_error', 'redirect', 'unexpected'])
def test_unreachable_webhook_reports_clear_retryable_error(configured, monkeypatch, failure):
    def probe(url, **kwargs):
        if failure == 'dns': raise httpx.ConnectError('DNS failure')
        if failure == 'timeout': raise httpx.ReadTimeout('timeout')
        return {'unauthenticated': httpx.Response(401, json={'code': 'UNAUTHENTICATED_WEBHOOK'}), 'proxy_error': httpx.Response(502, text='<html>bad gateway</html>'), 'redirect': httpx.Response(302, headers={'Location': 'https://untrusted.example'}), 'unexpected': httpx.Response(403, json={'code': 'other'})}[failure]
    monkeypatch.setattr(voice_readiness.httpx, 'post', probe)
    response = configured.post('/api/v1/voice/vapi/readiness', json={'location_id': 'demo-restaurant'})
    assert response.status_code == 503 and response.json()['code'] == 'VAPI_CONNECTION_UNAVAILABLE'
    assert response.json()['retryable'] is True
    assert 'Reconnect' in response.json()['message']
    assert 'fixture-webhook-secret' not in response.text


@pytest.mark.parametrize('url', ['', 'http://localhost', 'https://user:secret@host', 'https://host/path', 'https://host?token=private', 'https://['])
def test_bad_configuration_never_sends_credential(configured, monkeypatch, url):
    monkeypatch.setattr(settings(), 'vapi_server_url', url)
    monkeypatch.setattr(voice_readiness.httpx, 'post', lambda *a, **kw: pytest.fail('Invalid URL received secret'))
    response = configured.post('/api/v1/voice/vapi/readiness', json={'location_id': 'demo-restaurant'})
    assert response.status_code == 503 and response.json()['code'] == 'VAPI_NOT_CONFIGURED'


def test_probe_cannot_be_redirected_by_client_or_access_foreign_location(configured, monkeypatch):
    monkeypatch.setattr(voice_readiness.httpx, 'post', lambda *a, **kw: pytest.fail('Unauthorized network probe'))
    assert configured.post('/api/v1/voice/vapi/readiness', json={'location_id': 'demo-restaurant', 'server_url': 'https://attacker.example'}).status_code == 422
    assert configured.post('/api/v1/voice/vapi/readiness', json={'location_id': 'unknown-location'}).status_code == 404
    configured.post('/api/v1/auth/logout')
    assert configured.post('/api/v1/voice/vapi/readiness', json={'location_id': 'demo-restaurant'}).status_code == 401


@pytest.mark.parametrize('reason', ['health_failure', 'child_exit'])
def test_manager_replaces_failed_tunnel_and_reconfigures_existing_assistants(tmp_path, monkeypatch, reason):
    config = tmp_path / 'services/api/app/config.py'
    config.parent.mkdir(parents=True)
    config.touch()
    monkeypatch.setattr(manager, 'ROOT', tmp_path)
    class Stop:
        value = False
        def is_set(self): return self.value
        def set(self): self.value = True
        def wait(self, timeout): return self.value
    stopped = Stop()
    processes, configured, checked = [], [], []
    class Process:
        def __init__(self, args, **kwargs):
            self.number = len(processes) + 1
            self.stdout = io.StringIO(f'https://tunnel-{self.number}.trycloudflare.com\n')
            self.terminated = False
            self.polls = 0
            processes.append(self)
        def poll(self):
            self.polls += 1
            return 0 if self.terminated or (reason == 'child_exit' and self.number == 1 and self.polls >= 3) else None
        def terminate(self): self.terminated = True
        def wait(self, timeout=None): return 0
        def kill(self): self.terminated = True
    def ready(url, secret):
        checked.append(url)
        return not (reason == 'health_failure' and url.endswith('tunnel-1.trycloudflare.com') and checked.count(url) > 1)
    def apply(args, **kwargs):
        configured.append(args)
        if len(configured) == 2: stopped.set()
    monkeypatch.setattr(manager.subprocess, 'Popen', Process)
    monkeypatch.setattr(manager.subprocess, 'run', apply)
    monkeypatch.setattr(manager, 'webhook_ready', ready)
    manager.maintain_tunnel('cloudflared', 'secret', stopped)
    assert len(processes) == len(configured) == 2
    assert all(process.terminated and process.stdout.closed for process in processes)
    assert configured[0][configured[0].index('--server-url') + 1] != configured[1][configured[1].index('--server-url') + 1]
    assert all('--apply' in args and '--vertical' in args and 'both' in args for args in configured)


def test_dns_fallback_keeps_hostname_tls_and_never_sends_secret_to_resolver(configured, monkeypatch):
    import socket
    monkeypatch.setattr(settings(), 'vapi_server_url', 'https://temporary.trycloudflare.com')
    def no_dns(*a, **kw):
        raise httpx.ConnectError('local DNS unavailable') from socket.gaierror(8, 'not found')
    monkeypatch.setattr(voice_readiness.httpx, 'post', no_dns)
    def dns(url, **kwargs):
        assert url == 'https://1.1.1.1/dns-query'
        assert 'Authorization' not in kwargs['headers']
        assert kwargs['params']['name'] == 'temporary.trycloudflare.com'
        return httpx.Response(200, request=httpx.Request('GET', url), json={'Status': 0, 'Answer': [{'type': 1, 'data': '104.16.230.132'}]})
    monkeypatch.setattr(voice_readiness.httpx, 'get', dns)
    class VerifiedTLS:
        def __init__(self, **kwargs): assert kwargs.get('verify', True) is True
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            assert url == 'https://104.16.230.132/api/v1/voice/vapi'
            assert kwargs['headers']['Host'] == 'temporary.trycloudflare.com'
            assert kwargs['extensions']['sni_hostname'] == 'temporary.trycloudflare.com'
            assert kwargs['headers']['Authorization'] == 'Bearer fixture-webhook-secret'
            return httpx.Response(403, json={'code': 'UNKNOWN_ASSISTANT'})
    monkeypatch.setattr(voice_readiness.httpx, 'Client', VerifiedTLS)
    assert configured.post('/api/v1/voice/vapi/readiness', json={'location_id': 'demo-restaurant'}).status_code == 200


@pytest.mark.parametrize('answer', [{'Status': 3}, {'Status': 0, 'Answer': []}, {'Status': 0, 'Answer': [{'type': 1, 'data': '127.0.0.1'}]}])
def test_dns_fallback_rejects_missing_or_private_address(configured, monkeypatch, answer):
    import socket
    monkeypatch.setattr(settings(), 'vapi_server_url', 'https://temporary.trycloudflare.com')
    def no_dns(*a, **kw):
        raise httpx.ConnectError('local DNS unavailable') from socket.gaierror(8, 'not found')
    monkeypatch.setattr(voice_readiness.httpx, 'post', no_dns)
    monkeypatch.setattr(voice_readiness.httpx, 'get', lambda url, **kw: httpx.Response(200, request=httpx.Request('GET', url), json=answer))
    monkeypatch.setattr(voice_readiness.httpx, 'Client', lambda **kw: pytest.fail('Unsafe address received credentials'))
    assert configured.post('/api/v1/voice/vapi/readiness', json={'location_id': 'demo-restaurant'}).status_code == 503


def test_unknown_table_state_is_not_reported_as_system_failure(database):
    from datetime import timedelta
    from app.db import transaction
    from app.models import Resource, now
    from app.tools import execute_tool
    with transaction(database) as db:
        call = Call(tenant_id='demo-tenant', location_id='demo-restaurant', provider='vapi', provider_call_id='unknown-floor')
        db.add(call)
        db.flush()
        for table in db.scalars(select(Resource).where(Resource.location_id == call.location_id)):
            table.verified_at = now() - timedelta(hours=2)
        result = execute_tool(db, call, 'prepare_action', {'kind': 'reservation', 'name': 'Guest', 'party_size': 2, 'start_at': (now() + timedelta(hours=6)).isoformat()}, 'prepare')
        assert result['code'] == 'AVAILABILITY_UNKNOWN'
        assert result['availability_status'] == 'UNKNOWN'
        assert result['reason_codes'] == ['STALE_STATE']
        assert result['retryable'] is False
        assert 'not a system-access failure' in result['next_action']
        assert db.scalar(select(func.count()).select_from(PendingAction)) == 0


def test_missing_credential_probe_omits_authorization_header(monkeypatch):
    def probe(url, **kwargs):
        assert 'Authorization' not in kwargs['headers']
        return httpx.Response(401, json={'code': 'UNAUTHENTICATED_WEBHOOK'})
    monkeypatch.setattr(voice_readiness.httpx, 'post', probe)
    assert voice_readiness.post_probe('https://webhook.example', '', payload={}).status_code == 401
