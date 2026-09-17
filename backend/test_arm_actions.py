from uuid import uuid4

from test_rbac_integration import classroom


def test_processed_notifications_links_and_response_lifecycle(classroom):
    c = classroom; client = c['client']; h = c['headers']['student1']; s = c['session']
    url = '/api/v1/student/sessions/'+s['id']
    assert client.post(url+'/processed', headers=h).status_code == 409
    s['card']['services'] = ['Служба 101']
    saved = client.put(url+'/card', headers=h, json={'revision': 0, 'card': s['card']}).json()
    assert saved['service_states']['Служба 101']['status'] == 'Добавлена'
    assert saved['allowed_service_statuses']['Служба 101'] == ['Принята', 'Не принята']
    assert saved['incident_status'] == 'Зарегистрирована'
    notification = {'message_id': str(uuid4()), 'service': 'Служба 101', 'destination': 'Учебный диспетчер',
                    'phone': 'учебный', 'recipient': 'Тестовый сотрудник', 'comment': 'Информация передана в рамках тренировки'}
    result = client.post(url+'/notifications', headers=h, json=notification)
    assert result.status_code == 200, result.text
    assert client.post(url+'/notifications', headers=h, json=notification).json() == result.json()
    notification['comment'] = 'Иной текст'
    assert client.post(url+'/notifications', headers=h, json=notification).status_code == 409
    processed = client.post(url+'/processed', headers=h).json()
    assert processed['status'] != 'Завершена' and processed['incident_status'] == 'Отработана'
    assert client.post(url+'/processed', headers=h).json() == processed
    body = {'message_id': str(uuid4()), 'service': 'Служба 101', 'status': 'Не принята', 'comment': ''}
    assert client.post(url+'/services', headers=h, json=body).status_code == 422
    body.update(status='Принята', order_number='  Н-112  ', comment='')
    result = client.post(url+'/services', headers=h, json=body)
    assert result.status_code == 200, result.text
    assert result.json()['service_states']['Служба 101']['order_number'] == 'Н-112'
    service_event = result.json()['events'][-1]
    assert service_event['detail']['order_number'] == 'Н-112'
    teacher_view = client.get(
        '/api/v1/instructor/sessions/' + s['id'],
        headers=c['headers']['teacher1'],
    ).json()
    assert teacher_view['events'][-1]['detail']['order_number'] == 'Н-112'
    assert client.post(url+'/services', headers=h, json=body).json() == result.json()
    body.update(message_id=str(uuid4()), status='Работы завершены', comment='Учебные работы закончены')
    assert client.post(url+'/services', headers=h, json=body).json()['incident_status'] == 'Завершена'
    body.update(message_id=str(uuid4()), status='Принята')
    assert client.post(url+'/services', headers=h, json=body).status_code == 422
    too_long = {'service': 'Служба 101', 'status': 'Принята', 'order_number': '1' * 81}
    assert client.post(url+'/services', headers=h, json=too_long).status_code == 422
    # All previously notified services remain, even when omitted by a stale UI.
    s['card']['services'] = []
    saved = client.put(url+'/card', headers=h, json={'revision': 1, 'card': s['card']}).json()
    assert saved['card']['services'] == ['Служба 101']
    target = client.post('/api/v1/student/sessions', headers=h, json={'scenario_id': c['scenario_id'], 'assignment_id': c['assignment']['id']}).json()
    assert client.post(url+'/links', headers=h, json={'target_id': s['id']}).status_code == 422
    assert client.post(url+'/links', headers=h, json={'target_id': target['id']}).status_code == 200
    linked = client.post(url+'/links', headers=h, json={'target_id': target['id']}).json()
    assert linked['linked_cards'] == [{'id': target['id'], 'number': target['number']}]
    assert client.post(url+'/links', headers=c['headers']['student2'], json={'target_id': target['id']}).status_code == 404
    client.post(url+'/finish', headers=h)
    for suffix, payload in [('processed', {}), ('notifications', notification), ('links', {'target_id': target['id']})]:
        assert client.post(url+'/'+suffix, headers=h, json=payload).status_code == 409


def test_journal_pagination_is_owner_scoped(classroom):
    c = classroom; client = c['client']; h = c['headers']['student1']
    assert client.get('/api/v1/student/sessions?limit=1&offset=0', headers=h).json()[0]['id'] == c['session']['id']
    assert client.get('/api/v1/student/sessions?limit=1&offset=1', headers=h).json() == []
    assert client.get('/api/v1/student/sessions?limit=201', headers=h).status_code == 422
    assert client.get('/api/v1/student/sessions', headers=c['headers']['student2']).json() == []
