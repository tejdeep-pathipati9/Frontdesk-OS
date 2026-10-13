"""Staff authentication helpers: JWT session cookie, identity lookup and role checks.

Sessions are HS256 JWTs (12h) in the `frontdesk_session` cookie, signed with
`settings().session_secret`. Raises `DomainError` so the API error handler formats responses.
"""
import hmac
from datetime import timedelta
import jwt
from fastapi import Request
from sqlalchemy import select
from .config import settings
from .db import Session
from .models import Membership, now
from .domain import DomainError


def token_for(member):
    """Signed 12-hour session JWT whose subject is the Membership id."""
    return jwt.encode({"sub": member.id, "exp": now() + timedelta(hours=12)}, settings().session_secret, algorithm="HS256")


def identity(request: Request):
    """Resolve the session cookie to {id, tenant_id, role, email}; 401 if missing, invalid or membership deleted."""
    raw = request.cookies.get("frontdesk_session")
    if not raw:
        raise DomainError("UNAUTHENTICATED", "Sign in to continue", 401)
    try:
        claims = jwt.decode(raw, settings().session_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise DomainError("UNAUTHENTICATED", "Your session expired", 401)
    with Session() as db:
        member = db.get(Membership, claims["sub"])
        if not member:
            raise DomainError("UNAUTHENTICATED", "Membership no longer exists", 401)
        return {"id": member.id, "tenant_id": member.tenant_id, "role": member.role, "email": member.email}


def require_role(user, *roles):
    """Raise 403 unless the user is owner/manager or has one of the extra `roles`."""
    if user["role"] not in ["owner", "manager", *roles]:
        raise DomainError("FORBIDDEN", "Your role cannot perform this action", 403)


def secret_matches(supplied, expected):
    """Constant-time secret compare; empty/unset expected secret never matches."""
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))
