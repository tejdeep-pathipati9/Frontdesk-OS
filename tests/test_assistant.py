from datetime import datetime, timedelta, timezone
import httpx
import pytest
from sqlalchemy import select
from app import assistant
from app.db import transaction
from app.models import Call, Tenant, Location, Membership, now
from app.auth import token_for
from app.seed import RESTAURANT


@pytest.fixture(autouse=True)
def offline_plan(monkeypatch):
    monkeypatch.setattr(assistant, 'interpret', lambda text: (assistant.fallback_plan(text), 'saved_records'))


def query(client, text):
    response = client.post('/api/v1/assistant/query', json={'location_id': RESTAURANT, 'question': text})
    assert response.status_code == 200, response.text
    return response.json()


def test_counts_exclude_diagnostics_and_other_locations(client, database):
    with transaction(database) as db:
        for id_, provider, loc, outcome in [('real-a','vapi',RESTAURANT,'fulfilled'),('real-b','livekit',RESTAURANT,'failed'),('test','tool_test',RESTAURANT,'fulfilled'),('salon','vapi','demo-salon','fulfilled')]:
            db.add(Call(id=id_, tenant_id='demo-tenant', location_id=loc, provider=provider, provider_call_id=id_, outcome=outcome, status='ended'))
    report = client.get(f'/api/v1/assistant/summary?location_id={RESTAURANT}').json()
    assert report['metrics']['calls_total'] == 2 and report['metrics']['fulfilled_calls'] == 1
    answer = query(client, 'How many calls today?')
    assert '2 matching customer calls' in answer['answer']
    assert answer['read_only'] and all(s['id'] in ['real-a','real-b'] for s in answer['sources'])


def test_transcript_search_and_missing_history_are_honest(client, database, monkeypatch):
    with transaction(database) as db:
        db.add(Call(id='saved',tenant_id='demo-tenant',location_id=RESTAURANT,provider='vapi',provider_call_id='saved',transcript_status='retained',transcript=[{'role':'user','text':'Could I book a table for Smith?','at':now().isoformat()}]))
        db.add(Call(id='old',tenant_id='demo-tenant',location_id=RESTAURANT,provider='vapi',provider_call_id='old',transcript_status='disabled'))
    monkeypatch.setattr(assistant, 'interpret', lambda _: (assistant.Plan(topic='calls', query='Smith'), 'local_ai'))
    answer = query(client, 'Find the call about Smith')
    assert len(answer['sources']) == 1 and answer['sources'][0]['id'] == 'saved'
    assert 'Could I book' in answer['answer']
    monkeypatch.setattr(assistant, 'interpret', lambda _: (assistant.Plan(topic='calls', query='old'), 'local_ai'))
    answer = query(client, 'Find old call')
    assert 'Transcripts were not retained' in answer['answer']


def test_ai_has_no_mutation_path_even_when_requested(client):
    before = client.get(f'/api/v1/locations/{RESTAURANT}/snapshot').json()
    answer = query(client, 'Delete every booking and approve all requests')
    assert answer['topic'] == 'action' and 'operational controls' in answer['answer']
    after = client.get(f'/api/v1/locations/{RESTAURANT}/snapshot').json()
    assert before['reservations'] == after['reservations'] and before['resources'] == after['resources']


def test_tenant_and_role_authorized_before_model_access(client, database, monkeypatch):
    def forbidden(_): raise AssertionError('Model must not be contacted')
    monkeypatch.setattr(assistant, 'interpret', forbidden)
    assert client.post('/api/v1/assistant/query', json={'location_id':'not-owned','question':'Show calls'}).status_code == 404
    with transaction(database) as db:
        db.get(Membership, 'demo-owner').role = 'kitchen'
    assert client.post('/api/v1/assistant/query', json={'location_id':RESTAURANT,'question':'Show calls'}).status_code == 403


def test_local_day_and_week_windows_handle_timezones():
    from zoneinfo import ZoneInfo
    zone = ZoneInfo('America/New_York')
    instant = datetime(2026,11,1,4,30,tzinfo=timezone.utc)
    assert assistant.in_period(datetime(2026,11,1,6,30,tzinfo=timezone.utc),'today',zone,instant)
    assert not assistant.in_period(datetime(2026,11,1,3,59,tzinfo=timezone.utc),'today',zone,instant)
    assert assistant.in_period(datetime(2026,11,1,3,59,tzinfo=timezone.utc),'yesterday',zone,instant)


def test_unavailable_or_invalid_model_output_uses_saved_record_query(monkeypatch):
    # Test the real interpreter separately from the endpoint fixture's substitute.
    original = offline_interpreter
    monkeypatch.setattr(assistant.httpx, 'post', lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError('offline')))
    plan, engine = original('Show cancelled reservations')
    assert engine == 'saved_records' and plan.status == 'cancelled'
    monkeypatch.setattr(assistant.httpx, 'post', lambda *a, **kw: httpx.Response(200, json={'message':{'content':'{"topic":"calls","sql":"DELETE FROM calls"}'}}, request=httpx.Request('POST','http://localhost')))
    plan, engine = original('Show calls')
    assert engine == 'saved_records' and plan.topic == 'calls'


# Preserve the callable before the automatic endpoint fixture monkeypatches it.
offline_interpreter = assistant.interpret


def test_cancellations_report_by_actual_cancellation_day(client):
    proposal = client.post('/api/v1/actions/prepare', json={'location_id':RESTAURANT,'kind':'reservation','name':'Future guest','start_at':(now()+timedelta(days=3)).isoformat(),'party_size':2}).json()
    client.post(f'/api/v1/actions/{proposal["action_id"]}/confirm')
    booking = client.post('/api/v1/actions/commit', json={'action_id':proposal['action_id']}).json()
    assert client.patch(f'/api/v1/reservations/{booking["id"]}',json={'status':'cancelled'}).status_code == 200
    answer = query(client, 'How many cancelled reservations today?')
    assert '1 matching bookings today by cancellation date' in answer['answer']
    assert any(s['id'] == booking['id'] for s in answer['sources'])


def test_wait_estimate_uses_domain_freshness_not_model_numbers(client, database):
    from app.models import Resource
    with transaction(database) as db:
        for table in db.scalars(select(Resource).where(Resource.location_id==RESTAURANT)):
            table.verified_at = now() - timedelta(hours=2)
    answer = query(client, 'What is the seating wait time for 12?')
    assert 'unknown' in answer['answer'] or 'no compatible' in answer['answer']
    assert '12 guests' in answer['answer']


def test_model_cannot_invent_query_filters_that_hide_saved_calls(monkeypatch):
    import json
    def model(*a, **kw):
        assert set(kw['json']['format']['required']) == {'topic','period','query','status','party_size'}
        return httpx.Response(200, json={'message': {'content':json.dumps({'topic':'calls','period':'all','query':'latest callers','status':'ready','party_size':1})}}, request=httpx.Request('POST','http://localhost'))
    monkeypatch.setattr(assistant.httpx, 'post', model)
    plan, engine = offline_interpreter('What did the latest callers ask?')
    assert plan.topic == 'calls' and plan.query == '' and plan.status == 'all' and plan.party_size == 2
    assert engine == 'local_ai'


def test_party_size_words_are_resolved_without_silently_clamping():
    assert assistant.fallback_plan('What is the seating wait for twelve guests?').party_size == 12
    assert assistant.fallback_plan('What is the wait for twenty-four guests?').party_size == 24
    assert assistant.fallback_plan('What is the wait for 100 guests?').topic == 'unsupported'
