from test_rbac_integration import classroom  # noqa: F401


def test_preview_is_role_scoped_and_has_no_hidden_answers(classroom):
    c = classroom
    body = {'card': {'description': 'Проишествие'}, 'comment': 'Работы  завершены'}
    for role, namespace in [('student1', 'student'), ('teacher1', 'instructor')]:
        response = c['client'].post(f'/api/v1/{namespace}/grammar/preview',
                                    headers=c['headers'][role], json=body)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report['errors'] == 2
        assert report['typos'] == []
    denied = c['client'].post('/api/v1/instructor/grammar/preview',
                              headers=c['headers']['student1'], json=body)
    assert denied.status_code == 403
