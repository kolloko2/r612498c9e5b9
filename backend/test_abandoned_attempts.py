import json
from datetime import datetime, timedelta, timezone

from test_rbac_integration import classroom
from test_lesson_lifecycle import create_lesson


def sweep(client):
    handler = next(h for h in client.app.router.on_startup if h.__name__ == 'start_sweep')
    return handler.close_abandoned


def age(store, sid, minutes, action_minutes=None):
    value = json.loads(store.db.execute('SELECT body FROM workspace WHERE id=?', (sid,)).fetchone()[0])
    old = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    value['created_at'] = old.isoformat()
    for event in value['events']:
        event['at'] = old.isoformat()
    if action_minutes is not None:
        acted = datetime.now(timezone.utc) - timedelta(minutes=action_minutes)
        value['events'].append({'seq': len(value['events']) + 1, 'at': acted.isoformat(),
                                'type': 'card.saved', 'detail': {}})
    with store.db:
        store.db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), sid))


def test_idle_lesson_attempt_is_closed_at_last_action(classroom):
    c, h, store = classroom['client'], classroom['headers'], classroom['store']
    lesson = create_lesson(classroom).json()
    assert c.post('/api/v1/instructor/lessons/' + lesson['id'] + '/start', headers=h['teacher1']).status_code == 200
    card = c.post('/api/v1/student/lessons/' + lesson['id'] + '/next', headers=h['student1']).json()
    close = sweep(c)

    # Активная попытка (последнее действие 10 минут назад) не закрывается.
    age(store, card['id'], 300, action_minutes=10)
    assert c.portal.call(close) == 0
    assert c.get('/api/v1/student/sessions/' + card['id'], headers=h['student1']).json()['status'] != 'Завершена'

    # Два часа без действий: попытка закрыта системой, время — до последнего действия.
    age(store, card['id'], 300, action_minutes=200)
    assert c.portal.call(close) == 1
    closed = c.get('/api/v1/student/sessions/' + card['id'], headers=h['student1']).json()
    assert closed['status'] == 'Завершена'
    assert closed['attempt_outcome'] == 'timed_out'
    assert closed['completed_by']['role'] == 'system'
    assert 5900 <= closed['elapsed_seconds'] <= 6100
    assert c.portal.call(close) == 0
