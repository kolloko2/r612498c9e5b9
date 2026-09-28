import json
from fastapi import HTTPException
from test_rbac_integration import classroom
from test_lesson_lifecycle import create_lesson
from practice_plan import draft, fingerprint


def approve_fixture(classroom):
    store = classroom['store']
    scenario = store.scenario(classroom['scenario_id'])
    scenario['practice_plan'] = draft(scenario)
    scenario['practice_approved_version'] = fingerprint(scenario)
    with store.db:
        store.db.execute('UPDATE scenarios SET body=? WHERE id=?', (json.dumps(scenario), scenario['id']))
    # This test fixture predates issuing an approved snapshot.
    state = store.load(classroom['session']['id'])
    state['scenario'] = scenario
    store.save(classroom['session']['id'], state)


def test_assignment_practice_and_restart_preserves_history(classroom):
    approve_fixture(classroom)
    c, h = classroom['client'], classroom['headers']
    sid = classroom['session']['id']
    assert classroom['session']['practice_with_hints'] is False
    url = '/api/v1/student/sessions/' + sid + '/restart'
    assert c.post(url, headers=h['student2']).status_code == 404
    assert c.post('/api/v1/instructor/sessions/'+sid+'/restart', headers=h['teacher2']).status_code == 404
    assignment = classroom['assignment']
    assert c.patch('/api/v1/instructor/assignments/'+assignment['id'], headers=h['teacher1'],
                   json={'active': True, 'practice_with_hints': True}).status_code == 200
    restarted = c.post(url, headers=h['student1'])
    assert restarted.status_code == 201, restarted.text
    new = restarted.json()
    assert new['id'] != sid and new['attempt_number'] == 2
    assert new['practice_with_hints'] is True
    assert new['restarted_from'] == sid
    assert new['card']['description'] == ''
    assert c.post(url, headers=h['student1']).json()['id'] == new['id']
    old = c.get('/api/v1/student/sessions/'+sid, headers=h['student1']).json()
    assert old['status'] == 'Завершена' and old['attempt_outcome'] == 'restarted'
    assert old['restarted_to'] == new['id']
    assert any(e['type'] == 'session.restarted' for e in old['events'])
    report = c.get('/api/v1/instructor/sessions', headers=h['teacher1']).json()
    assert next(v for v in report if v['id'] == new['id'])['attempt_number'] == 2


def test_unapproved_practice_is_rejected_before_issuance(classroom):
    c, h = classroom['client'], classroom['headers']
    response = c.patch('/api/v1/instructor/assignments/'+classroom['assignment']['id'],
                       headers=h['teacher1'], json={'active': True, 'practice_with_hints': True})
    assert response.status_code == 409
    assert create_lesson(classroom, practice_with_hints=True).status_code == 409


def test_lesson_policy_and_same_slot_restart(classroom):
    approve_fixture(classroom)
    c, h = classroom['client'], classroom['headers']
    lesson = create_lesson(classroom, practice_with_hints=True).json()
    lid = lesson['id']
    teacher = '/api/v1/instructor/lessons/'+lid
    assert c.post(teacher+'/start', headers=h['teacher1']).status_code == 200
    next_url = '/api/v1/student/lessons/'+lid+'/next'
    first = c.post(next_url, headers=h['student1']).json()
    assert first['practice_with_hints'] is True
    visible = next(item for item in c.get('/api/v1/student/lessons', headers=h['student1']).json() if item['id'] == lid)
    assert visible['restart_session_id'] == first['id']
    policy = teacher+'/practice'
    assert c.put(policy, headers=h['student1'], json={'practice_with_hints': False}).status_code == 403
    assert c.put(policy, headers=h['teacher1'], json={'practice_with_hints': False}).status_code == 200
    assert c.get('/api/v1/student/sessions/'+first['id'], headers=h['student1']).json()['practice_with_hints'] is False
    assert c.put(teacher+'/guided-step', headers=h['teacher1'], json={'step': 1}).status_code == 409
    new = c.post('/api/v1/instructor/sessions/'+first['id']+'/restart', headers=h['teacher1'])
    assert new.status_code == 201, new.text
    assert new.json()['scenario_id'] == first['scenario_id']
    assert new.json()['lesson_position'] == first['lesson_position']
    assert c.post(next_url, headers=h['student1']).json()['id'] == new.json()['id']
    summary = c.get('/api/v1/student/lessons', headers=h['student1']).json()[0]
    assert summary['completed'] == 0 and not summary['exhausted']
    assert summary['active_session_id'] == new.json()['id']
    assert summary['restart_session_id'] == new.json()['id']


def test_dds_restart_resets_actions_and_requires_successful_hangup(classroom, monkeypatch):
    from test_dds_lesson_contract import prepared
    _, first = prepared(classroom)
    c, h, store = classroom['client'], classroom['headers'], classroom['store']
    source = json.loads(store.db.execute('SELECT body FROM workspace WHERE id=?', (first['id'],)).fetchone()[0])
    source.update(call_id='synthetic-call', assigned_crew={'id':'Наряд 17'}, opened_at=source['created_at'])
    source['service_states']['Служба 101']['status'] = 'Принята'
    with store.db:
        store.db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(source), first['id']))
    async def unavailable(*args, **kwargs):
        raise HTTPException(503, 'Voice unavailable')
    monkeypatch.setattr('workspace.voice_request', unavailable)
    url = '/api/v1/student/sessions/'+first['id']+'/restart'
    assert c.post(url, headers=h['student1']).status_code == 503
    assert c.get('/api/v1/student/sessions/'+first['id'], headers=h['student1']).json()['status'] != 'Завершена'
    async def hangup(*args, **kwargs):
        return {'status': 'ended'}
    monkeypatch.setattr('workspace.voice_request', hangup)
    response = c.post(url, headers=h['student1'])
    assert response.status_code == 201, response.text
    fresh = response.json()
    assert fresh['card'] == first['card'] and fresh['sip_extension'] == '201'
    assert fresh['service_states']['Служба 101']['status'] == 'Добавлена'
    assert not fresh.get('assigned_crew') and not fresh.get('opened_at') and fresh['call_id'] is None
