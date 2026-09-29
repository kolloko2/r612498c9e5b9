from test_assessment import configure, grading  # noqa: F401 - фикстуры
from test_lesson_lifecycle import create_lesson
from test_rbac_integration import classroom  # noqa: F401 - фикстура


def test_lesson_norm_and_pass_score_apply_to_issued_cards(grading):  # noqa: F811
    c = grading
    configure(c)
    client, h = c['client'], c['headers']
    assert create_lesson(c, norm_seconds=5).status_code == 422
    lesson = create_lesson(c, norm_seconds=45, pass_score_percent=100).json()
    assert lesson['norm_seconds'] == 45 and lesson['pass_score_percent'] == 100
    assert client.post('/api/v1/instructor/lessons/' + lesson['id'] + '/start', headers=h['teacher1']).status_code == 200
    card = client.post('/api/v1/student/lessons/' + lesson['id'] + '/next', headers=h['student1']).json()
    assert card['response_limit_seconds'] == 45
    assert client.put('/api/v1/student/sessions/' + card['id'] + '/card', headers=h['student1'],
                      json={'revision': card['revision'], 'card': {**card['card'], 'caller_name': 'Учебный'}}).status_code == 200
    done = client.post('/api/v1/student/sessions/' + card['id'] + '/finish', headers=h['student1']).json()
    assert done['evaluation']['timing']['response_limit_seconds'] == 45
    assert done['evaluation']['timing']['response_within_limit'] is True
    assert done['lesson_verdict'] == {'threshold': 100, 'score_percent': 100.0, 'passed': True}
    progress = client.get('/api/v1/student/statistics', headers=h['student1']).json()['progress']
    assert progress[-1]['passed'] is True
