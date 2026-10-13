"""Verify the authenticated public webhook before a browser starts voice audio.

Fit: the dashboard asks check_webhook() before it starts a Vapi call. The probe
sends a synthetic status-update with an unmapped assistant id and expects the
authenticated 403 UNKNOWN_ASSISTANT, which proves the tunnel, bearer secret and
webhook routing all work without creating any call row.
"""
from urllib.parse import urlsplit
import ipaddress
import re
import socket
import httpx
from .domain import DomainError


def post_probe(base_url, secret, *, method='POST', path='/api/v1/voice/vapi', payload=None):
    """Send the readiness probe (POST by default) to the webhook and return the httpx response.

    Redirects and proxy env are disabled. On a DNS failure for a *.trycloudflare.com quick-tunnel host only,
    resolve it via DNS-over-HTTPS (accepting global addresses only) and connect to that IP while keeping
    the original Host/SNI so TLS verification still applies. Other errors propagate.
    """
    if method not in ['GET', 'POST'] or not path.startswith('/') or path.startswith('//'):
        raise ValueError('Invalid boundary probe')
    endpoint = base_url.rstrip('/') + path
    headers = {'Authorization': 'Bearer ' + secret} if secret else {}
    body = payload if payload is not None else {'message': {'type': 'status-update', 'call': {'id': 'voice-readiness-check', 'assistantId': '__frontdesk_readiness__'}}}
    timeout = httpx.Timeout(4, connect=2)
    try:
        send = httpx.post if method == 'POST' else httpx.get
        return send(endpoint, headers=headers, timeout=timeout, follow_redirects=False, trust_env=False, **({'json': body} if method == 'POST' else {}))
    except httpx.ConnectError as error:
        cause = error
        while cause and not isinstance(cause, socket.gaierror):
            cause = cause.__cause__ or cause.__context__
        hostname = urlsplit(base_url).hostname
        # The local resolver may reject new quick-tunnel hostnames. Resolve only
        # this configured Cloudflare hostname, with HTTPS and certificate checks.
        if not cause or not re.fullmatch(r'[a-z0-9-]+\.trycloudflare\.com', hostname or ''):
            raise
        dns = httpx.get('https://1.1.1.1/dns-query', params={'name': hostname, 'type': 'A'}, headers={'Accept': 'application/dns-json'}, timeout=3, trust_env=False)
        dns.raise_for_status()
        answer = dns.json()
        if answer.get('Status') != 0:
            raise ValueError('Public DNS did not resolve the configured tunnel')
        addresses = [record['data'] for record in answer.get('Answer', []) if record.get('type') == 1 and ipaddress.ip_address(record.get('data', '')).is_global]
        if not addresses:
            raise ValueError('No public address for the configured tunnel')
        # HTTPX's documented SNI extension verifies the ORIGINAL hostname's TLS
        # certificate while connecting to its resolved IP. Verification stays on.
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            send = client.post if method == 'POST' else client.get
            return send(f'https://{addresses[0]}{path}', headers={**headers, 'Host': hostname}, extensions={'sni_hostname': hostname}, **({'json': body} if method == 'POST' else {}))


def check_webhook(base_url, secret):
    """Return {'status': 'ready'} if the public HTTPS webhook accepts the secret and rejects the probe assistant.

    Raises DomainError VAPI_NOT_CONFIGURED for a missing secret or a non-origin/non-HTTPS URL (no credentials,
    query, fragment or path), and retryable VAPI_CONNECTION_UNAVAILABLE for any unexpected response or transport error.
    """
    try:
        url = urlsplit(base_url)
    except ValueError:
        raise DomainError('VAPI_NOT_CONFIGURED', 'Voice connection requires setup before starting a call.', 503)
    if not secret or url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ['', '/']:
        raise DomainError('VAPI_NOT_CONFIGURED', 'Voice connection requires setup before starting a call.', 503)
    try:
        response = post_probe(base_url, secret)
        # An unmapped assistant must be rejected BEFORE any call row is created.
        if response.status_code != 403 or response.json().get('code') != 'UNKNOWN_ASSISTANT':
            raise ValueError('Unexpected authenticated webhook response')
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
        raise DomainError('VAPI_CONNECTION_UNAVAILABLE', 'Voice service is unavailable. Reconnect it before starting a call.', 503, retryable=True)
    return {'status': 'ready'}
