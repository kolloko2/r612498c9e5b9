import json
from test_rbac_integration import classroom  # noqa: F401
from test_situation_updates import plant, prepare_own_service, OPERATIONAL
from workspace import apply_default_norms, update_elapsed
from datetime import datetime, timedelta, timezone


def prepare(c, elapsed=50, sip=False):
    sid = c['session']['id']
    prepare_own_service(c['store'], sid)
    plant(c['store'], sid, OPERATIONAL, created_shift_seconds=elapsed)
    db = c['store'].db
    value = json.loads(db.execute('SELECT body FROM workspace WHERE id=?', (sid,)).fetchone()[0])
    value.update(exercise_mode='actions', assigned_crew={'id': '17', 'leader': 'Старший', 'phone': '205'})
    if sip:
        value['sip_extension'] = '201'
    with db:
        db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), sid))
    return sid


def test_outgoing_query_reveals_only_due_fact_once(classroom):
    c = classroom
    sid = prepare(c)
    url = f'/api/v1/student/sessions/{sid}/progress'
    response = c['client'].post(url, headers=c['headers']['student1'])
    assert response.status_code == 200, response.text
    assert 'Наряд направлен' in response.json()['progress_message']
    assert 'прибыл' not in response.json()['progress_message']
    again = c['client'].post(url, headers=c['headers']['student1']).json()
    assert len([e for e in again['events'] if e['type'] == 'situation.update']) == 1
    assert c['client'].post(url, headers=c['headers']['student2']).status_code == 404


def test_early_query_does_not_unlock_status(classroom):
    c = classroom
    sid = prepare(c, elapsed=5)
    result = c['client'].post(f'/api/v1/student/sessions/{sid}/progress', headers=c['headers']['student1']).json()
    assert 'Новых сведений' in result['progress_message']
    assert not [e for e in result['events'] if e['type'] == 'situation.update']


def test_sip_query_waits_for_playback(classroom, monkeypatch):
    async def voice(*args):
        return {'call_id': 'progress-test-call', 'status': 'ringing'}
    monkeypatch.setattr('workspace.voice_request', voice)
    c = classroom
    sid = prepare(c, sip=True)
    response = c['client'].post(f'/api/v1/student/sessions/{sid}/progress', headers=c['headers']['student1'])
    assert response.status_code == 200, response.text
    assert not [e for e in response.json()['events'] if e['type'] == 'situation.update']
    assert c['client'].post(f'/api/v1/student/sessions/{sid}/updates/dispatched/confirm', headers=c['headers']['student1']).status_code == 409


def test_dds_norms_have_no_overall_handling_limit():
    # Работы могут идти часами: нормируется только первая запись (3 мин).
    value = {'exercise_mode': 'actions', 'owner_service': 'Служба 101', 'first_record_seconds': 40,
             'evaluation': {'timing': {'elapsed_seconds': 7200, 'response_seconds': 15}}}
    apply_default_norms(value)
    timing = value['evaluation']['timing']
    assert timing['within_limit'] is True and timing['limit_seconds'] == 180
    assert timing['first_record_seconds'] == 40
    assert timing['response_limit_seconds'] == 30 and timing['response_within_limit'] is True


def test_operational_reports_start_after_completed_briefing_not_assignment():
    timestamp = datetime.now(timezone.utc)
    value = {'created_at': (timestamp - timedelta(seconds=100)).isoformat(),
             'updates_anchor': 'crew_assigned'}
    crew_report = {'unlocks_status': 'Прибытие'}
    assert update_elapsed(value, crew_report) == -1
    assert update_elapsed(value, {}) >= 100
    value['assigned_crew'] = {'id': '17', 'at': (timestamp - timedelta(seconds=50)).isoformat()}
    assert update_elapsed(value, crew_report) == -1
    value['events'] = [{'type': 'briefing.started', 'at': timestamp.isoformat()}]
    assert update_elapsed(value, crew_report) == -1
    event = {'type': 'notification.recorded', 'at': (timestamp - timedelta(seconds=5)).isoformat(),
             'detail': {'source': 'briefing', 'counterpart': 'crew', 'crew_id': 'other'}}
    value['events'].append(event)
    assert update_elapsed(value, crew_report) == -1
    event['detail']['crew_id'] = '17'
    assert 5 <= update_elapsed(value, crew_report) < 8
    del value['updates_anchor']
    assert update_elapsed(value, crew_report) >= 100


def test_next_report_waits_until_previous_is_reflected():
    """Доклады бригады не идут подряд: следующий — после статуса по предыдущему и паузы."""
    from workspace import report_due, REPORT_GAP_SECONDS
    start = datetime.now(timezone.utc) - timedelta(seconds=600)
    planned = [{'id': 'go', 'after_seconds': 10, 'unlocks_status': 'Начало реагирования'},
               {'id': 'there', 'after_seconds': 20, 'unlocks_status': 'Прибытие'}]
    value = {'created_at': start.isoformat(), 'owner_service': 'Деп. ЖКХ', 'events': [
        {'type': 'situation.update', 'at': (start + timedelta(seconds=15)).isoformat(), 'detail': {'id': 'go'}}]}
    assert report_due(value, planned[0], planned)
    assert not report_due(value, planned[1], planned)  # статус по «выехали» ещё не поставлен
    just_now = datetime.now(timezone.utc) - timedelta(seconds=REPORT_GAP_SECONDS // 2)
    value['events'].append({'type': 'service.updated', 'at': just_now.isoformat(),
                            'detail': {'service': 'Деп. ЖКХ', 'status': 'Начало реагирования'}})
    assert not report_due(value, planned[1], planned)  # пауза ещё не прошла
    value['events'][-1]['at'] = (datetime.now(timezone.utc) - timedelta(seconds=REPORT_GAP_SECONDS + 1)).isoformat()
    assert report_due(value, planned[1], planned)
