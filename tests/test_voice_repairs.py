"""Regressions from the real rehearsal: contact formatting and consent loops."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from app.config import settings
from app.db import transaction
from app.domain import prepare, observe_transcript, commit, speech_key, explicit_agreement, DomainError
from app.models import Call, Customer, Location, PendingAction, now
from app.schemas import PrepareAction


@pytest.mark.parametrize('phone', ['202 555 0123', '(202) 555-0123', '12025550123', '+1 202 555 0123', '0012025550123', '', None])
def test_phone_formats_and_optional_contact_preserve_booking(database, phone):
    with transaction(database) as db:
        loc = db.get(Location, 'demo-restaurant')
        call = Call(tenant_id=loc.tenant_id, location_id=loc.id, provider='vapi', provider_call_id='phone-fixture')
        db.add(call)
        db.flush()
        proposal = prepare(db, loc, PrepareAction(location_id=loc.id, kind='reservation', name='Guest', phone=phone, start_at=now() + timedelta(hours=4)), call.id)
        action = db.get(PendingAction, proposal['action_id'])
        assert action.payload['request']['phone'] == ('+12025550123' if phone else '')
        observe_transcript(db, call, 'assistant', proposal['readback'])
        observe_transcript(db, call, 'user', "Yes. That's correct.")
        assert commit(db, loc, action)['status'] == 'confirmed'
        assert db.scalar(select(Customer).where(Customer.display_name == 'Guest')).phone == ('+12025550123' if phone else '')


def test_no_country_guess_for_unconfigured_business(database):
    with transaction(database) as db:
        loc = db.get(Location, 'demo-restaurant')
        loc.settings = {k: v for k, v in loc.settings.items() if k != 'phone_country_code'}
        request = PrepareAction(location_id=loc.id, kind='reservation', name='Guest', phone='2025550123', start_at=now() + timedelta(hours=4))
        with pytest.raises(DomainError, match='country code'):
            prepare(db, loc, request)
        assert prepare(db, loc, request.model_copy(update={'phone': '+44 20 7946 0123'}))['action_id']


@pytest.mark.parametrize('phrase', ["Yes. That's correct.", 'Yes, book it. Book it.', 'Yes. I wanna book. Yes. I wanna book.', 'Yes, please.', 'Go ahead.', 'Yeah.'])
def test_natural_agreements_are_explicit(phrase):
    assert explicit_agreement(phrase)


@pytest.mark.parametrize('phrase', ['please', 'yes if the price is right', 'yes but make it 5', 'not yes', 'yes cancel that', 'yes?', 'I might want to book', 'yes do not book it', 'Yes, tomorrow instead'])
def test_conditions_changes_negation_and_questions_are_not_consent(phrase):
    assert not explicit_agreement(phrase)


def test_speech_number_formats_match_but_material_changes_do_not():
    expected = 'table for 2 for Guest on Tuesday, October 13 at 7 PM.'
    assert speech_key(expected) == speech_key('table for two for Guest on Tuesday October thirteenth at 07:00 P.M.')
    assert speech_key(expected) == speech_key('table for 2 for Guest on Tuesday October 13th at 7 p m')
    assert speech_key(expected) != speech_key(expected.replace('13', '14'))
    assert speech_key(expected) != speech_key(expected.replace('7 PM', '7 AM'))
    assert speech_key(expected) != speech_key(expected.replace('for 2', 'for 3'))


@pytest.fixture
def voice_events(client, monkeypatch):
    monkeypatch.setattr(settings(), 'vapi_webhook_secret', 'repair-secret')
    monkeypatch.setattr(settings(), 'vapi_restaurant_assistant_id', 'repair-assistant')
    def event(id_, **body):
        response = client.post('/api/v1/voice/vapi', headers={'Authorization': 'Bearer repair-secret'}, json={'message': {'id': id_, 'call': {'id': 'repair-call', 'assistantId': 'repair-assistant'}, **body}})
        assert response.status_code == 200, response.text
        return response.json()
    def tool(id_, name, args):
        response = event(id_, type='tool-calls', toolCallList=[{'id': id_, 'function': {'name': name, 'arguments': args}}])
        return json.loads(response['results'][0]['result'])
    return event, tool


def proposal_for(tool):
    when = (now().astimezone(ZoneInfo('America/New_York')) + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
    proposal = tool('prepare', 'prepare_action', {'kind': 'reservation', 'name': 'Guest', 'phone': '202 555 0123', 'party_size': 2, 'start_at': when.isoformat()})
    assert proposal.get('action_id'), proposal
    return proposal


def test_one_readback_then_barge_in_agreement_commits_once(voice_events):
    event, tool = voice_events
    proposal = proposal_for(tool)
    spoken = proposal['readback'].replace('table for 2', 'table for two').replace('7 PM', '07:00 P.M.')
    event('readback', type='transcript[transcriptType="final"]', role='assistant', transcript=spoken)
    event('barge-in', type='user-interrupted')
    event('yes', type='transcript', transcriptType='final', role='user', transcript='Yes. Book it. Book it.')
    result = tool('commit', 'commit_action', {'action_id': proposal['action_id']})
    assert result['status'] == 'confirmed', result
    assert tool('replay', 'commit_action', {'action_id': proposal['action_id']})['id'] == result['id']


def test_interrupted_incomplete_readback_does_not_commit(voice_events):
    event, tool = voice_events
    proposal = proposal_for(tool)
    event('partial', type='transcript', transcriptType='final', role='assistant', transcript=proposal['readback'][:30])
    event('barge-in', type='user-interrupted')
    event('yes', type='transcript', transcriptType='final', role='user', transcript='Yes. Book it.')
    result = tool('commit', 'commit_action', {'action_id': proposal['action_id']})
    assert result['code'] == 'CONFIRMATION_REQUIRED'
    assert result['confirmation_state'] == 'readback_incomplete'


def test_delayed_final_transcript_can_arrive_while_commit_waits(voice_events):
    event, tool = voice_events
    proposal = proposal_for(tool)
    event('readback', type='transcript', transcriptType='final', role='assistant', transcript=proposal['readback'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(tool, 'commit', 'commit_action', {'action_id': proposal['action_id']})
        time.sleep(0.15)
        event('delayed-yes', type='transcript', transcriptType='final', role='user', transcript="Yes. That's correct.")
        result = pending.result(timeout=5)
    assert result['status'] == 'confirmed', result


def test_validation_errors_are_actionable_without_contact_data(voice_events):
    _, tool = voice_events
    result = tool('invalid', 'prepare_action', {'kind': 'reservation', 'name': 'Guest', 'phone': 'invalid private text'})
    assert result['code'] == 'INVALID_ARGUMENTS' and result['invalid_fields'] == ['phone']
    assert 'invalid private text' not in json.dumps(result)
    escalation = tool('escalate', 'transfer_to_human', {'reason': 'Customer requested staff'})
    assert escalation['callback_scheduled'] is False and escalation['transfer_completed'] is False


@pytest.mark.parametrize('agreement', ['That is right.', "That's right.", 'Sounds good.', 'Sure.', 'Okay.'])
def test_spoken_name_variation_and_natural_agreement_do_not_loop(voice_events, agreement):
    event, tool = voice_events
    when = (now() + timedelta(days=1)).astimezone(ZoneInfo('America/New_York')).replace(hour=19, minute=0, second=0, microsecond=0)
    proposal = tool('prepare', 'prepare_action', {'kind': 'reservation', 'name': 'Smyth', 'party_size': 2, 'start_at': when.isoformat()})
    spoken = proposal['readback'].replace('Smyth', 'Smith').replace('table for', 'for')
    words = spoken.split()
    for i in range(0, len(words), 3):
        event(f'speech-{i}', type='transcript', transcriptType='final', role='assistant', transcript=' '.join(words[i:i+3]))
    event('agreed', type='transcript', transcriptType='final', role='user', transcript=agreement)
    result = tool('commit', 'commit_action', {'action_id': proposal['action_id']})
    assert result['status'] == 'confirmed', result


@pytest.mark.parametrize('changed', ['name', 'party', 'date', 'time', 'omitted_date'])
def test_pronunciation_tolerance_never_accepts_changed_terms(database, changed):
    with transaction(database) as db:
        loc = db.get(Location, 'demo-restaurant')
        call = Call(tenant_id=loc.tenant_id, location_id=loc.id, provider='vapi', provider_call_id='wrong-terms')
        db.add(call)
        db.flush()
        when = (now() + timedelta(days=1)).astimezone(ZoneInfo(loc.timezone)).replace(hour=19, minute=0, second=0, microsecond=0)
        proposal = prepare(db, loc, PrepareAction(location_id=loc.id, kind='reservation', name='Smyth', party_size=2, start_at=when), call.id)
        action = db.get(PendingAction, proposal['action_id'])
        spoken = proposal['readback'].replace('Smyth', 'Smith')
        if changed == 'name': spoken = spoken.replace('Smith', 'Alex')
        if changed == 'party': spoken = spoken.replace('for 2', 'for 3')
        if changed == 'date': spoken = spoken.replace(f'{when.strftime("%B")} {when.day}', f'{when.strftime("%B")} {when.day + 1}')
        if changed == 'time': spoken = spoken.replace('7 PM', '8 PM')
        if changed == 'omitted_date': spoken = spoken.split(' on ')[0] + ' at 7 PM.'
        observe_transcript(db, call, 'assistant', spoken)
        observe_transcript(db, call, 'user', 'That is right.')
        assert not action.confirmed_at
        with pytest.raises(DomainError, match='confirmation'):
            commit(db, loc, action)
        assert all(isinstance(state, int) for state in action.payload.get('readback_states', []))


def test_past_date_error_returns_current_business_time_for_recovery(voice_events):
    _, tool = voice_events
    result = tool('past', 'prepare_action', {'kind': 'reservation', 'name': 'Guest', 'party_size': 2, 'start_at': (now() - timedelta(days=1)).isoformat()})
    assert result['code'] == 'PAST_TIME'
    assert result['business_local_time'] and result['timezone'] == 'America/New_York'
    assert 'Recalculate' in result['next_action']


def test_confirmation_help_question_does_not_demand_another_readback(database):
    with transaction(database) as db:
        loc = db.get(Location, 'demo-restaurant')
        call = Call(tenant_id=loc.tenant_id, location_id=loc.id, provider='vapi', provider_call_id='help-question')
        db.add(call)
        db.flush()
        proposal = prepare(db, loc, PrepareAction(location_id=loc.id, kind='reservation', name='Guest', start_at=now() + timedelta(hours=4)), call.id)
        action = db.get(PendingAction, proposal['action_id'])
        observe_transcript(db, call, 'assistant', proposal['readback'])
        observe_transcript(db, call, 'user', 'And do I confirm yes?')
        assert action.readback_at and not action.confirmed_at
        observe_transcript(db, call, 'user', 'That is right.')
        assert commit(db, loc, action)['status'] == 'confirmed'


def test_action_specific_agreement_does_not_change_the_action_kind():
    assert explicit_agreement('Please place the order.', 'order')
    assert not explicit_agreement('Please place the order.', 'reservation')
    assert explicit_agreement('Book the appointment.', 'appointment')
    assert not explicit_agreement('Book the appointment.', 'reservation')
