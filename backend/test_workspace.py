import os
from uuid import uuid4
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault('DIALOGUE_DB', ':memory:')
os.environ['LLM_PROVIDER'] = 'mock'
from server import Store, Engine
from workspace import router
from test_rbac_integration import classroom


@pytest.fixture
def client():
    store = Store(':memory:')
    app = FastAPI()
    app.include_router(router(store, Engine(store), lambda: None))
    with TestClient(app) as c:
        yield c
    store.db.close()


def create(client):
    scenarios = client.get('/api/v1/student/scenarios').json()
    assert set(scenarios[0]) == {'id', 'title'}
    result = client.post('/api/v1/student/sessions', json={'scenario_id': scenarios[0]['id']})
    assert result.status_code == 201
    assert 'scenario' not in result.json()
    return result.json()


def test_card_revision_dialogue_finish_and_audit(client):
    s = create(client)
    url = '/api/v1/student/sessions/' + s['id']
    card = s['card'] | {'caller_name': 'Учебный заявитель', 'services': ['Служба 101']}
    saved = client.put(url+'/card', json={'revision':0,'card':card})
    assert saved.status_code == 200
    assert saved.json()['revision'] == 1
    assert client.put(url+'/card', json={'revision':0,'card':card}).status_code == 409
    mid = str(uuid4())
    message = {'message_id':mid, 'text':'Что случилось?'}
    first = client.post(url+'/messages', json=message).json()
    second = client.post(url+'/messages', json=message).json()
    assert first['messages'] == second['messages']
    assert len(first['messages']) == 3
    assert client.post(url+'/services', json={'service':'Служба 101','status':'Принята','comment':'Учебная бригада уведомлена'}).status_code == 200
    assert client.post(url+'/services', json={'service':'Служба 104','status':'Принята','comment':'нет'}).status_code == 422
    finished = client.post(url+'/finish').json()
    assert finished['status'] == 'Завершена'
    assert finished['events'][-1]['type'] == 'session.finished'
    assert finished == client.post(url+'/finish').json()
    assert client.post(url+'/messages', json={'message_id':str(uuid4()),'text':'Повторите'}).status_code == 409
    assert client.put(url+'/card', json={'revision':1,'card':card}).status_code == 409


def test_missing_session_and_hidden_answers(client):
    assert client.get('/api/v1/student/sessions/'+str(uuid4())).status_code == 404
    assert client.post('/api/v1/student/sessions', json={'scenario_id':'missing'}).status_code == 404
    s=create(client)
    assert 'known_facts' not in str(s)
    assert s['card']['street'] == ''
    assert client.post('/api/v1/student/sessions/'+s['id']+'/call').status_code == 409


def test_classifier_canonical_type_and_mismatched_features(client):
    catalog = client.get('/api/v1/student/classifier').json()
    record = catalog['records'][0]
    s = create(client)
    url = '/api/v1/student/sessions/' + s['id'] + '/card'
    card = s['card'] | {'classifier_id': record['id'], 'classifier_version': catalog['version'],
                         'classifier_group': record['group_id'], 'classifier_features': record['features'],
                         'incident_type': 'Client cannot override canonical type'}
    result = client.put(url, json={'revision': 0, 'card': card})
    assert result.status_code == 200
    assert result.json()['card']['incident_type'] == record['incident_type']
    assert result.json()['classification']['source_row'] == record['source_row']
    card['classifier_features'] = ['invalid', '', '']
    assert client.put(url, json={'revision': 1, 'card': card}).status_code == 422
    card['classifier_features'] = record['features']
    card['classifier_version'] = 'invalid'
    assert client.put(url, json={'revision': 1, 'card': card}).status_code == 422


def test_routing_preview_save_and_history(client):
    catalog = client.get('/api/v1/student/classifier').json()
    record = catalog['records'][0]
    s = create(client)
    url = '/api/v1/student/sessions/' + s['id']
    card = s['card'] | {'classifier_id': record['id'], 'classifier_version': catalog['version'],
                       'classifier_group': record['group_id'], 'classifier_features': record['features'],
                       'injured': True, 'gasification': True}
    response = client.post('/api/v1/student/routing/preview', json=card)
    assert response.status_code == 200
    routing = response.json()
    assert {'Служба 101', 'Служба 102', 'Служба 103', 'Служба 104'} <= {s['service'] for s in routing['suggestions']}
    # Preview does not persist or implicitly add suggested services.
    assert client.get(url).json()['revision'] == 0
    saved = client.put(url+'/card', json={'revision': 0, 'card': card}).json()
    assert saved['routing'] == routing
    assert set(saved['card']['services']) == {s['service'] for s in routing['suggestions']}
    card['services'] = ['Служба 101']
    saved = client.put(url+'/card', json={'revision': 1, 'card': card}).json()
    assert set(saved['card']['services']) == {s['service'] for s in routing['suggestions']}
    assert client.post(url+'/finish').json()['routing'] == routing
    assert client.get(url).json()['routing'] == routing
    card['classifier_version'] = 'stale'
    assert client.post('/api/v1/student/routing/preview', json=card).status_code == 422


def test_source_feature_whitespace_survives_card_validation():
    from classifier import get_catalog
    from workspace import Card
    for record in get_catalog()['records']:
        assert Card(classifier_features=record['features']).classifier_features == record['features']


def test_frozen_rubric_report_and_no_active_answer_leak(client):
    scenario = client.get('/api/v1/student/scenarios').json()[0]['id']
    rubric_url = '/api/v1/instructor/scenarios/' + scenario + '/rubric'
    rubric = {'title': 'Учебный эталон', 'time_limit_seconds': 30, 'criteria': [
        {'id': 'street', 'label': 'Улица', 'field': 'street', 'mode': 'equals',
         'expected': ['Учебная секретная улица'], 'weight': 3},
        {'id': 'house', 'label': 'Дом', 'field': 'house', 'mode': 'equals', 'expected': ['12'], 'weight': 1}]}
    assert client.get(rubric_url).json() == {'revision': 0, 'rubric': None}
    assert client.put(rubric_url, json={'revision': 0, 'rubric': rubric}).status_code == 200
    s = create(client)
    url = '/api/v1/student/sessions/' + s['id']
    assert s['assessment_enabled'] and s['time_limit_seconds'] == 30
    assert 'Учебная секретная улица' not in str(s)
    assert 'Учебная секретная улица' not in client.get('/api/v1/student/sessions').text
    assert client.get(url+'/report').status_code == 409
    card = s['card'] | {'street': 'учебная секретная улица', 'house': '99'}
    assert client.put(url+'/card', json={'revision': 0, 'card': card}).status_code == 200
    # Reconfiguration does not change the reference already frozen for this attempt.
    rubric['criteria'][0]['expected'] = ['Другая улица']
    assert client.put(rubric_url, json={'revision': 1, 'rubric': rubric}).status_code == 200
    assert client.put(rubric_url, json={'revision': 1, 'rubric': rubric}).status_code == 409
    result = client.post(url+'/finish').json()['evaluation']
    assert result['score_percent'] == 75
    assert result['rubric_revision'] == 1
    assert result['criteria'][0]['expected'] == ['Учебная секретная улица']
    assert result == client.get(url+'/report').json()
    assert result == client.post(url+'/finish').json()['evaluation']


def test_unconfigured_rubric_does_not_invent_grade(client):
    s = create(client)
    result = client.post('/api/v1/student/sessions/'+s['id']+'/finish').json()['evaluation']
    assert result['status'] == 'not_configured'
    assert result['score_percent'] is None


def test_invalid_rubric_rejected_without_persistence(client):
    scenario = client.get('/api/v1/student/scenarios').json()[0]['id']
    url = '/api/v1/instructor/scenarios/'+scenario+'/rubric'
    assert client.put(url, json={'revision': 0, 'rubric': {'title': 'Тест', 'criteria': []}}).status_code == 422
    assert client.get(url).json()['revision'] == 0


def test_ai_review_only_after_finish_and_cached(client, monkeypatch):
    import workspace
    s = create(client)
    url = '/api/v1/student/sessions/'+s['id']
    calls = []
    async def fake_review(card, scenario, rubric, corrections=None, materials=None):
        calls.append((card, scenario, rubric))
        return {'status': 'ready', 'provider': 'mock', 'model': 'test', 'summary': 'Разбор', 'findings': [], 'limitations': []}
    monkeypatch.setattr(workspace, 'review_card', fake_review)
    assert client.post(url+'/ai-review').status_code == 409
    assert not calls
    finished = client.post(url+'/finish').json()
    reviewed = client.post(url+'/ai-review').json()
    assert reviewed['ai_review']['status'] == 'ready'
    assert reviewed['card'] == finished['card']
    assert reviewed['evaluation'] == finished['evaluation']
    assert reviewed['events'][-1]['type'] == 'review.completed'
    assert client.post(url+'/ai-review').json() == reviewed
    assert client.get(url).json()['ai_review'] == reviewed['ai_review']
    assert len(calls) == 1


def test_ai_review_failure_is_sanitized_and_retryable(client, monkeypatch):
    import workspace
    s = create(client)
    url = '/api/v1/student/sessions/'+s['id']
    client.post(url+'/finish')
    async def bad_review(*args):
        raise ValueError('private upstream diagnostic must not leak')
    monkeypatch.setattr(workspace, 'review_card', bad_review)
    result = client.post(url+'/ai-review')
    assert result.status_code == 200
    assert result.json()['ai_review']['status'] == 'failed'
    assert 'private upstream' not in result.text
    async def good_review(*args):
        return {'status': 'mock', 'provider': 'mock', 'model': 'test', 'summary': 'Демонстрация', 'findings': [], 'limitations': []}
    monkeypatch.setattr(workspace, 'review_card', good_review)
    assert client.post(url+'/ai-review').json()['ai_review']['status'] == 'mock'


@pytest.mark.asyncio
async def test_concurrent_ai_review_is_single_provider_request(monkeypatch):
    import asyncio
    import workspace
    store = Store(':memory:')
    app = FastAPI()
    app.include_router(router(store, Engine(store), lambda: None))
    calls = []
    async def fake_review(*args):
        calls.append(1)
        await asyncio.sleep(.01)
        return {'status': 'ready', 'provider': 'mock', 'model': 'test', 'summary': 'Разбор', 'findings': [], 'limitations': []}
    monkeypatch.setattr(workspace, 'review_card', fake_review)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
            sid = (await api.post('/api/v1/student/sessions', json={'scenario_id':store.first_enabled()['id'], 'transport':'sip'})).json()['id']
            url = '/api/v1/student/sessions/'+sid
            await api.post(url+'/finish')
            responses = await asyncio.gather(api.post(url+'/ai-review'), api.post(url+'/ai-review'))
            assert all(r.status_code == 200 for r in responses)
            assert responses[0].json() == responses[1].json()
            assert len(calls) == 1
    finally:
        store.db.close()


@pytest.mark.asyncio
async def test_review_completion_budget_preserves_long_json(monkeypatch):
    import json
    import llm
    monkeypatch.setenv('LLM_PROVIDER', 'openrouter')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-secret-not-real')
    original = httpx.AsyncClient
    payload = json.dumps({'summary': 'текст '*400, 'findings': []}, ensure_ascii=False)
    def handler(request):
        assert json.loads(request.content)['max_tokens'] == 1800
        return httpx.Response(200,json={'choices':[{'message':{'content':payload}}]})
    monkeypatch.setattr(llm.httpx,'AsyncClient',lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    assert await llm.complete([{'role':'user','content':'Тест'}],max_tokens=1800) == payload
    with pytest.raises(ValueError):
        await llm.complete([], max_tokens=2001)


@pytest.mark.asyncio
async def test_openrouter_request(monkeypatch):
    import llm
    monkeypatch.setenv('LLM_PROVIDER','openrouter')
    monkeypatch.setenv('OPENROUTER_API_KEY','test-secret-not-real')
    monkeypatch.setenv('OPENROUTER_MODEL','example/model')
    actual_client = httpx.AsyncClient
    def handler(request):
        assert str(request.url) == 'https://openrouter.ai/api/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-secret-not-real'
        assert b'example/model' in request.content
        return httpx.Response(200,json={'choices':[{'message':{'content':'Учебный ответ.'}}]})
    monkeypatch.setattr(llm.httpx,'AsyncClient',lambda **kwargs: actual_client(transport=httpx.MockTransport(handler)))
    assert await llm.complete([{'role':'user','content':'Тест'}]) == 'Учебный ответ.'
    assert 'test-secret' not in str(llm.configuration())


@pytest.mark.asyncio
async def test_concurrent_turns_are_not_lost():
    import asyncio
    store=Store(':memory:')
    async def model(messages):
        await asyncio.sleep(.01)
        return 'Не знаю.'
    engine=Engine(store,model)
    sid=str(uuid4())
    def event(kind, text=''):
        return {'event_id':str(uuid4()),'session_id':sid,'type':kind,'payload':{'mode':'auto','text':text,'utterance_id':str(uuid4())}}
    await engine.handle(sid,event('call.connected'))
    await asyncio.gather(engine.handle(sid,event('operator.utterance','Первый вопрос')),engine.handle(sid,event('operator.utterance','Второй вопрос')))
    assert len(store.load(sid)['messages']) == 5
    store.db.close()


def test_forward_adds_service_with_reason(classroom):
    """Перенаправление — отдельное действие, а не просто добавление службы."""
    client, h = classroom['client'], classroom['headers']['student1']
    card = client.post('/api/v1/student/sessions', headers=h, json={
        'scenario_id': classroom['scenario_id'], 'assignment_id': classroom['assignment']['id']}).json()
    url = '/api/v1/student/sessions/' + card['id']

    # До сохранения перенаправлять нечего.
    assert client.post(url + '/forward', headers=h, json={
        'message_id': str(uuid4()), 'service': 'Служба 102', 'reason': 'Не наш профиль'}).status_code == 409

    saved = client.put(url + '/card', headers=h, json={'revision': card['revision'], 'card': {
        **card['card'], 'street': 'Лесная', 'house': '12', 'services': ['Служба 101']}}).json()
    assert saved['card']['services'] == ['Служба 101']

    request = {'message_id': str(uuid4()), 'service': 'Служба 102', 'reason': 'Требуется полиция'}
    forwarded = client.post(url + '/forward', headers=h, json=request)
    assert forwarded.status_code == 200, forwarded.text
    value = forwarded.json()
    assert 'Служба 102' in value['card']['services']
    assert value['service_states']['Служба 102']['status'] == 'Добавлена'
    assert value['service_states']['Служба 102']['comment'] == 'Требуется полиция'
    event = next(e for e in value['events'] if e['type'] == 'card.forwarded')
    assert event['detail']['service'] == 'Служба 102' and event['detail']['reason'] == 'Требуется полиция'

    # Повтор идемпотентен, другая служба с тем же идентификатором — конфликт.
    assert client.post(url + '/forward', headers=h, json=request).status_code == 200
    assert len([e for e in client.get(url, headers=h).json()['events']
                if e['type'] == 'card.forwarded']) == 1
    assert client.post(url + '/forward', headers=h, json={
        **request, 'service': 'Служба 103'}).status_code == 409
    # Служба, уже присутствующая в карточке, не перенаправляется повторно.
    assert client.post(url + '/forward', headers=h, json={
        'message_id': str(uuid4()), 'service': 'Служба 101', 'reason': 'Повтор'}).status_code == 422
