from test_assessment import configure, finish, grading, new_card  # noqa: F401 - фикстуры
from test_rbac_integration import classroom  # noqa: F401 - фикстура


def test_review_joins_task_timeline_decision_and_grade(grading):  # noqa: F811
    c = grading
    configure(c)
    client, h = c['client'], c['headers']
    card = new_card(c)
    assert client.put('/api/v1/student/sessions/' + card['id'] + '/card', headers=h['student1'],
                      json={'revision': 0, 'card': {**card['card'], 'caller_name': 'Иванов'}}).status_code == 200
    in_progress = client.get('/api/v1/student/sessions/' + card['id'] + '/review', headers=h['student1']).json()
    assert in_progress['assessment'] is None and in_progress['status'] != 'Завершена'
    finish(c, card)
    review = client.get('/api/v1/student/sessions/' + card['id'] + '/review', headers=h['student1']).json()
    assert review['task']['mode'].startswith('Полный цикл 112')
    labels = [row['label'] for row in review['timeline']]
    assert labels[0] == 'Карточка поступила' and 'Карточка сохранена' in labels and labels[-1] == 'Попытка завершена'
    assert all(row['offset_seconds'] is not None for row in review['timeline'])
    saved = next(row for row in review['timeline'] if row['type'] == 'card.saved')
    assert saved['actor'] == 'student'
    criterion = review['decision']['criteria'][0]
    assert criterion['label'] == 'Заявитель' and criterion['expected'] == ['Учебный'] and criterion['passed'] is False
    assert {'label': 'Заявитель', 'value': 'Иванов'} in review['decision']['card']
    assert review['assessment']['effective']['score_percent'] == 0
    assert any(n['label'] == 'Лимит карточки' and n['limit_seconds'] == 120 for n in review['norms'])
    teacher = client.get('/api/v1/instructor/sessions/' + card['id'] + '/review', headers=h['teacher1']).json()
    assert teacher['student_name'] == 'Обучающийся 1' and teacher['timeline'] == review['timeline']
    assert client.get('/api/v1/student/sessions/' + card['id'] + '/review', headers=h['student2']).status_code == 404
    assert client.get('/api/v1/instructor/sessions/' + card['id'] + '/review', headers=h['teacher2']).status_code == 404
    # Запись звонка выдаётся только для звонков этой попытки.
    stranger = '/api/v1/student/sessions/' + card['id'] + '/calls/00000000-0000-4000-8000-000000000000/recording'
    assert client.get(stranger, headers=h['student1']).status_code == 404
