"""Vapi-shaped webhook flows across auth, transcripts, tools and persisted actions."""
import json
from datetime import timedelta
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from app.config import settings
from app.db import transaction
from app.models import Call, Reservation, Order, now


@pytest.mark.parametrize('kind', ['reservation', 'appointment', 'order'])
def test_vapi_confirmed_transaction_persists_once_with_call_history(client, database, monkeypatch, kind):
    cfg = settings()
    monkeypatch.setattr(cfg, 'vapi_webhook_secret', 'voice-fixture-secret')
    monkeypatch.setattr(cfg, 'vapi_restaurant_assistant_id', 'voice-restaurant')
    monkeypatch.setattr(cfg, 'vapi_salon_assistant_id', 'voice-salon')
    provider_id = f'voice-rehearsal-{kind}'
    call = {'id': provider_id, 'assistantId': 'voice-salon' if kind == 'appointment' else 'voice-restaurant'}
    headers = {'Authorization': 'Bearer voice-fixture-secret'}

    def event(id_, **body):
        response = client.post('/api/v1/voice/vapi', headers=headers, json={'message': {'id': id_, 'call': call, **body}})
        assert response.status_code == 200, response.text
        return response.json()

    def tool(id_, name, arguments):
        response = event(id_, type='tool-calls', toolCallList=[{'id': id_, 'function': {'name': name, 'arguments': json.dumps(arguments)}}])
        assert response['results'][0]['toolCallId'] == id_
        return json.loads(response['results'][0]['result'])

    event('started', type='status-update', status='in-progress')
    info = tool('info', 'get_business_info', {})
    assert info['name'] == ('Studio Bloom' if kind == 'appointment' else 'Momentuum Bistro')
    args = {'kind': kind, 'name': 'Rehearsal guest', 'phone': '+12025550123'}
    if kind == 'order':
        args['items'] = [{'item_id': 'menu-burger', 'quantity': 2, 'modifiers': ['extra-cheese']}]
    else:
        when = (now().astimezone(ZoneInfo('America/New_York')) + timedelta(days=1)).replace(hour=13, minute=0, second=0, microsecond=0)
        args.update(start_at=when.isoformat(), party_size=2)
        if kind == 'appointment':
            args.update(service_id='service-cut', staff_id='stylist-1')
    proposal = tool('prepare', 'prepare_action', args)
    assert proposal.get('action_id'), proposal
    unconfirmed = tool('premature-commit', 'commit_action', {'action_id': proposal['action_id']})
    assert unconfirmed['code'] == 'CONFIRMATION_REQUIRED'
    # Real speech final events can split the readback into multiple segments.
    words = proposal['readback'].split()
    middle = len(words) // 2
    event('readback-a', type='transcript', transcriptType='final', role='assistant', transcript=' '.join(words[:middle]))
    event('readback-b', type='transcript', transcriptType='final', role='assistant', transcript=' '.join(words[middle:]))
    event('confirmed', type='transcript', transcriptType='final', role='user', transcript='Yes, please.')
    result = tool('commit', 'commit_action', {'action_id': proposal['action_id']})
    assert result['status'] == ('accepted' if kind == 'order' else 'confirmed'), result
    assert tool('commit', 'commit_action', {'action_id': proposal['action_id']}) == result
    event('ended', type='end-of-call-report')
    with database() as db:
        stored = db.scalar(select(Call).where(Call.provider_call_id == provider_id))
        assert stored.status == 'ended' and stored.outcome == 'fulfilled'
        assert stored.transcript == []
        call_id = stored.id
        model = Order if kind == 'order' else Reservation
        rows = list(db.scalars(select(model).where(model.call_id == call_id)))
        assert len(rows) == 1 and rows[0].id == result['id']
    detail = client.get(f'/api/v1/calls/{call_id}').json()
    assert detail['outcome'] == 'fulfilled' and len(detail['tools']) == 4
    assert len(detail['orders' if kind == 'order' else 'reservations']) == 1


@pytest.mark.parametrize('phrase', ['Yes, but change the time.', 'No, do not book it.', 'Yes, cancel that.', 'Maybe yes.', 'Yes?'])
def test_punctuation_fix_never_accepts_corrections_or_ambiguous_consent(database, phrase):
    from app.models import Location, PendingAction
    from app.domain import prepare, observe_transcript
    from app.schemas import PrepareAction
    with transaction(database) as db:
        call = Call(tenant_id='demo-tenant', location_id='demo-restaurant', provider='vapi', provider_call_id='consent-fixture')
        db.add(call)
        db.flush()
        loc = db.get(Location, 'demo-restaurant')
        result = prepare(db, loc, PrepareAction(location_id=loc.id, kind='reservation', name='Consent fixture', party_size=2, start_at=now() + timedelta(hours=5)), call.id)
        action = db.get(PendingAction, result['action_id'])
        observe_transcript(db, call, 'assistant', result['readback'])
        observe_transcript(db, call, 'user', phrase)
        assert action.confirmed_at is None
