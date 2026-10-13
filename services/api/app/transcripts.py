"""Bounded, location-scoped transcript retention; audio is never stored here.

Fit: voice providers (Vapi webhook, LiveKit worker) call capture() for each final
turn. Retention is opt-in per location (transcript_retention_calls, max 10 calls);
older calls are purged on every capture/start. Text is redacted before storage and
a caller's objection to recording clears and stops storage for that call. This
module is also the shared redact() used wherever transcript text is displayed.
"""
import re
from sqlalchemy import select
from .models import Call, Location, now

VOICE_PROVIDERS = ['vapi', 'livekit']


def retention_limit(loc):
    """Number of most recent voice calls whose transcripts may be kept (0-10, default 0 = disabled)."""
    return min(10, max(0, int(loc.settings.get('transcript_retention_calls', 0))))


def redact(text):
    """Mask phone/card-like digit runs and bearer/sk- credentials, cap at 4000 chars; ISO dates are preserved.

    Pure function; safe to call on already-redacted text.
    """
    # Contact/card digit sequences and accidental credentials do not belong in speech logs.
    # Dates look like digit runs, so stash them behind placeholders while masking numbers.
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
    """Purge transcripts beyond the retention window (newest calls first) for this location's voice calls.

    Marks purged calls 'purged' (or 'disabled' when retention is off). Flushes first so the current call is counted.
    """
    db.flush()
    calls = list(db.scalars(select(Call).where(Call.tenant_id == loc.tenant_id, Call.location_id == loc.id, Call.provider.in_(VOICE_PROVIDERS)).order_by(Call.created_at.desc(), Call.id.desc())))
    limit = retention_limit(loc)
    for index, call in enumerate(calls):
        if index >= limit and (call.transcript or call.transcript_status == 'retained'):
            call.transcript = []
            call.transcript_status = 'purged' if limit else 'disabled'


def start_retention(db, call, loc):
    """Initialize a call's transcript_status ('retained' only for voice providers with retention enabled) and prune."""
    call.transcript_status = 'retained' if retention_limit(loc) and call.provider in VOICE_PROVIDERS else 'disabled'
    prune(db, loc)


def capture(db, call, role, text, interrupted=False):
    """Append one redacted turn to a retained call transcript (keeps the last 500 turns).

    No-ops for non-voice providers, empty text, tenant mismatch or when retention is off. Side effect:
    a caller's spoken objection to recording sets status 'declined' and wipes the transcript.
    """
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
