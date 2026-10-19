import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from server import Scenario, Engine
from phone_directory import PhoneContact, check_contact, reply
from simple_dds import scenarios
from test_briefing import reporting, saved_card, sip_reporting, voice_calls
from test_dialogue import event
from test_rbac_integration import classroom
from dds_review import _superior_reports, review, unfinished


def setup_card(c, index=0):
    card = saved_card(c, services=('Деп. ЖКХ',))
    example = Scenario.model_validate(scenarios()[index]).model_dump()
    card.update(exercise_mode='actions', owner_service='Деп. ЖКХ', card=example['prefilled_card'],
                crew_options=example['crew_options'], dds_expectation=example['dds_expectation'],
                required_phone_contacts=[{'id': x['id'], 'name': x['name']} for x in example['phone_contacts'] if x.get('required')])
    state = c['store'].load(card['id'])
    state['scenario'] = example
    c['store'].save(card['id'], state)
    save_workspace(c, card)
    return card, example, '/api/v1/student/sessions/' + card['id']


def save_workspace(c, card):
    with c['store'].db:
        c['store'].db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(card), card['id']))


def test_contact_validation_limits_and_unique_numbers():
    scenario = scenarios()[0]
    assert len(Scenario.model_validate(scenario).phone_contacts) == 4
    scenario['phone_contacts'] *= 3
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario)
    scenario = scenarios()[0]
    scenario['phone_contacts'][1]['number'] = '8 (101)'
    with pytest.raises(ValidationError, match='уникальными'):
        Scenario.model_validate(scenario)
    for invalid in ('https://example.test', 'sip:100@attacker', 'a12', '1'):
        with pytest.raises(ValidationError):
            PhoneContact(id='contact', name='Контакт', role='superior', number=invalid)


def test_public_directory_hides_private_facts_and_limits_to_owner(reporting):
    c = reporting
    card, example, path = setup_card(c)
    response = c['client'].get(path + '/phone/contacts', headers=c['headers']['student1'])
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 4
    assert not any('facts' in item or 'required_keywords' in item for item in items)
    assert not next(item for item in items if item['role'] == 'crew_leader')['available']
    assert c['client'].get(path + '/phone/contacts', headers=c['headers']['student2']).status_code == 404
    assert c['client'].get(path + '/phone/contacts', headers=c['headers']['teacher1']).status_code == 403
    # Editing the teacher's current template must not mutate the attempt snapshot.
    c['store'].put_scenario(Scenario.model_validate({**example, 'phone_contacts': []}))
    assert len(c['client'].get(path + '/phone/contacts', headers=c['headers']['student1']).json()) == 4


def dial(c, path, number, **extra):
    return c['client'].post(path + '/phone/dial', headers=c['headers']['student1'],
                            json={'message_id': str(uuid4()), 'number': number, 'transport': 'text', **extra})


def test_dial_is_directory_only_and_crew_requires_assignment(reporting):
    c = reporting
    card, _, path = setup_card(c)
    assert dial(c, path, '9999').status_code == 422
    assert dial(c, path, '8103').status_code == 409
    assert dial(c, path, '8102', transport='sip').status_code == 409
    card['assigned_crew'] = card['crew_options'][0]
    save_workspace(c, card)
    message_id = str(uuid4())
    first = dial(c, path, '8 (103)', message_id=message_id)
    assert first.status_code == 201, first.text
    assert first.json()['contact_role'] == 'crew_leader'
    assert dial(c, path, '8103', message_id=message_id).json()['id'] == first.json()['id']
    assert dial(c, path, '8102', message_id=message_id).status_code == 409
    # The legacy start route cannot bypass an explicit directory.
    response = c['client'].post(path + '/briefings', headers=c['headers']['student1'], json={
        'message_id': str(uuid4()), 'service': 'Деп. ЖКХ', 'phone': '9999', 'transport': 'text'})
    assert response.status_code == 422


def complete_call(c, path, number, text):
    started = dial(c, path, number)
    assert started.status_code == 201, started.text
    bid = started.json()['id']
    response = c['client'].post(path + f'/briefings/{bid}/messages', headers=c['headers']['student1'],
                                json={'message_id': str(uuid4()), 'text': text})
    assert response.status_code == 200, response.text
    assert response.json()['report']['complete'], response.text
    response = c['client'].post(path + f'/briefings/{bid}/finish', headers=c['headers']['student1'],
                                json={'message_id': str(uuid4()), 'recipient': started.json()['recipient_hint']})
    assert response.status_code == 200, response.text
    return response.json()


def test_other_roles_cannot_replace_chief_or_unlock_crew(reporting):
    c = reporting
    card, _, path = setup_card(c)
    result = complete_call(c, path, '8102', 'Согласуйте уборку мусора во дворе, пожалуйста.')
    assert result['purpose'] == 'coordination'
    value = json.loads(c['store'].db.execute('SELECT body FROM workspace WHERE id=?', (card['id'],)).fetchone()[0])
    assert _superior_reports(value, value['dds_expectation']) == []
    assert value['notifications'][0]['counterpart'] == 'organization_head'
    assert not any(item.get('type') == 'situation.update' for item in value['events'])
    assert 'доклад вышестоящему начальнику по телефону' in unfinished(value, value['dds_expectation'])
    checked = review(value, value['dds_expectation'])
    contact = next(item for item in checked['checks'] if item['id'] == 'contact:organization')
    assert contact['passed'] is True
    assert next(item for item in checked['checks'] if item['id'] == 'contact:specialist')['passed'] is False


@pytest.mark.parametrize('index', [0, 1])
def test_simple_exercises_accept_role_calls(reporting, index):
    c = reporting
    card, example, path = setup_card(c, index)
    card['assigned_crew'] = card['crew_options'][0]
    save_workspace(c, card)
    prefix = '81' if index == 0 else '82'
    initial = f"Москва улица Учебная дом {index + 1}. {example['incident']}. Пострадавших нет."
    for number in (prefix + '03', prefix + '01'):
        complete_call(c, path, number, initial)
    complete_call(c, path, prefix + '02', 'Согласуйте уборку мусора во дворе.' if not index else 'Согласуйте доступ в подъезд, пожалуйста.')
    complete_call(c, path, prefix + '04', 'Какие сведения о мусоре у вас есть?' if not index else 'Что известно про замок входной двери?')
    value = json.loads(c['store'].db.execute('SELECT body FROM workspace WHERE id=?', (card['id'],)).fetchone()[0])
    checked = review(value, value['dds_expectation'])
    assert all(item['passed'] for item in checked['checks'] if item['id'].startswith('contact:') or item['id'] in ('briefing', 'crew_briefing'))
    assert not any(label.startswith('разговор:') for label in unfinished(value, value['dds_expectation']))


def test_coordination_needs_meaning_not_ok():
    contact = {'id': 'test', 'required_keywords': ['доступ']}
    assert not check_contact('ок', contact)['complete']
    assert not check_contact('доступ', contact)['complete']
    assert not check_contact('Доступ не требуется, ничего не делаем.', contact)['complete']
    assert check_contact('Прошу согласовать доступ в подъезд.', contact)['complete']


@pytest.mark.asyncio
async def test_role_prompt_receives_only_current_information(monkeypatch):
    seen = []
    monkeypatch.setattr('llm.configuration', lambda: {'provider': 'ollama', 'configured': True})
    async def fake(messages, **kwargs):
        seen.extend(messages)
        return 'Ключ передаст дежурный организации.'
    monkeypatch.setattr('llm.reply', fake)
    contact = {'name': 'Дежурный организации', 'role': 'organization_head', 'facts': ['Ключ у дежурного.'],
               'current_reports': ['Бригада выехала.']}
    answer = await reply([{'role': 'user', 'content': 'Кто откроет дверь?'}], {'city': 'Москва'}, contact, {})
    assert 'Ключ' in answer
    payload = json.loads(seen[0]['content'].split('Данные: ', 1)[1])
    assert payload['кто'] == 'Дежурный организации, Руководитель организации'
    assert payload['доклады'] == ['Бригада выехала.'] and payload['знает'] == ['Ключ у дежурного.']
    assert 'works_done' not in json.dumps(seen)


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('sloppy', [False, True])
def test_full_simple_exercise_through_real_workspace_routes(reporting, monkeypatch, index, sloppy):
    monkeypatch.setenv('OPERATOR_112_ENABLED', 'false')
    c = reporting
    client, headers = c['client'], c['headers']
    example = scenarios()[index]
    c['store'].put_scenario(Scenario.model_validate(example))
    assignment = client.post('/api/v1/instructor/assignments', headers=headers['teacher1'], json={
        'group_id': c['group']['id'], 'scenario_id': example['id'], 'title': example['title']})
    assert assignment.status_code == 201, assignment.text
    created = client.post('/api/v1/student/sessions', headers=headers['student1'], json={
        'scenario_id': example['id'], 'assignment_id': assignment.json()['id']})
    assert created.status_code == 201, created.text
    card = created.json()
    path = '/api/v1/student/sessions/' + card['id']
    h = headers['student1']
    assert client.post(path + '/open', headers=h).status_code == 200
    assert client.post(path + '/finish', headers=h).status_code == 409
    accepted = client.post(path + '/services', headers=h, json={
        'service': 'Деп. ЖКХ', 'status': 'Принята', 'comment': 'Профильная карточка принята в работу.'})
    assert accepted.status_code == 200, accepted.text
    crew = client.post(path + '/crew', headers=h, json={
        'message_id': str(uuid4()), 'crew_id': example['crew_options'][0]['id'], 'decision_by': 'dispatcher'})
    assert crew.status_code == 200, crew.text
    # Even when time has elapsed, updates cannot start without the crew briefing.
    monkeypatch.setattr('workspace.update_elapsed', lambda value, update: 1000 if any(
        item.get('type') == 'notification.recorded' and item.get('detail', {}).get('counterpart') == 'crew'
        for item in value.get('events', [])) else -1)
    monkeypatch.setattr('workspace.REPORT_GAP_SECONDS', 0)
    before = client.post(path + '/updates', headers=h)
    assert not before.json().get('situation_updates')
    prefix = '81' if index == 0 else '82'
    initial = f"Москва улица Учебная дом {index + 1}. {example['incident']}. Пострадавших нет."
    complete_call(c, path, prefix + '03', initial)
    complete_call(c, path, prefix + '01', initial)
    complete_call(c, path, prefix + '02', 'Согласуйте уборку мусора во дворе.' if not index else 'Согласуйте доступ в подъезд, пожалуйста.')
    # Missing the required specialist conversation must remain visible throughout.
    for update in example['updates']:
        delivered = client.post(path + '/updates', headers=h)
        assert delivered.status_code == 200, delivered.text
        assert update['id'] in [item['id'] for item in delivered.json()['situation_updates']]
        changed = client.post(path + '/services', headers=h, json={
            'service': 'Деп. ЖКХ', 'status': update['unlocks_status'],
            'comment': 'Всё сделано.' if sloppy and update['id'] == 'done' else update['text']})
        assert changed.status_code == 200, changed.text
    complete_call(c, path, prefix + '01', example['updates'][-1]['text'])
    assert client.post(path + '/processed', headers=h).status_code == 200
    blocked = client.post(path + '/finish', headers=h)
    assert blocked.status_code == 409 and 'Специалист' in blocked.text
    complete_call(c, path, prefix + '04', 'Какие сведения о мусоре у вас есть?' if not index else 'Что известно про замок входной двери?')
    finished = client.post(path + '/finish', headers=h)
    assert finished.status_code == 200, finished.text
    result = finished.json()['dds_review']
    assert result['passed'] is not sloppy, json.dumps(result, ensure_ascii=False)
    if sloppy:
        assert result['score_percent'] < 100
        assert not next(item for item in result['checks'] if item['id'] == 'result')['passed']
    else:
        assert result['score_percent'] == 100
        assert result['critical_errors'] == []
    assert all(item.get('label') for item in result['checks'])


@pytest.mark.asyncio
async def test_voice_contact_uses_same_role_and_completion_rules(sip_reporting):
    c = sip_reporting
    card, _, path = setup_card(c)
    card.update(sip_extension='201', text_input_allowed=False)
    save_workspace(c, card)
    assert dial(c, path, '8102').status_code == 409
    result = dial(c, path, '8102', transport='sip')
    assert result.status_code == 201, result.text
    bid = result.json()['id']
    assert c['voice'].calls[-1]['body']['extension'] == '201'
    assert c['voice'].calls[-1]['body']['session_id'] == bid
    state = c['store'].load(bid)
    assert state['duty']['purpose'] == 'coordination'
    assert state['duty']['contact']['role'] == 'organization_head'
    engine = Engine(c['store'])
    await engine.handle(bid, event(bid, 'call.connected'))
    response = await engine.handle(bid, event(bid, 'operator.utterance', 'Согласуйте уборку мусора во дворе.'))
    assert 'согласовала' in response['payload']['text']
    assert c['store'].load(bid)['duty_report']['complete']
    ended = c['client'].post(path + f'/briefings/{bid}/finish', headers=c['headers']['student1'],
                             json={'message_id': str(uuid4()), 'recipient': 'Руководитель организации'})
    assert ended.status_code == 200, ended.text


def test_negated_fact_from_the_card_is_passed_on():
    # «Не работает домофон» in the card: the same words in the report pass «домофон».
    contact = {'id': 'org', 'required_keywords': ['домофон'],
               'card_text': 'Нет доступа в подъезд Не работает домофон или замок в подъезде'}
    assert check_contact('По адресу Ленинградская 5 не работает домофон, нужен доступ.', contact)['complete']
    assert check_contact('По адресу Ленинградская 5 домофон исправен, нужен доступ.', contact)['complete']
    # Without such a card the invented negation still fails.
    assert not check_contact('Доступ не требуется, ничего не делаем.', {'id': 'x', 'required_keywords': ['доступ']})['complete']


def test_contact_context_keeps_only_what_a_person_knows():
    from phone_directory import _system
    card = {'country': 'Россия', 'street': 'Ленина', 'house': '12', 'incident_type': 'Наледь',
            'service_phones': {'ДДС': '8103'}, 'classifier_version': 'v1', 'caller_name': 'Иван', 'phone': '+7 900'}
    text = _system(card, {'name': 'Ирина', 'role': 'organization_head', 'facts': ['Ключи у старшей']})['content']
    assert 'Ленина' in text and 'Наледь' in text and 'Ключи у старшей' in text
    assert '8103' not in text and 'Россия' not in text and 'Иван' not in text
    applicant = _system(card, {'name': 'Иван', 'role': 'applicant', 'facts': []})['content']
    assert '+7 900' in applicant
