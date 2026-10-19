"""Autonomous AI cards: the model authors exercises ahead of time, students get them without a teacher."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from accounts import Accounts
from ai_pool import Pool
from cluster import Coordinator
from generation import router as generation_router
from learning import Learning
from server import Engine, Scenario, Store
from test_rbac_integration import PASSWORD, login, session_headers
from workspace import router as workspace_router


@pytest.fixture
def stand(monkeypatch):
    monkeypatch.setenv('OPERATOR_112_ENABLED', 'false')
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    monkeypatch.setenv('AI_POOL_TARGET', '3')

    async def unexpected_model(_messages):
        raise AssertionError('no dialogue model in this test')

    store = Store(':memory:')
    accounts = Accounts(store)
    learning = Learning(store, accounts)
    generation = generation_router(store, accounts, lambda: None, Scenario)
    pool = Pool(store, Coordinator.from_database(store.db), generation.generate_dds)
    app = FastAPI()
    app.include_router(accounts.router(lambda: None))
    app.include_router(workspace_router(store, Engine(store, unexpected_model), lambda: None, accounts, learning,
                                        ai_pool=pool))
    app.include_router(learning.router(lambda: None))
    with TestClient(app) as client:
        boot = client.post('/api/v1/auth/bootstrap', json={'username': 'admin', 'password': PASSWORD,
                                                          'display_name': 'Администратор'})
        admin = session_headers(boot.json()['session_token'])
        users = {}
        for username, role in (('teacher1', 'teacher'), ('student1', 'student'), ('student2', 'student'),
                               ('outsider', 'student')):
            users[username] = client.post('/api/v1/admin/users', headers=admin, json={
                'username': username, 'password': PASSWORD, 'display_name': username, 'role': role}).json()
        headers = {name: login(client, name) for name in users}
        group = client.post('/api/v1/instructor/groups', headers=headers['teacher1'], json={'title': 'Смена'}).json()
        for name in ('student1', 'student2'):
            client.post(f"/api/v1/instructor/groups/{group['id']}/members", headers=headers['teacher1'],
                        json={'student_id': users[name]['id']})
        yield {'client': client, 'store': store, 'pool': pool, 'headers': headers, 'users': users, 'group': group}
    store.db.close()


def fill(pool, count):
    for _ in range(count):
        assert asyncio.run(pool.fill_once())


def test_pool_stores_validated_exercises_hidden_from_teacher_library(stand):
    pool, store = stand['pool'], stand['store']
    fill(pool, 3)
    assert pool.counts() == {'utilities': 3}
    assert not asyncio.run(pool.fill_once())  # target reached, nothing generated
    rows = store.db.execute("SELECT id, topic, body FROM ai_card_pool").fetchall()
    assert len({row[1] for row in rows}) == 3  # different topics
    for sid, _, body in rows:
        scenario = store.scenario(sid)
        assert scenario['enabled'] and scenario['owner_service'] == 'Деп. ЖКХ'
        assert store.db.execute('SELECT teacher_id FROM scenario_owners WHERE scenario_id=?', (sid,)).fetchone()[0] == 'ai-pool'
        assert json.loads(body)['ai_generated']['topic']


def test_lesson_with_ai_cards_gives_each_student_a_different_ai_card(stand):
    c, h = stand['client'], stand['headers']
    fill(stand['pool'], 3)
    lesson = c.post('/api/v1/instructor/lessons', headers=h['teacher1'], json={
        'title': 'ИИ выдаёт карточки', 'group_id': stand['group']['id'], 'mode': 'actions',
        'ai_cards': True, 'dds_profile': 'utilities', 'cards_per_student': 2})
    assert lesson.status_code == 201, lesson.text
    lid = lesson.json()['id']
    # The profile only steers the AI pool; no published scenarios are mixed in.
    assert lesson.json()['templates'] == [] and lesson.json()['prefilled_scenario_ids'] == []
    assert c.post(f'/api/v1/instructor/lessons/{lid}/start', headers=h['teacher1']).status_code == 200
    first = c.post(f'/api/v1/student/lessons/{lid}/next', headers=h['student1'], json={})
    second = c.post(f'/api/v1/student/lessons/{lid}/next', headers=h['student2'], json={})
    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    one, two = first.json(), second.json()
    assert one['source_kind'] == two['source_kind'] == 'ai_pool'
    assert one['ai_generated']['topic'] and one['scenario_id'] != two['scenario_id']
    assert one['exercise_mode'] == 'actions' and one['owner_service'] == 'Деп. ЖКХ'
    assert stand['pool'].counts() == {'utilities': 1}


def test_empty_pool_asks_to_wait_instead_of_failing_silently(stand):
    c, h = stand['client'], stand['headers']
    lid = c.post('/api/v1/instructor/lessons', headers=h['teacher1'], json={
        'title': 'ИИ', 'group_id': stand['group']['id'], 'mode': 'actions', 'ai_cards': True}).json()['id']
    c.post(f'/api/v1/instructor/lessons/{lid}/start', headers=h['teacher1'])
    response = c.post(f'/api/v1/student/lessons/{lid}/next', headers=h['student1'], json={})
    assert response.status_code == 409 and 'ИИ готовит' in response.json()['detail']


def test_ai_cards_require_dds_actions_mode(stand):
    c, h = stand['client'], stand['headers']
    response = c.post('/api/v1/instructor/lessons', headers=h['teacher1'], json={
        'title': 'ИИ', 'group_id': stand['group']['id'], 'mode': 'mixed', 'ai_cards': True})
    assert response.status_code in (409, 422)
    hinted = c.post('/api/v1/instructor/lessons', headers=h['teacher1'], json={
        'title': 'ИИ', 'group_id': stand['group']['id'], 'mode': 'actions', 'ai_cards': True,
        'practice_with_hints': True})
    assert hinted.status_code == 422


def test_student_self_practice_needs_no_teacher_and_is_visible_to_group_teacher(stand):
    c, h = stand['client'], stand['headers']
    fill(stand['pool'], 2)
    assert c.get('/api/v1/student/ai-practice', headers=h['student1']).json()['available']
    card = c.post('/api/v1/student/ai-practice', headers=h['student1'], json={})
    assert card.status_code == 201, card.text
    assert card.json()['ai_generated'] and card.json()['lesson_title'] == 'Тренировка с ИИ'
    # The open card is returned again rather than a second one being issued.
    again = c.post('/api/v1/student/ai-practice', headers=h['student1'], json={})
    assert again.json()['id'] == card.json()['id'] and stand['pool'].counts() == {'utilities': 1}
    lessons = c.get('/api/v1/student/lessons', headers=h['student1']).json()
    assert any(item['title'] == 'Тренировка с ИИ' for item in lessons)
    teacher = c.get('/api/v1/instructor/lessons', headers=h['teacher1']).json()
    practice = next(item for item in teacher if item['title'] == 'Тренировка с ИИ')
    assert practice['self_practice'] and [x['id'] for x in practice['cards']] == [card.json()['id']]
    assert c.post('/api/v1/student/ai-practice', headers=h['outsider'], json={}).status_code == 409
    assert c.post('/api/v1/student/ai-practice', headers=h['teacher1'], json={}).status_code == 403


def test_phone_call_pauses_and_cancels_background_generation(stand):
    from ai_pool import PhoneBusy
    pool = stand['pool']
    started = []

    async def slow_produce(*args, **kwargs):
        started.append(True)
        await asyncio.sleep(30)

    pool.produce = slow_produce

    async def scenario():
        task = asyncio.ensure_future(pool.fill_once())
        while not started:
            await asyncio.sleep(0.01)
        pool.mark_phone()  # a call begins on any replica
        with pytest.raises(PhoneBusy):
            await asyncio.wait_for(task, 5)
        assert pool.generating is None
        with pytest.raises(PhoneBusy):
            await pool.fill_once()  # stays paused during the quiet window
    asyncio.run(scenario())
    assert pool.counts() == {} and pool.status()['paused_for_calls']


def test_applicant_is_callable_only_when_the_teacher_allows_it(stand):
    # Звонок заявителю — только если преподаватель включил его номер (рекомендация жюри).
    c, h, store = stand['client'], stand['headers'], stand['store']
    fill(stand['pool'], 3)
    for sid, body in store.db.execute("SELECT id, body FROM ai_card_pool").fetchall():
        value = json.loads(body)
        contacts = [x for x in value['scenario'].get('phone_contacts') or [] if x.get('role') != 'applicant']
        contacts.append({'id': 'applicant', 'name': 'Житель', 'role': 'applicant', 'number': '+7 900 000-11-22',
                         'facts': ['Подъезд второй'], 'required': False, 'required_keywords': []})
        value['scenario']['phone_contacts'] = contacts
        with store.db:
            store.db.execute('UPDATE ai_card_pool SET body=? WHERE id=?', (json.dumps(value, ensure_ascii=False), sid))

    def issued_roles(allow):
        lesson = c.post('/api/v1/instructor/lessons', headers=h['teacher1'], json={
            'title': 'ИИ', 'group_id': stand['group']['id'], 'mode': 'actions', 'ai_cards': True,
            'allow_applicant_calls': allow}).json()
        c.post(f"/api/v1/instructor/lessons/{lesson['id']}/start", headers=h['teacher1'])
        student = 'student1' if not allow else 'student2'
        card = c.post(f"/api/v1/student/lessons/{lesson['id']}/next", headers=h[student], json={}).json()
        return {x['role'] for x in store.load(card['id'])['scenario']['phone_contacts']}

    assert 'applicant' not in issued_roles(False)
    assert 'applicant' in issued_roles(True)


def test_capitalised_titles_are_normalised():
    from ai_pool import sentence_case
    assert sentence_case('ДДС: КОШКА ЗАСТРЯЛА') == 'ДДС: Кошка застряла'
    assert sentence_case('ДДС: Упало дерево') == 'ДДС: Упало дерево'


def test_model_is_loaded_again_after_background_authoring(stand, monkeypatch):
    # Модель телефона всегда в памяти: после выгрузки она сразу загружается снова.
    import llm
    calls = []
    monkeypatch.setattr(stand['pool'], 'provider', lambda: 'ollama')

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            calls.append(url)

    async def warm():
        calls.append('warm')
    monkeypatch.setattr('httpx.AsyncClient', Client)
    monkeypatch.setattr(llm, 'warm_phone_model', warm)
    asyncio.run(stand['pool'].release_model())
    assert calls[-1] == 'warm' and calls[0].endswith('/api/generate')


def test_calls_are_tracked_apart_from_other_model_requests(stand):
    pool = stand['pool']
    assert not pool.call_active(45)
    pool.mark_phone()  # a review or a check is not a call
    assert not pool.call_active(45)
    pool.mark_call()
    assert pool.call_active(45)



def test_review_is_cancelled_by_a_call_and_runs_again(stand, monkeypatch):
    # Разбор ИИ не делит процессор со звонком: начавшийся звонок прерывает его, потом он повторяется.
    import workspace
    pool, runs = stand['pool'], []
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await real_sleep(0.001)

    async def make():
        runs.append('start')
        if len(runs) == 1:
            pool.mark_call()           # someone dials while the review is running
            await real_sleep(10)
        return 'ready'

    async def run():
        monkeypatch.setattr(workspace.asyncio, 'sleep', fast_sleep)
        monkeypatch.setattr(pool, 'call_active', lambda seconds: False)
        return await workspace.yield_to_calls(make, pool, quiet=45, wait=120, retries=3, poll=0.01)
    assert asyncio.run(run()) == 'ready' and runs == ['start', 'start']
