import json
from uuid import uuid4

import pytest

from accounts import Accounts
from briefing import check, duty_voice, router
from test_rbac_integration import classroom


@pytest.fixture
def reporting(classroom, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    c = classroom
    c['client'].app.include_router(router(c['store'], Accounts(c['store']), lambda: None))
    return c


def saved_card(c, services=('Служба 101',)):
    client, h = c['client'], c['headers']['student1']
    card = client.post('/api/v1/student/sessions', headers=h, json={
        'scenario_id': c['scenario_id'], 'assignment_id': c['assignment']['id']}).json()
    body = {**card['card'], 'street': 'Лесная', 'house': '12',
            'incident_type': 'Пожар в квартире', 'services': list(services)}
    saved = client.put('/api/v1/student/sessions/' + card['id'] + '/card', headers=h,
                       json={'revision': card['revision'], 'card': body})
    assert saved.status_code == 200, saved.text
    return saved.json()


def base(card):
    return '/api/v1/student/sessions/' + card['id'] + '/briefings'


def test_report_completeness_is_checked_against_the_card():
    card = {'street': 'Лесная', 'house': '12', 'incident_type': 'Пожар в квартире'}
    assert check('', card)['complete'] is False
    partial = check('Докладываю: улица Лесная', card)
    assert partial['complete'] is False and 'Дом' in partial['missing']
    full = check('Улица Лесная, дом 12, пожар в жилой квартире', card)
    assert full['complete'] is True and full['missing'] == []
    # Тип происшествия принимается по значимому слову, а не дословно.
    assert check('Лесная 12, горит квартира, пожар', card)['complete'] is True


def test_duty_voice_is_stable_per_service():
    assert duty_voice('Служба 101') == duty_voice('Служба 101')
    assert {duty_voice('Служба 101'), duty_voice('Служба 102')} <= {'baya', 'aidar'}


def test_briefing_dialogue_and_acceptance(reporting):
    c = reporting; client = c['client']; h = c['headers']['student1']
    card = saved_card(c)
    start = client.post(base(card), headers=h, json={'message_id': str(uuid4()), 'service': 'Служба 101',
                                                    'destination': 'ЦУКС', 'phone': '+7 900 000-00-01'})
    assert start.status_code == 201, start.text
    briefing = start.json()
    assert briefing['messages'][0]['content'].startswith('Дежурный')
    assert briefing['state'] == 'open' and briefing['simulated'] is True
    assert briefing['voice'] in ('baya', 'aidar')
    url = base(card) + '/' + briefing['id']

    # Неполный доклад: дежурный уточняет, приём не подтверждается.
    partial = client.post(url + '/messages', headers=h,
                          json={'message_id': str(uuid4()), 'text': 'Докладываю, улица Лесная'}).json()
    assert partial['report']['complete'] is False
    assert client.post(url + '/finish', headers=h, json={'message_id': str(uuid4()),
                                                         'recipient': 'Дежурный'}).status_code == 409

    full = client.post(url + '/messages', headers=h,
                       json={'message_id': str(uuid4()), 'text': 'Дом 12, пожар в квартире'}).json()
    assert full['report']['complete'] is True
    accepted = client.post(url + '/finish', headers=h,
                           json={'message_id': str(uuid4()), 'recipient': 'Дежурный смены'})
    assert accepted.status_code == 200
    assert accepted.json()['state'] == 'accepted'

    # Принятый доклад стал телефонограммой и событием карточки.
    session = client.get('/api/v1/student/sessions/' + card['id'], headers=h).json()
    notification = session['notifications'][-1]
    assert notification['service'] == 'Служба 101' and notification['recipient'] == 'Дежурный смены'
    assert 'Лесная' in notification['comment']
    assert any(event['type'] == 'notification.recorded' and event['detail'].get('source') == 'briefing'
               for event in session['events'])


def test_repeat_and_limits(reporting):
    c = reporting; client = c['client']; h = c['headers']['student1']
    card = saved_card(c)
    request = {'message_id': str(uuid4()), 'service': 'Служба 101'}
    first = client.post(base(card), headers=h, json=request).json()
    # Повтор того же message_id не создаёт второй доклад.
    assert client.post(base(card), headers=h, json=request).json()['id'] == first['id']
    assert client.post(base(card), headers=h, json={**request, 'service': 'Служба 102'}).status_code == 409
    # Служба обязана быть в сохранённой карточке.
    assert client.post(base(card), headers=h, json={'message_id': str(uuid4()),
                                                    'service': 'Неизвестная служба'}).status_code == 422
    # Повторная реплика с тем же идентификатором не дублируется.
    url = base(card) + '/' + first['id']
    message = {'message_id': str(uuid4()), 'text': 'Улица Лесная, дом 12, пожар в квартире'}
    once = client.post(url + '/messages', headers=h, json=message).json()
    twice = client.post(url + '/messages', headers=h, json=message).json()
    assert len(once['messages']) == len(twice['messages'])


def test_briefing_requires_own_saved_and_active_card(reporting):
    c = reporting; client = c['client']; h = c['headers']['student1']
    unsaved = client.post('/api/v1/student/sessions', headers=h, json={
        'scenario_id': c['scenario_id'], 'assignment_id': c['assignment']['id']}).json()
    assert client.post(base(unsaved), headers=h, json={'message_id': str(uuid4()),
                                                       'service': 'Служба 101'}).status_code == 409

    card = saved_card(c)
    # Чужой обучающийся и преподаватель не видят доклад.
    assert client.get(base(card), headers=c['headers']['student2']).status_code == 404
    assert client.get(base(card), headers=c['headers']['teacher1']).status_code == 403

    client.post('/api/v1/student/sessions/' + card['id'] + '/finish', headers=h)
    assert client.post(base(card), headers=h, json={'message_id': str(uuid4()),
                                                    'service': 'Служба 101'}).status_code == 409


@pytest.fixture
def voice_calls():
    """Голосовой модуль подменяется: тест не поднимает SIP и не звонит."""
    calls = []

    async def fake(path, method='GET', body=None):
        calls.append({'path': path, 'method': method, 'body': body})
        return {'call_id': 'call-1'} if path == 'calls' else {}

    fake.calls = calls
    return fake


@pytest.fixture
def sip_reporting(classroom, monkeypatch, voice_calls):
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    c = classroom
    c['client'].app.include_router(
        router(c['store'], Accounts(c['store']), lambda: None, None, voice_calls))
    c['voice'] = voice_calls
    return c


def test_voice_briefing_starts_a_call_and_marks_the_counterpart(sip_reporting):
    c = sip_reporting; client = c['client']; h = c['headers']['student1']
    card = saved_card(c)
    # Без назначенного учебного номера голосовой доклад недоступен.
    refused = client.post(base(card), headers=h, json={'message_id': str(uuid4()),
                                                       'service': 'Служба 101', 'transport': 'sip'})
    assert refused.status_code == 409

    # Преподаватель назначил обучающемуся номер учебного телефона.
    row = c['store'].db.execute('SELECT body FROM workspace WHERE id=?', (card['id'],)).fetchone()
    value = json.loads(row[0]); value['sip_extension'] = '201'
    with c['store'].db:
        c['store'].db.execute('INSERT INTO workspace VALUES (?,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body',
                              (card['id'], json.dumps(value, ensure_ascii=False)))

    started = client.post(base(card), headers=h, json={'message_id': str(uuid4()),
                                                       'service': 'Служба 101', 'transport': 'sip'})
    assert started.status_code == 201, started.text
    briefing = started.json()
    assert briefing['transport'] == 'sip' and briefing['call_id'] == 'call-1'
    assert c['voice'].calls[0]['body']['session_id'] == briefing['id']
    assert c['voice'].calls[0]['body']['extension'] == '201'

    # Разговор помечен ролью дежурного, и в нём лежат сведения карточки.
    state = c['store'].load(briefing['id'])
    assert state['duty']['service'] == 'Служба 101'
    assert state['duty']['card']['street'] == 'Лесная'

    # Текстовые реплики в голосовом докладе запрещены.
    assert client.post(base(card) + '/' + briefing['id'] + '/messages', headers=h,
                       json={'message_id': str(uuid4()), 'text': 'Докладываю'}).status_code == 409


def test_voice_briefing_reads_report_from_the_conversation(sip_reporting):
    c = sip_reporting; client = c['client']; h = c['headers']['student1']
    card = saved_card(c)
    row = c['store'].db.execute('SELECT body FROM workspace WHERE id=?', (card['id'],)).fetchone()
    value = json.loads(row[0]); value['sip_extension'] = '201'
    with c['store'].db:
        c['store'].db.execute('INSERT INTO workspace VALUES (?,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body',
                              (card['id'], json.dumps(value, ensure_ascii=False)))
    briefing = client.post(base(card), headers=h, json={'message_id': str(uuid4()),
                                                        'service': 'Служба 101', 'transport': 'sip'}).json()
    url = base(card) + '/' + briefing['id']

    # Неполный доклад по телефону завершить нельзя.
    state = c['store'].load(briefing['id'])
    state['messages'] = [{'role': 'assistant', 'content': 'Дежурный, Служба 101. Слушаю вас.'},
                         {'role': 'user', 'content': 'Докладываю, улица Лесная'}]
    c['store'].save(briefing['id'], state)
    listed = client.get(base(card), headers=h).json()[0]
    assert listed['report']['complete'] is False and 'Дом' in listed['report']['missing']
    assert client.post(url + '/finish', headers=h, json={'message_id': str(uuid4()),
                                                         'recipient': 'Дежурный'}).status_code == 409

    state['messages'].append({'role': 'user', 'content': 'дом 12, пожар в квартире'})
    c['store'].save(briefing['id'], state)
    accepted = client.post(url + '/finish', headers=h,
                           json={'message_id': str(uuid4()), 'recipient': 'Дежурный смены'})
    assert accepted.status_code == 200 and accepted.json()['state'] == 'accepted'
    # Разговор завершён, телефонограмма записана из произнесённого текста.
    assert any(call['path'].endswith('/hangup') for call in c['voice'].calls)
    session = client.get('/api/v1/student/sessions/' + card['id'], headers=h).json()
    assert 'Лесная' in session['notifications'][-1]['comment']
