"""Bounded, location-scoped transcript retention; audio is never stored here."""
import re
from sqlalchemy import select
from .models import Call, Location, now

VOICE_PROVIDERS = ['vapi', 'livekit']


def retention_limit(loc):
    return min(10, max(0, int(loc.settings.get('transcript_retention_calls', 0))))


def redact(text):
    # Contact/card digit sequences and accidental credentials do not belong in speech logs.
    dates = []
    def protect_date(match):
        dates.append(match[0])
        return f'[date{len(dates) - 1}]'
    text = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', protect_date, text)
    text = re.sub(r'(?<!\w)\+?(?:\d[\s().-]*){7,}(?!\w)', '[number redacted]', text)
    text = re.sub(r'(?i)\b(?:bearer\s+|sk-)[A-Za-z0-9_.-]{12,}', '[credential redacted]', text)
    for index, date in enumerate(dates):
        text = text.replace(f'[date{index}]', date)
    return text[:4000]


def prune(db, loc):
    db.flush()
    calls = list(db.scalars(select(Call).where(Call.tenant_id == loc.tenant_id, Call.location_id == loc.id, Call.provider.in_(VOICE_PROVIDERS)).order_by(Call.created_at.desc(), Call.id.desc())))
    limit = retention_limit(loc)
    for index, call in enumerate(calls):
        if index >= limit and (call.transcript or call.transcript_status == 'retained'):
            call.transcript = []
            call.transcript_status = 'purged' if limit else 'disabled'


def start_retention(db, call, loc):
    call.transcript_status = 'retained' if retention_limit(loc) and call.provider in VOICE_PROVIDERS else 'disabled'
    prune(db, loc)


def capture(db, call, role, text, interrupted=False):
    if role not in ['assistant', 'user'] or not text.strip() or call.provider not in VOICE_PROVIDERS:
        return
    loc = db.get(Location, call.location_id)
    if not loc or loc.tenant_id != call.tenant_id:
        return
    # Caller objections stop storage; confirmation evaluation remains transient.
    objection = role == 'user' and re.search(r"\b(?:do not|don't|dont|stop)\s+(?:record(?:ing)?|sav(?:e|ing)|stor(?:e|ing)|keep(?:ing)?|transcrib(?:e|ing))\b|\b(?:do not|don't|dont)\s+consent\b", text.replace('’', "'"), re.I)
    if objection:
        call.transcript_status, call.transcript = 'declined', []
    if call.transcript_status != 'retained' or not retention_limit(loc):
        return
    prune(db, loc)
    if call.transcript_status == 'retained':
        call.transcript = [*(call.transcript or []), {'role': role, 'text': redact(text), 'at': now().isoformat(), **({'interrupted': True} if interrupted else {})}][-500:]
