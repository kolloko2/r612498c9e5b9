"""DDS release boundaries, including previously issued operator attempts."""
from uuid import uuid4

from generation import GenerateRequest
from product_mode import operator_112_enabled
from workspace import CreateLesson
from server import Scenario
from test_rbac_integration import classroom
from test_generation import drafts


def test_product_defaults_to_dds(monkeypatch):
    monkeypatch.delenv('OPERATOR_112_ENABLED', raising=False)
    assert not operator_112_enabled()
    assert CreateLesson(title='Занятие', group_id='group').mode == 'actions'
    assert GenerateRequest(request_id=uuid4(), brief='Учебное происшествие').mode == 'dds'


def test_caller_assignment_cannot_start_but_history_remains(classroom, monkeypatch):
    c = classroom
    monkeypatch.delenv('OPERATOR_112_ENABLED', raising=False)
    client, headers = c['client'], c['headers']['student1']
    before = c['store'].db.execute('SELECT count(*) FROM workspace').fetchone()[0]
    response = client.post('/api/v1/student/sessions', headers=headers,
                           json={'scenario_id': c['scenario_id'], 'assignment_id': c['assignment']['id']})
    assert response.status_code == 409
    assert c['store'].db.execute('SELECT count(*) FROM workspace').fetchone()[0] == before
    assert client.get('/api/v1/student/assignments', headers=headers).json() == []
    assert client.get('/api/v1/student/sessions/' + c['session']['id'], headers=headers).status_code == 200


def test_standalone_assignment_starts_as_prefilled_dds(classroom, monkeypatch):
    c = classroom
    scenario = c['store'].scenario(c['scenario_id'])
    scenario.update(owner_service='Деп. ЖКХ', prefilled_card={
        'city': 'Москва', 'street': 'Учебная', 'house': '12',
        'description': 'Учебное повреждение трубы', 'services': ['Деп. ЖКХ']})
    c['store'].put_scenario(Scenario.model_validate(scenario))
    monkeypatch.delenv('OPERATOR_112_ENABLED', raising=False)
    response = c['client'].post('/api/v1/student/sessions', headers=c['headers']['student1'],
                               json={'scenario_id': c['scenario_id'], 'assignment_id': c['assignment']['id']})
    assert response.status_code == 201, response.text
    value = response.json()
    assert value['exercise_mode'] == 'actions'
    assert value['card']['house'] == '12'
    assert value['owner_service'] == 'Деп. ЖКХ'
    assert value['revision'] == 1


def test_legacy_lesson_is_hidden_and_cannot_start_or_issue(classroom, monkeypatch):
    c = classroom
    response = c['client'].post('/api/v1/instructor/lessons', headers=c['headers']['teacher1'],
                               json={'title': 'Архив 112', 'group_id': c['group']['id'],
                                     'mode': 'fill', 'scenario_ids': [c['scenario_id']]})
    assert response.status_code == 201, response.text
    lid = response.json()['id']
    assert c['client'].post(f'/api/v1/instructor/lessons/{lid}/start', headers=c['headers']['teacher1']).status_code == 200
    monkeypatch.delenv('OPERATOR_112_ENABLED', raising=False)
    assert c['client'].post(f'/api/v1/instructor/lessons/{lid}/start', headers=c['headers']['teacher1']).status_code == 409
    assert c['client'].post(f'/api/v1/instructor/lessons/{lid}/restart', headers=c['headers']['teacher1']).status_code == 409
    assert c['client'].get(f'/api/v1/instructor/lessons/{lid}/live', headers=c['headers']['teacher1']).json()['state'] == 'running'
    assert c['client'].post(f'/api/v1/student/lessons/{lid}/next', headers=c['headers']['student1'], json={}).status_code == 409
    assert c['client'].get('/api/v1/student/lessons', headers=c['headers']['student1']).json() == []
    assert c['client'].get('/api/v1/instructor/lessons', headers=c['headers']['teacher1']).json() == []
    for mode in ('fill', 'mixed'):
        assert c['client'].post('/api/v1/instructor/lessons', headers=c['headers']['teacher1'],
            json={'title': 'Недоступный режим', 'group_id': c['group']['id'], 'mode': mode,
                  'scenario_ids': [c['scenario_id']]}).status_code == 409


def test_new_dds_lesson_cannot_enable_mode_switch(classroom, monkeypatch):
    monkeypatch.delenv('OPERATOR_112_ENABLED', raising=False)
    assert classroom['client'].post('/api/v1/instructor/lessons', headers=classroom['headers']['teacher1'],
        json={'title': 'ДДС', 'group_id': classroom['group']['id'], 'mode': 'actions',
              'allow_mode_switch': True}).status_code == 409


def test_caller_generation_is_blocked_before_model_call(drafts, monkeypatch):
    monkeypatch.delenv('OPERATOR_112_ENABLED', raising=False)
    response = drafts['client'].post('/api/v1/instructor/generations', headers=drafts['headers']['teacher1'],
        json={'request_id': str(uuid4()), 'brief': 'Учебная ситуация', 'mode': 'caller'})
    assert response.status_code == 409
    assert drafts['store'].db.execute('SELECT count(*) FROM generation_drafts').fetchone()[0] == 0


def test_dds_lesson_issues_locked_prepared_card(classroom, monkeypatch):
    c = classroom
    scenario = c['store'].scenario(c['scenario_id'])
    scenario.update(owner_service='Деп. ЖКХ', prefilled_card={
        'city': 'Москва', 'street': 'Учебная', 'house': '12',
        'description': 'Повреждение трубы', 'services': ['Деп. ЖКХ']})
    c['store'].put_scenario(Scenario.model_validate(scenario))
    monkeypatch.delenv('OPERATOR_112_ENABLED', raising=False)
    response = c['client'].post('/api/v1/instructor/lessons', headers=c['headers']['teacher1'],
        json={'title': 'Практика ДДС', 'group_id': c['group']['id'], 'prefilled_scenario_ids': [c['scenario_id']]})
    assert response.status_code == 201, response.text
    lid = response.json()['id']
    assert response.json()['mode'] == 'actions'
    assert c['client'].post(f'/api/v1/instructor/lessons/{lid}/start', headers=c['headers']['teacher1']).status_code == 200
    response = c['client'].post(f'/api/v1/student/lessons/{lid}/next', headers=c['headers']['student1'], json={})
    assert response.status_code == 200, response.text
    card = response.json()
    assert card['exercise_mode'] == 'actions'
    assert card['card']['house'] == '12'
    response = c['client'].put(f"/api/v1/student/sessions/{card['id']}/card", headers=c['headers']['student1'],
        json={'revision': card['revision'], 'card': {**card['card'], 'house': '99'}})
    assert response.status_code == 403
    saved = c['client'].get(f"/api/v1/student/sessions/{card['id']}", headers=c['headers']['student1']).json()
    assert saved['card']['house'] == '12'
