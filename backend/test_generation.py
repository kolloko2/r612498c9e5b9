import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from accounts import Accounts
from generation import router
from server import Scenario
from test_rbac_integration import classroom

BASE = '/api/v1/instructor/generations'


def test_generation_curriculum_survives_revision_and_approval(drafts):
    c = drafts; client = c['client']; h = c['headers']['teacher1']
    body = {'request_id': str(uuid4()), 'brief': 'Учебное взаимодействие служб',
            'difficulty': 'advanced', 'dds_profile': 'medical', 'learning_objectives': 'Уточнять неполные сведения'}
    response = client.post(BASE, headers=h, json=body)
    assert response.status_code == 201
    draft = response.json(); url = BASE+'/'+draft['id']
    assert draft['scenario']['difficulty'] == 'advanced'
    assert client.post(BASE, headers=h, json={**body, 'difficulty': 'basic'}).status_code == 409
    revised = client.post(url+'/revise', headers=h, json={'revision': 1, 'comment': 'Уточните учебные обстоятельства'}).json()
    assert revised['scenario']['dds_profile'] == 'medical'
    assert client.post(url+'/approve', headers=h, json={'revision': 2}).status_code == 200
    saved = c['store'].scenario(draft['scenario']['id'])
    assert saved['learning_objectives'] == body['learning_objectives'] and saved['difficulty'] == 'advanced'


@pytest.fixture
def drafts(classroom, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    c = classroom
    c['client'].app.include_router(router(c['store'], Accounts(c['store']), lambda: None, Scenario))
    return c


def create(c):
    body = {'request_id': str(uuid4()), 'brief': 'Учебное задымление без пострадавших', 'category_id': 'fire'}
    response = c['client'].post(BASE, headers=c['headers']['teacher1'], json=body)
    assert response.status_code == 201, response.text
    return body, response.json()


def test_draft_review_approval_and_privacy(drafts):
    c = drafts; client = c['client']; h = c['headers']['teacher1']
    body, draft = create(c); url = BASE+'/'+draft['id']; sid = draft['scenario']['id']
    assert draft['status'] == 'draft' and draft['provider'] == 'mock'
    assert c['store'].scenario(sid) is None
    assert client.get(url, headers=c['headers']['teacher2']).status_code == 404
    assert client.get(url, headers=c['headers']['student1']).status_code == 403
    assert client.get(BASE, headers=c['headers']['teacher2']).json() == []
    assert client.post(BASE, headers=h, json=body).json() == draft
    changed = client.post(url+'/revise', headers=h, json={'revision': 1, 'comment': 'Уточните обстоятельства'}).json()
    assert changed['revision'] == 2 and len(changed['history']) == 2
    assert c['store'].scenario(sid) is None
    assert client.post(url+'/approve', headers=h, json={'revision': 1}).status_code == 409
    approved = client.post(url+'/approve', headers=h, json={'revision': 2}).json()
    assert approved['status'] == 'approved' and approved['approved_scenario_id'] == sid
    assert c['store'].scenario(sid)['enabled']
    rubric = client.get('/api/v1/instructor/scenarios/'+sid+'/rubric', headers=h).json()
    assert rubric['revision'] == 1 and rubric['rubric'] == changed['rubric']
    assert client.post(url+'/approve', headers=h, json={'revision': 2}).json() == approved
    assert client.post(url+'/revise', headers=h, json={'revision': 2, 'comment': 'Ещё исправление'}).status_code == 409
    assert client.get('/api/v1/instructor/scenarios/'+sid+'/rubric', headers=c['headers']['teacher2']).status_code == 404


def test_provider_revision_and_failed_output_preserves_preview(drafts, monkeypatch):
    c = drafts; _, draft = create(c); h = c['headers']['teacher1']; url = BASE+'/'+draft['id']
    monkeypatch.setenv('LLM_PROVIDER', 'openrouter')
    calls = []
    async def reply(messages, **kwargs):
        calls.append(messages)
        payload = {'scenario': draft['scenario'], 'rubric': draft['rubric']}
        payload['scenario']['victim_name'] = 'Учебный свидетель'
        return json.dumps(payload)
    monkeypatch.setattr('generation.llm.complete', reply)
    changed = c['client'].post(url+'/revise', headers=h, json={'revision': 1, 'comment': 'Заявитель — учебный свидетель'}).json()
    assert changed['scenario']['victim_name'] == 'Учебный свидетель'
    assert 'Заявитель — учебный свидетель' in calls[0][1]['content']
    async def failure(*args, **kwargs):
        raise RuntimeError('sensitive upstream diagnostics')
    monkeypatch.setattr('generation.llm.complete', failure)
    result = c['client'].post(url+'/revise', headers=h, json={'revision': 2, 'comment': 'Повторить исправление'})
    assert result.status_code == 502 and 'sensitive' not in result.text
    assert c['client'].get(url, headers=h).json() == changed


def test_missing_generated_opening_reuses_incident(drafts, monkeypatch):
    c = drafts; _, draft = create(c)
    monkeypatch.setenv('LLM_PROVIDER', 'openrouter')
    payload = {'scenario': {**draft['scenario']}, 'rubric': draft['rubric']}
    payload['scenario'].pop('opening')

    async def reply(*args, **kwargs):
        return json.dumps(payload)

    monkeypatch.setattr('generation.llm.complete', reply)
    result = c['client'].post(BASE, headers=c['headers']['teacher1'], json={
        'request_id': str(uuid4()), 'brief': 'Синтетическое происшествие по учебному адресу',
    })
    assert result.status_code == 201, result.text
    assert result.json()['scenario']['opening'] == draft['scenario']['incident']


@pytest.mark.parametrize('invalid', ['json', 'reference', 'field'])
def test_invalid_model_output_never_publishes(drafts, monkeypatch, invalid):
    c = drafts; _, draft = create(c)
    monkeypatch.setenv('LLM_PROVIDER', 'openrouter')
    payload = {'scenario': draft['scenario'], 'rubric': draft['rubric']}
    if invalid == 'reference':
        payload['rubric']['criteria'][0]['expected'] = ['Нет такого факта']
    if invalid == 'field':
        payload['rubric']['criteria'][0].update(field='services', mode='set_equals', expected=['101'])
    async def reply(*args, **kwargs):
        return 'not JSON' if invalid == 'json' else json.dumps(payload)
    monkeypatch.setattr('generation.llm.complete', reply)
    result = c['client'].post(BASE, headers=c['headers']['teacher1'], json={'request_id': str(uuid4()), 'brief': 'Новый учебный сценарий'})
    assert result.status_code == 502
    assert len(c['client'].get(BASE, headers=c['headers']['teacher1']).json()) == 1
    assert c['store'].scenario(draft['scenario']['id']) is None


def test_concurrent_generation_and_approval_are_idempotent(drafts):
    c = drafts; h = c['headers']['teacher1']; body = {'request_id': str(uuid4()), 'brief': 'Учебный вызов'}
    with ThreadPoolExecutor(3) as pool:
        responses = list(pool.map(lambda _: c['client'].post(BASE, headers=h, json=body), range(3)))
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()['scenario']['id'] for r in responses}) == 1
    url = BASE+'/'+responses[0].json()['id']+'/approve'
    with ThreadPoolExecutor(3) as pool:
        responses = list(pool.map(lambda _: c['client'].post(url, headers=h, json={'revision': 1}), range(3)))
    assert all(r.status_code == 200 for r in responses)
    assert c['store'].db.execute('SELECT count(*) FROM rubric_history WHERE scenario_id LIKE ?', ('%:'+responses[0].json()['approved_scenario_id'],)).fetchone()[0] == 1


def test_publication_rolls_back_and_can_retry(drafts):
    c = drafts; _, draft = create(c); h = c['headers']['teacher1']; sid = draft['scenario']['id']
    db = c['store'].db
    db.execute("CREATE TRIGGER reject_generated_rubric BEFORE INSERT ON rubrics BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(Exception, match='test failure'):
        c['client'].post(BASE+'/'+draft['id']+'/approve', headers=h, json={'revision': 1})
    assert c['store'].scenario(sid) is None
    assert db.execute('SELECT 1 FROM scenario_owners WHERE scenario_id=?', (sid,)).fetchone() is None
    assert c['client'].get(BASE+'/'+draft['id'], headers=h).json()['status'] == 'draft'
    db.execute('DROP TRIGGER reject_generated_rubric')
    assert c['client'].post(BASE+'/'+draft['id']+'/approve', headers=h, json={'revision': 1}).status_code == 200
    assignment = c['client'].post('/api/v1/instructor/assignments', headers=h, json={
        'group_id': c['group']['id'], 'scenario_id': sid, 'title': 'Утверждённый сценарий'}).json()
    session = c['client'].post('/api/v1/student/sessions', headers=c['headers']['student1'], json={
        'assignment_id': assignment['id'], 'scenario_id': sid}).json()
    assert session['assessment_enabled']
    assert c['store'].load(session['id'])['evaluation_rubric']['rubric'] == draft['rubric']
