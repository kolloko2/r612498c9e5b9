import json
from uuid import uuid4

from test_briefing import reporting, saved_card, base  # noqa: F401
from communication import summary
from test_rbac_integration import classroom  # noqa: F401
from test_dds_lesson_contract import prepared


def save(c, value):
    with c['store'].db:
        c['store'].db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), value['id']))


def test_voice_only_rejects_text_briefing(reporting):
    c = reporting
    card = saved_card(c)
    card['text_input_allowed'] = False
    save(c, card)
    response = c['client'].post(base(card), headers=c['headers']['student1'], json={
        'message_id': str(uuid4()), 'service': 'Служба 101', 'transport': 'text'})
    assert response.status_code == 409, response.text


def test_actual_input_not_selected_transport(reporting):
    c = reporting
    card = saved_card(c)
    assert summary(c['store'], card)['mode'] == 'none'
    response = c['client'].post(base(card), headers=c['headers']['student1'], json={
        'message_id': str(uuid4()), 'service': 'Служба 101', 'transport': 'text'})
    assert response.status_code == 201, response.text
    item = response.json()
    assert summary(c['store'], card)['mode'] == 'none'
    item['messages'].append({'role': 'user', 'content': 'Докладываю: пожар'})
    with c['store'].db:
        c['store'].db.execute('UPDATE briefings SET body=? WHERE id=?', (json.dumps(item), item['id']))
    result = summary(c['store'], card)
    assert result['mode'] == 'text' and result['text_turns'] == 1 and result['sip_turns'] == 0
    # A SIP extension is configuration, not evidence of spoken dialogue.
    card['sip_extension'] = '201'
    assert summary(c['store'], card)['mode'] == 'text'
    teacher = c['client'].get('/api/v1/instructor/sessions/' + card['id'], headers=c['headers']['teacher1'])
    assert teacher.status_code == 200, teacher.text
    assert teacher.json()['communication']['text_turns'] == 1


def test_phone_configuration_is_owned_and_validated(reporting):
    c = reporting
    response = c['client'].post('/api/v1/instructor/lessons', headers=c['headers']['teacher1'], json={
        'title': 'Телефон', 'group_id': c['group']['id'], 'mode': 'fill',
        'scenario_ids': [c['scenario_id']], 'cards_per_student': 1})
    assert response.status_code == 201, response.text
    path = '/api/v1/instructor/lessons/' + response.json()['id'] + '/phones'
    body = {'sip_extensions': {c['users']['student1']['id']: '201'}}
    assert c['client'].put(path, headers=c['headers']['teacher2'], json=body).status_code == 404
    assert c['client'].put(path, headers=c['headers']['student1'], json=body).status_code == 403
    wrong = {'sip_extensions': {c['users']['student2']['id']: '201'}}
    assert c['client'].put(path, headers=c['headers']['teacher1'], json=wrong).status_code == 422
    result = c['client'].put(path, headers=c['headers']['teacher1'], json=body)
    assert result.status_code == 200, result.text
    assert result.json()['transport'] == 'sip'


def test_no_progress_before_dispatch_and_phone_only_snapshot(reporting):
    c = reporting
    lid, card = prepared(c)
    card['assigned_crew'] = {'id': 'Наряд 17', 'leader': 'Старший', 'phone': '205',
                             'at': card['created_at']}
    card['text_input_allowed'] = False
    save(c, card)
    path = '/api/v1/student/sessions/' + card['id']
    response = c['client'].post(path + '/progress', headers=c['headers']['student1'])
    assert response.status_code == 409, response.text
    value = c['client'].get(path, headers=c['headers']['student1']).json()
    assert not value['pending_phone_reports']
    assert not [e for e in value['events'] if e['type'] == 'situation.update']
    assert value['text_input_allowed'] is False


def test_voice_only_lesson_cannot_start_without_phone(classroom):
    from server import Scenario
    from test_lesson_lifecycle import create_lesson
    c = classroom
    scenario = c['store'].scenario(c['scenario_id'])
    scenario['text_input_allowed'] = False
    c['store'].put_scenario(Scenario.model_validate(scenario))
    lesson = create_lesson(c).json()
    path = '/api/v1/instructor/lessons/' + lesson['id']
    assert c['client'].post(path + '/start', headers=c['headers']['teacher1']).status_code == 409
    configured = c['client'].put(path + '/phones', headers=c['headers']['teacher1'], json={
        'sip_extensions': {c['users']['student1']['id']: '201'}})
    assert configured.status_code == 200, configured.text
    assert c['client'].post(path + '/start', headers=c['headers']['teacher1']).status_code == 200
