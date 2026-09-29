from test_lesson_lifecycle import create_lesson
from test_rbac_integration import classroom  # noqa: F401 - фикстура


def test_browser_phone_credentials_only_for_assigned_number(classroom, monkeypatch):  # noqa: F811
    c, h = classroom['client'], classroom['headers']
    requested = []

    async def voice(path, method='GET', body=None):
        requested.append(path)
        return {'extension': path.rsplit('/', 1)[-1], 'username': 'w' + path.rsplit('/', 1)[-1], 'password': 'x' * 32}
    monkeypatch.setattr('workspace.voice_request', voice)
    student = classroom['users']['student1']['id']
    # Без назначенного номера телефон в браузере недоступен и Voice не спрашивается.
    assert c.get('/api/v1/student/softphone', headers=h['student1']).json()['enabled'] is False
    first = create_lesson(classroom, title='Занятие 201', transport='sip', sip_extensions={student: '201'}).json()
    second = create_lesson(classroom, title='Занятие 205', transport='sip', sip_extensions={student: '205'}).json()
    assert requested == []
    chosen = c.get('/api/v1/student/softphone?lesson_id=' + first['id'], headers=h['student1']).json()
    assert chosen['enabled'] and chosen['extension'] == '201' and chosen['username'] == 'w201'
    assert chosen['ws_path'] == '/sip-ws' and chosen['lesson_title'] == 'Занятие 201'
    assert c.get('/api/v1/student/softphone?lesson_id=' + second['id'], headers=h['student1']).json()['extension'] == '205'
    # Другой обучающийся не получает чужой номер, преподаватель — вообще не обучающийся.
    assert c.get('/api/v1/student/softphone?lesson_id=' + first['id'], headers=h['student2']).json()['enabled'] is False
    assert c.get('/api/v1/student/softphone', headers=h['teacher1']).status_code == 403
    assert requested == ['webrtc/201', 'webrtc/205']
