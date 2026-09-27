"""DDS incoming-card lifecycle, structured facts, and decision scoring."""
import json
from datetime import datetime, timedelta, timezone

from server import Scenario
from workspace import prefilled_from_scenario
from accounts import Accounts
from briefing import router as briefing_router
from test_rbac_integration import classroom
from test_lesson_lifecycle import create_lesson


def prepared(classroom):
    store = classroom['store']
    scenario = store.scenario(classroom['scenario_id'])
    scenario.update(owner_service='Служба 101', crew_options=[
        {'id': 'Наряд 17', 'leader': 'Старший наряда', 'phone': '+7 900 000-00-17'}],
        prefilled_card={
        'street': 'Берзарина', 'house': '21', 'building': '2',
        'apartment': '17', 'incident_type': 'Пожар в квартире',
        'services': ['Служба 101', 'Служба 103'],
        'service_phones': {'Служба 101': '+7 900 000-00-01'},
    })
    store.put_scenario(Scenario.model_validate(scenario))
    c, h = classroom['client'], classroom['headers']
    uid = classroom['users']['student1']['id']
    lesson = create_lesson(classroom, mode='actions', scenario_ids=[],
                           prefilled_scenario_ids=[scenario['id']],
                           sip_extensions={uid: '201'}).json()
    lid = lesson['id']
    assert c.post(f'/api/v1/instructor/lessons/{lid}/start', headers=h['teacher1']).status_code == 200
    issued = c.post(f'/api/v1/student/lessons/{lid}/next', headers=h['student1']).json()
    return lid, issued


def test_prefilled_phone_must_belong_to_actual_recipient(classroom):
    import pytest
    scenario = classroom['store'].scenario(classroom['scenario_id'])
    scenario['owner_service'] = 'Служба 101'
    scenario['prefilled_card'] = {'services': ['Служба 101'],
                                   'service_phones': {'Служба 103': '+7 900 000-00-03'}}
    with pytest.raises(ValueError, match='только для служб'):
        prefilled_from_scenario(scenario)


def test_incoming_card_keeps_structured_facts_and_sip_for_briefing(classroom):
    lid, card = prepared(classroom)
    assert card['transport'] == 'text'  # No second victim call for incoming data.
    assert card['sip_extension'] == '201'  # Outgoing DDS briefing remains possible.
    assert card['exercise_mode'] == 'actions'
    assert card['card']['street'] == 'Берзарина'
    assert card['card']['house'] == '21'
    assert card['card']['building'] == '2'
    assert card['card']['apartment'] == '17'
    assert card['card']['incident_type'] == 'Пожар в квартире'
    assert card['card']['services'] == ['Служба 101', 'Служба 103']
    assert card['card']['service_phones'] == {'Служба 101': '+7 900 000-00-01'}
    assert card['owner_service'] == 'Служба 101'
    assert card['crew_options'][0]['id'] == 'Наряд 17'
    assert 'dds_expectation' not in card
    assert card['dds_assessment_enabled'] is True
    assert card['service_states']['Служба 101']['status'] == 'Добавлена'


def test_teacher_session_list_uses_dds_score(classroom):
    _, card = prepared(classroom)
    store = classroom['store']
    value = json.loads(store.db.execute('SELECT body FROM workspace WHERE id=?', (card['id'],)).fetchone()[0])
    value.update(status='Завершена', dds_review={'score_percent': 75}, evaluation={'score_percent': None})
    with store.db:
        store.db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), card['id']))
    response = classroom['client'].get('/api/v1/instructor/sessions', headers=classroom['headers']['teacher1'])
    assert response.status_code == 200
    assert next(item for item in response.json() if item['id'] == card['id'])['score_percent'] == 75


def test_opening_clock_and_early_finish_guard(classroom):
    lid, card = prepared(classroom)
    c, h, store = classroom['client'], classroom['headers'], classroom['store']
    sid = card['id']
    path = f'/api/v1/student/sessions/{sid}'
    assert c.post(path + '/finish', headers=h['student1']).status_code == 409
    # The opening clock starts when the row is issued and stops on opening.
    row = store.db.execute('SELECT body FROM workspace WHERE id=?', (sid,)).fetchone()
    value = json.loads(row[0])
    value['created_at'] = (datetime.now(timezone.utc) - timedelta(seconds=35)).isoformat()
    with store.db:
        store.db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), sid))
    opened = c.post(path + '/open', headers=h['student1'])
    assert opened.status_code == 200
    assert opened.json()['opened_at']
    assert opened.json()['service_states']['Служба 101']['status'] == 'Получена службой'
    assert not opened.json().get('receipt_decided_at')
    assert c.post(path + '/open', headers=h['student1']).json()['opened_at'] == opened.json()['opened_at']
    assert c.post(path + '/finish', headers=h['student1']).status_code == 409
    accepted = c.post(path + '/services', headers=h['student1'], json={
        'service': 'Служба 101', 'status': 'Принята', 'comment': 'Принято в работу'})
    assert accepted.status_code == 200
    assert accepted.json()['accepted_at'] == accepted.json()['receipt_decided_at']
    teacher = c.post(f'/api/v1/instructor/sessions/{sid}/finish',
                     headers=h['teacher1'], json={'reason': 'Остановлено преподавателем'})
    assert teacher.status_code == 200
    result = teacher.json()['dds_review']
    reaction = next(check for check in result['checks'] if check['id'] == 'receipt_time')
    assert reaction['passed'] is False
    assert teacher.json()['evaluation']['timing']['response_seconds'] >= 35
    assert result['passed'] is False
    report = c.get(f'/api/v1/instructor/lessons/{lid}/report', headers=h['teacher1']).json()
    assert report['participants'][0]['cards'][0]['score_percent'] == result['score_percent']


def test_receipt_norm_ends_at_opening_and_first_record_has_own_norm(classroom):
    """30 с — до открытия карточки, 3 мин — до первой записи."""
    _, card = prepared(classroom)
    c, h, store = classroom['client'], classroom['headers'], classroom['store']
    sid = card['id']
    path = f'/api/v1/student/sessions/{sid}'
    assert c.post(path + '/open', headers=h['student1']).status_code == 200
    row = store.db.execute('SELECT body FROM workspace WHERE id=?', (sid,)).fetchone()
    value = json.loads(row[0])
    value['created_at'] = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    value['opened_at'] = (datetime.now(timezone.utc) - timedelta(seconds=190)).isoformat()
    with store.db:
        store.db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), sid))
    assert c.post(path + '/services', headers=h['student1'], json={
        'service': 'Служба 101', 'status': 'Принята', 'comment': 'Принято в работу'}).status_code == 200
    finished = c.post(f'/api/v1/instructor/sessions/{sid}/finish',
                      headers=h['teacher1'], json={'reason': 'Стоп'}).json()
    timing = finished['evaluation']['timing']
    assert finished['opening_seconds'] == 10
    assert timing['response_seconds'] == 10 and timing['response_within_limit'] is True
    assert timing['first_record_seconds'] >= 200 and timing['within_limit'] is False
    checks = {item['id']: item['passed'] for item in finished['dds_review']['checks']}
    assert checks['receipt_time'] is True
    assert checks['first_record'] is False
    assert 'handling' not in checks


def test_dds_status_needs_text_and_cannot_skip_stages(classroom):
    _, card = prepared(classroom)
    c, h = classroom['client'], classroom['headers']['student1']
    path = f"/api/v1/student/sessions/{card['id']}"
    assert c.post(path + '/open', headers=h).status_code == 200
    empty = c.post(path + '/services', headers=h, json={
        'service': 'Служба 101', 'status': 'Принята', 'comment': ' '})
    assert empty.status_code == 422
    accepted = c.post(path + '/services', headers=h, json={
        'service': 'Служба 101', 'status': 'Принята', 'comment': 'Принято в работу'}).json()
    assert accepted['first_record_at']
    assert accepted['allowed_service_statuses']['Служба 101'] == [
        'Начало реагирования', 'Отказ от выполнения работ']
    assert c.post(path + '/crew', headers=h, json={
        'message_id': '00000000-0000-4000-8000-000000000016',
        'crew_id': 'Наряд 17', 'decision_by': 'dispatcher'}).status_code == 200
    skipped = c.post(path + '/services', headers=h, json={
        'service': 'Служба 101', 'status': 'Прибытие', 'comment': 'Прибыли'})
    assert skipped.status_code == 409


def test_timely_acceptance_satisfies_receipt_norm(classroom):
    _, card = prepared(classroom)
    c, h = classroom['client'], classroom['headers']
    sid = card['id']
    assert c.post(f'/api/v1/student/sessions/{sid}/open', headers=h['student1']).status_code == 200
    assert c.post(f'/api/v1/student/sessions/{sid}/services', headers=h['student1'], json={
        'service': 'Служба 101', 'status': 'Принята', 'comment': 'Принято в работу'}).status_code == 200
    finished = c.post(f'/api/v1/instructor/sessions/{sid}/finish',
                      headers=h['teacher1'], json={'reason': 'Стоп'}).json()
    assert finished['evaluation']['timing']['response_within_limit'] is True
    assert next(item for item in finished['dds_review']['checks']
                if item['id'] == 'receipt_time')['passed'] is True


def test_dds_cannot_change_recipient_list_or_contact_directory(classroom):
    _, card = prepared(classroom)
    c, h = classroom['client'], classroom['headers']['student1']
    path = f"/api/v1/student/sessions/{card['id']}"
    altered = {**card['card'], 'services': [*card['card']['services'], 'Служба 102']}
    assert c.put(path + '/card', headers=h,
                 json={'revision': card['revision'], 'card': altered}).status_code == 403
    altered = {**card['card'], 'service_phones': {'Служба 103': '123'}}
    assert c.put(path + '/card', headers=h,
                 json={'revision': card['revision'], 'card': altered}).status_code == 403
    assert c.post(path + '/forward', headers=h, json={
        'message_id': '00000000-0000-4000-8000-000000000001',
        'service': 'Служба 102', 'reason': 'Требуется помощь'}).status_code == 403


def test_dds_contact_without_number_is_view_only(classroom):
    async def voice(_path, _method, _body):
        return {'call_id': '00000000-0000-4000-8000-000000000004'}
    classroom['client'].app.include_router(briefing_router(
        classroom['store'], Accounts(classroom['store']), lambda: None, voice=voice))
    _, card = prepared(classroom)
    c, h = classroom['client'], classroom['headers']['student1']
    path = f"/api/v1/student/sessions/{card['id']}"
    response = c.post(path + '/briefings', headers=h, json={
        'message_id': '00000000-0000-4000-8000-000000000002',
        'service': 'Служба 103', 'transport': 'text'})
    assert response.status_code == 409
    response = c.post(path + '/briefings', headers=h, json={
        'message_id': '00000000-0000-4000-8000-000000000003',
        'service': 'Служба 101', 'phone': '+7 900 000-00-01',
        'transport': 'text'})
    assert response.status_code == 409
    response = c.post(path + '/briefings', headers=h, json={
        'message_id': '00000000-0000-4000-8000-000000000005',
        'service': 'Служба 101', 'phone': '+7 900 000-00-01',
        'transport': 'sip'})
    assert response.status_code == 201


def test_crew_is_selected_after_acceptance_and_before_departure(classroom):
    _, card = prepared(classroom)
    c, h = classroom['client'], classroom['headers']['student1']
    path = f"/api/v1/student/sessions/{card['id']}"
    body = {'message_id': '00000000-0000-4000-8000-000000000006',
            'crew_id': 'Наряд 17', 'decision_by': 'dispatcher'}
    assert c.post(path + '/crew', headers=h, json=body).status_code == 409
    assert c.post(path + '/open', headers=h).status_code == 200
    assert c.post(path + '/services', headers=h, json={
        'service': 'Служба 101', 'status': 'Принята', 'comment': 'Принято в работу'}).status_code == 200
    selected = c.post(path + '/crew', headers=h, json=body)
    assert selected.status_code == 200, selected.text
    assert selected.json()['assigned_crew']['leader'] == 'Старший наряда'
    assert c.post(path + '/crew', headers=h, json=body).status_code == 200


def test_teacher_stop_hangs_up_field_report_and_briefing(classroom, monkeypatch):
    calls = []
    async def voice(path, method='GET', body=None):
        calls.append(path)
        return {'status':'ended'}
    monkeypatch.setattr('workspace.voice_request', voice)
    store = classroom['store']
    classroom['client'].app.include_router(briefing_router(store, Accounts(store), lambda: None, voice=voice))
    _, card = prepared(classroom)
    value = json.loads(store.db.execute('SELECT body FROM workspace WHERE id=?', (card['id'],)).fetchone()[0])
    value['field_report_calls'] = {'update': {'call_id':'report-call', 'session_id':'report-session'}}
    with store.db:
        store.db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), card['id']))
        store.db.execute('INSERT INTO briefings VALUES (?,?,?,?,?)', ('brief', card['id'], value['student_id'], 'message', json.dumps({'state':'open','call_id':'brief-call'})))
    response = classroom['client'].post(f"/api/v1/instructor/sessions/{card['id']}/finish",
        headers=classroom['headers']['teacher1'], json={'reason':'Остановка преподавателем'})
    assert response.status_code == 200, response.text
    assert {'calls/report-call/hangup', 'calls/brief-call/hangup'} <= set(calls)


def test_dds_reports_card_error_to_112_instead_of_editing(classroom):
    """ДДС правит только свои поля, об ошибке сообщает в 112."""
    _, card = prepared(classroom)
    c, h = classroom['client'], classroom['headers']['student1']
    path = f"/api/v1/student/sessions/{card['id']}"
    edited = {**card['card'], 'house': '22'}
    denied = c.put(path + '/card', headers=h, json={'revision': card['revision'], 'card': edited})
    assert denied.status_code == 403 and 'house' in denied.json()['detail']
    marked = c.put(path + '/card', headers=h,
                   json={'revision': card['revision'], 'card': {**card['card'], 'bookmarked': True}})
    assert marked.status_code == 200, marked.text
    body = {'message_id': '00000000-0000-4000-8000-000000000021', 'field': 'house',
            'correct_value': '22', 'source': 'Старший наряда', 'recipient': 'Оператор 112 Иванова'}
    reported = c.post(path + '/error-reports', headers=h, json=body)
    assert reported.status_code == 200, reported.text
    value = reported.json()
    assert value['card']['house'] == '21'  # Карточку 112 меняет только 112.
    assert value['error_reports'][0]['card_value'] == '21'
    assert value['error_reports'][0]['correct_value'] == '22'
    assert any(e['type'] == 'card.error_reported' for e in value['events'])
    assert len(c.post(path + '/error-reports', headers=h, json=body).json()['error_reports']) == 1
    assert c.post(path + '/error-reports', headers=h,
                  json={**body, 'correct_value': '23'}).status_code == 409
    assert c.post(path + '/error-reports', headers=h,
                  json={**body, 'message_id': '00000000-0000-4000-8000-000000000022',
                        'field': 'caller_name'}).status_code == 422


def test_correct_facts_come_from_the_brigade():
    from workspace import correction_evidence_visible
    value = {'exercise_mode': 'actions', 'crew_options': [{'id': '1'}], 'accepted_at': 'x', 'events': []}
    assert correction_evidence_visible(value) is False
    value['events'].append({'type': 'progress.requested', 'detail': {}})
    assert correction_evidence_visible(value) is True
    value = {'exercise_mode': 'actions', 'planned_unlocks': {'a': 'Прибытие'}, 'events': [
        {'type': 'progress.requested', 'detail': {}}, {'type': 'field_report.call_started', 'detail': {}}]}
    # Запрос без нового доклада и непрослушанный звонок не раскрывают эталон.
    assert correction_evidence_visible(value) is False
    value['events'].append({'type': 'situation.update', 'detail': {'id': 'a', 'unlocks_status': 'Прибытие'}})
    assert correction_evidence_visible(value) is True
    # Без бригады и докладов источник — вводная после приёма карточки.
    assert correction_evidence_visible({'exercise_mode': 'actions', 'events': []}) is False
    assert correction_evidence_visible({'exercise_mode': 'actions', 'events': [], 'accepted_at': 'x'}) is True


def test_brigade_names_actual_address_when_card_is_wrong():
    from field_dialogue import report_context, fallback
    value = {'initial_card': {'street': 'Берзарина', 'house': '21'},
             'dds_expectation': {'expected_corrections': {'house': '22'}}, 'events': []}
    report = report_context(value, 'Старший наряда', 'Прибыли на место', update_id='arrived')
    assert report['card']['house'] == '22'
    assert '22' in fallback(report, 'Уточните адрес')


def test_dds_profile_limits_the_feed_to_its_service(classroom):
    """Ролевая модель: профиль ДДС отбирает только карточки своей службы."""
    store = classroom['store']
    base_scenario = store.scenario(classroom['scenario_id'])
    card = {'street': 'Учебная', 'house': '12', 'incident_type': 'Прорыв трубы',
            'services': ['Деп. ЖКХ']}
    utilities = {**base_scenario, 'id': 'profile-utilities', 'dds_profile': 'utilities',
                 'owner_service': 'Деп. ЖКХ', 'prefilled_card': card}
    fire = {**base_scenario, 'id': 'profile-fire', 'dds_profile': 'fire',
            'owner_service': 'Служба 101', 'prefilled_card': {**card, 'services': ['Служба 101']}}
    for item in (utilities, fire):
        store.put_scenario(Scenario.model_validate(item))
    lesson = create_lesson(classroom, mode='actions', scenario_ids=[], dds_profile='utilities')
    assert lesson.status_code in (200, 201), lesson.text
    chosen = lesson.json()['prefilled_scenario_ids']
    assert 'profile-utilities' in chosen and 'profile-fire' not in chosen
    wrong = create_lesson(classroom, mode='actions', scenario_ids=[], dds_profile='utilities',
                          prefilled_scenario_ids=['profile-fire'])
    assert wrong.status_code == 422
