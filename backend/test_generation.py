import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from accounts import Accounts
from generation import router
from teacher_guidance import router as guidance_router, examples as guidance_examples
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
    c['client'].app.include_router(guidance_router(c['store'], Accounts(c['store']), lambda: None))
    return c


def test_dds_generation_has_ready_card_and_action_expectation(drafts):
    c = drafts; h = c['headers']['teacher1']
    response = c['client'].post(BASE, headers=h, json={
        'request_id': str(uuid4()), 'brief': 'Учебное задымление в помещении',
        'mode': 'dds', 'owner_service': 'Служба 101', 'dds_profile': 'fire'})
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft['scenario']['owner_service'] == 'Служба 101'
    assert draft['scenario']['prefilled_card']['house'] == '10'
    assert draft['scenario']['dds_expectation']['brief_keywords']
    assert draft['scenario']['updates'][-1]['unlocks_status'] == 'Работы завершены'
    assert c['client'].post(BASE+'/'+draft['id']+'/approve', headers=h,
                            json={'revision': 1}).status_code == 200


def test_teacher_corrections_are_private_and_available_as_context(drafts):
    c = drafts; client = c['client']; h = c['headers']
    payload = {'request_id': str(uuid4()), 'profile': 'fire',
               'situation': 'Доклад о задымлении', 'incorrect': 'Адрес не назван',
               'correct': 'Назвать улицу и дом'}
    saved = client.post('/api/v1/instructor/corrections', headers=h['teacher1'],
                        json=payload)
    assert saved.status_code == 201, saved.text
    assert client.get('/api/v1/instructor/corrections', headers=h['teacher2']).json() == []
    assert guidance_examples(c['store'], c['users']['teacher1']['id'], 'fire')[0]['correct'] == payload['correct']
    assert guidance_examples(c['store'], c['users']['teacher1']['id'], 'medical') == []
    assert client.post('/api/v1/instructor/corrections/'+payload['request_id']+'/disable',
                       headers=h['teacher2']).status_code == 404
    assert client.post('/api/v1/instructor/corrections/'+payload['request_id']+'/disable',
                       headers=h['teacher1']).status_code == 200
    assert guidance_examples(c['store'], c['users']['teacher1']['id'], 'fire') == []


def test_dds_model_receives_teacher_correction_and_rejects_missing_reports(drafts, monkeypatch):
    c = drafts; client = c['client']; h = c['headers']['teacher1']
    correction = {'request_id': str(uuid4()), 'profile': 'fire',
                  'situation': 'Доклад о задымлении', 'incorrect': 'Не назван адрес',
                  'correct': 'Назвать улицу и номер дома'}
    assert client.post('/api/v1/instructor/corrections', headers=h,
                       json=correction).status_code == 201
    monkeypatch.setenv('LLM_PROVIDER', 'openrouter')
    seen = []
    source = {'title': 'Задымление в доме', 'description': 'Отработать приём карточки',
              'incident': 'Задымление в помещении', 'location': 'Учебная улица, дом 10',
              'known_facts': ['Задымление в помещении'],
              'prefilled_card': {'city': 'Учебный город', 'street': 'Учебная улица',
                                 'house': '10', 'incident_type': 'Задымление',
                                 'description': 'Задымление в помещении'},
              'updates': [], 'dds_expectation': {'should_accept': True, 'refusal_kind': '',
                  'refusal_keywords': [], 'brief_keywords': ['Задымление'],
                  'result_keywords': ['устранено']}}

    async def model(messages, **kwargs):
        seen.append(json.loads(messages[1]['content']))
        return json.dumps({'scenario': source}, ensure_ascii=False)

    monkeypatch.setattr('generation.llm.complete', model)
    response = client.post(BASE, headers=h, json={'request_id': str(uuid4()),
        'brief': 'Учебное задымление в доме', 'mode': 'dds',
        'owner_service': 'Служба 101', 'dds_profile': 'fire'})
    assert response.status_code == 502
    assert seen[0]['teacher_corrections'][0]['correct'] == correction['correct']


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
