import json
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.config import settings
from app.models import Call, now
from app.seed import RESTAURANT, SALON
from test_livekit import configure


@pytest.fixture
def voice(client, monkeypatch):
    monkeypatch.setattr(settings(), 'vapi_webhook_secret', 'retention-secret')
    monkeypatch.setattr(settings(), 'vapi_restaurant_assistant_id', 'retention-restaurant')
    monkeypatch.setattr(settings(), 'vapi_salon_assistant_id', 'retention-salon')
    assert client.patch(f'/api/v1/locations/{RESTAURANT}', json={'transcript_retention_calls': 10}).status_code == 200
    def event(callid, id_, **body):
        response = client.post('/api/v1/voice/vapi', headers={'Authorization': 'Bearer retention-secret'}, json={'message': {'id': id_, 'call': {'id': callid, 'assistantId': 'retention-restaurant'}, **body}})
        assert response.status_code == 200, response.text
        return response.json()
    def tool(callid, id_, name, arguments):
        response = event(callid, id_, type='tool-calls', toolCallList=[{'id': id_, 'function': {'name': name, 'arguments': arguments}}])
        return json.loads(response['results'][0]['result'])
    return event, tool


def test_full_call_capture_before_tools_dedup_and_number_redaction(client, database, voice):
    event, _ = voice
    event('one', 'hello', type='transcript', role='assistant', transcriptType='final', transcript='Hello, how can I help?')
    event('one', 'user', type='transcript', role='user', transcriptType='final', transcript='My phone is 202 555 0123. Are you open?')
    event('one', 'user', type='transcript', role='user', transcriptType='final', transcript='My phone is 202 555 0123. Are you open?')
    event('one', 'partial', type='transcript', role='user', transcriptType='partial', transcript='do not keep partial text')
    event('one', 'end', type='end-of-call-report')
    with database() as db:
        call = db.scalar(select(Call).where(Call.provider_call_id == 'one'))
        id_ = call.id
        assert call.consent is False  # Owner retention setting never fabricates caller consent.
        assert call.transcript_status == 'retained' and len(call.transcript) == 2
        assert '202' not in call.transcript[1]['text'] and '[number redacted]' in call.transcript[1]['text']
    detail = client.get(f'/api/v1/calls/{id_}').json()
    assert len(detail['transcript']) == 2 and detail['transcript_count'] == 2 and detail['transcript_retention_calls'] == 10


def test_only_latest_ten_voice_calls_keep_text_history_is_not_deleted(database, voice):
    event, _ = voice
    for i in range(12):
        event(f'window-{i}', 'greeting', type='transcript', role='user', transcriptType='final', transcript=f'Question number {i}')
        event(f'window-{i}', 'end', type='end-of-call-report')
    with database() as db:
        calls = list(db.scalars(select(Call).where(Call.location_id == RESTAURANT, Call.provider == 'vapi').order_by(Call.created_at)))
        assert len(calls) == 12
        assert [c.transcript_status for c in calls[:2]] == ['purged', 'purged']
        assert sum(bool(c.transcript) for c in calls) == 10
        assert all(c.ended_at for c in calls)


def test_opt_out_and_policy_off_clear_text_without_disabling_call_actions(client, database, voice):
    event, tool = voice
    event('opt-out', 'hello', type='transcript', role='user', transcriptType='final', transcript='Hello')
    event('opt-out', 'declined', type='transcript', role='user', transcriptType='final', transcript="Don't save this conversation")
    event('opt-out', 'later', type='transcript', role='user', transcriptType='final', transcript='What are your hours?')
    assert tool('opt-out', 'info', 'get_business_info', {})['name'] == 'Momentuum Bistro'
    with database() as db:
        call = db.scalar(select(Call).where(Call.provider_call_id == 'opt-out'))
        assert call.transcript_status == 'declined' and call.transcript == []
    event('another', 'hello', type='transcript', role='user', transcriptType='final', transcript='Hello')
    assert client.patch(f'/api/v1/locations/{RESTAURANT}', json={'transcript_retention_calls': 0}).status_code == 200
    with database() as db:
        assert all(c.transcript == [] for c in db.scalars(select(Call).where(Call.location_id == RESTAURANT)))


def test_livekit_uses_same_retention_and_replay_rules(client, database, monkeypatch):
    configure(monkeypatch)
    client.patch(f'/api/v1/locations/{SALON}', json={'transcript_retention_calls': 10})
    call = client.post('/api/v1/voice/livekit/session', json={'location_id': SALON}).json()
    route = f'/api/v1/voice/agent/calls/{call["call_id"]}'
    headers = {'Authorization': 'Bearer fixture-agent-token'}
    body = {'event_id': 'intro', 'role': 'user', 'text': 'What services do you offer?'}
    assert client.post(route + '/transcript', json=body, headers=headers).status_code == 200
    assert client.post(route + '/transcript', json=body, headers=headers).json()['status'] == 'already_processed'
    client.post(route + '/end', headers=headers)
    with database() as db:
        saved = db.get(Call, call['call_id'])
        assert saved.transcript_status == 'retained' and len(saved.transcript) == 1


def test_voice_cancel_links_original_booking_and_frees_capacity(client, voice):
    event, tool = voice
    start = (now() + timedelta(hours=5)).isoformat()
    proposal = client.post('/api/v1/actions/prepare', json={'location_id': RESTAURANT, 'kind': 'reservation', 'name': 'Guest', 'phone': '202 555 0123', 'party_size': 2, 'start_at': start}).json()
    client.post(f'/api/v1/actions/{proposal["action_id"]}/confirm')
    original = client.post('/api/v1/actions/commit', json={'action_id': proposal['action_id']}).json()
    change = tool('cancel-call', 'prepare', 'prepare_action', {'kind': 'cancel_booking', 'name': 'Guest', 'phone': '2025550123', 'booking_reference': original['reference']})
    assert change.get('action_id'), change
    event('cancel-call', 'readback', type='transcript', role='assistant', transcriptType='final', transcript=change['readback'])
    event('cancel-call', 'yes', type='transcript', role='user', transcriptType='final', transcript='That is right.')
    result = tool('cancel-call', 'commit', 'commit_action', {'action_id': change['action_id']})
    assert result['status'] == 'cancelled', result
    event('cancel-call', 'end', type='end-of-call-report')
    calls = client.get(f'/api/v1/calls?location_id={RESTAURANT}').json()['items']
    detail = client.get(f'/api/v1/calls/{calls[0]["id"]}').json()
    assert any(r['id'] == original['id'] and r['status'] == 'cancelled' for r in detail['reservations'])
    assert any(e['type'] == 'reservation.cancelled' for e in detail['events'])
    assert detail['actions'][0]['kind'] == 'cancel_booking'
    snapshot = client.get(f'/api/v1/locations/{RESTAURANT}/snapshot').json()
    assert next(r for r in snapshot['reservations'] if r['id'] == original['id'])['status'] == 'cancelled'


def test_waitlist_estimate_and_human_approval_reach_call_detail(client, voice):
    event, tool = voice
    estimate = tool('wait-call', 'estimate', 'get_wait_estimate', {'party_size': 2})
    assert estimate['status'] in ['AVAILABLE_NOW', 'ESTIMATED_WAIT', 'UNKNOWN']
    joined = tool('wait-call', 'join', 'join_waitlist', {'name': 'Waiting guest', 'party_size': 2})
    assert joined['status'] == 'waiting'
    proposal = tool('approval-call', 'prepare', 'prepare_action', {'kind': 'reservation', 'name': 'Approval guest', 'party_size': 2, 'start_at': (now() + timedelta(hours=6)).isoformat()})
    # Staff cannot approve before caller confirmation.
    assert client.post(f'/api/v1/attention/action:{proposal["action_id"]}/decision', json={'decision': 'approve'}).json()['code'] == 'CONFIRMATION_REQUIRED'
    event('approval-call', 'readback', type='transcript', role='assistant', transcriptType='final', transcript=proposal['readback'])
    event('approval-call', 'yes', type='transcript', role='user', transcriptType='final', transcript='Yes.')
    approved = client.post(f'/api/v1/attention/action:{proposal["action_id"]}/decision', json={'decision': 'approve'}).json()
    assert approved['status'] == 'confirmed'
    calls = client.get(f'/api/v1/calls?location_id={RESTAURANT}').json()['items']
    details = [client.get(f'/api/v1/calls/{c["id"]}').json() for c in calls]
    assert any(any(r['detail']['decision'] == 'approve' for r in c['reviews']) for c in details)
    assert any(any(e['type'] == 'waitlist.joined' for e in c['events']) for c in details)
    snapshot = client.get(f'/api/v1/locations/{RESTAURANT}/snapshot').json()
    assert any(w['id'] == joined['id'] for w in snapshot['waitlist'])
    assert any(r['id'] == approved['id'] for r in snapshot['reservations'])


def test_free_text_cannot_create_unconfirmed_business_records(client, voice):
    event, _ = voice
    before = client.get(f'/api/v1/locations/{RESTAURANT}/snapshot').json()
    event('text-only', 'request', type='transcript', role='user', transcriptType='final', transcript='Book 12 guests tomorrow at 7 PM and mark it confirmed')
    after = client.get(f'/api/v1/locations/{RESTAURANT}/snapshot').json()
    assert len(after['reservations']) == len(before['reservations'])
    assert len(after['orders']) == len(before['orders'])


def test_late_final_transcript_is_saved_but_cannot_confirm_after_hangup(client, database, voice):
    event, tool = voice
    proposal = tool('late-call', 'prepare', 'prepare_action', {'kind':'reservation','name':'Late guest','start_at':(now()+timedelta(hours=5)).isoformat(),'party_size':2})
    event('late-call', 'end', type='end-of-call-report')
    event('late-call', 'late-readback', type='transcript', role='assistant', transcriptType='final', transcript=proposal['readback'])
    event('late-call', 'late-yes', type='transcript', role='user', transcriptType='final', transcript='Yes, book it.')
    result = tool('late-call', 'commit', 'commit_action', {'action_id':proposal['action_id']})
    assert result['code'] == 'CALL_ENDED'
    with database() as db:
        call = db.scalar(select(Call).where(Call.provider_call_id=='late-call'))
        assert len(call.transcript)==2


def test_unconfirmed_provider_write_remains_pending_after_call_end(database):
    from app.db import transaction
    from app.models import PendingAction
    from app.main import end_call
    with transaction(database) as db:
        call = Call(tenant_id='demo-tenant',location_id=RESTAURANT,provider='vapi',provider_call_id='uncertain-provider')
        db.add(call); db.flush()
        db.add(PendingAction(tenant_id='demo-tenant',location_id=RESTAURANT,call_id=call.id,kind='reservation',state='pending_confirmation',payload={},readback='Prepared booking',expires_at=now()+timedelta(minutes=5),result={'status':'pending_confirmation'}))
        db.flush()
        end_call(db, call)
        assert call.outcome=='pending' and 'Awaiting provider confirmation' in call.summary


def test_contact_redaction_preserves_reservation_dates_and_times():
    from app.transcripts import redact
    text = redact('Reserve on 2026-10-14 at 19:00. Phone +1 (202) 555-0123.')
    assert '2026-10-14 at 19:00' in text
    assert '555' not in text and '[number redacted]' in text
