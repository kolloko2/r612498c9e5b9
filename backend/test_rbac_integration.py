"""End-to-end RBAC checks across accounts, classroom, and workspace routers."""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DIALOGUE_DB", ":memory:")
os.environ["LLM_PROVIDER"] = "mock"

from accounts import Accounts
from learning import Learning
from server import Engine, Store
from workspace import router as workspace_router


PASSWORD = "integration-password"


def session_headers(token):
    return {"X-User-Session": token}


def login(client, username):
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200
    return session_headers(response.json()["session_token"])


@pytest.fixture
def classroom(monkeypatch):
    async def unexpected_model(_messages):
        raise AssertionError("the integration test must not call an LLM")

    async def unexpected_review(*_args):
        raise AssertionError("an unauthorized review reached the review provider")

    monkeypatch.setattr("workspace.review_card", unexpected_review)
    store = Store(":memory:")
    accounts = Accounts(store)
    learning = Learning(store, accounts)
    app = FastAPI()
    app.include_router(accounts.router(lambda: None))
    app.include_router(
        workspace_router(store, Engine(store, unexpected_model), lambda: None, accounts, learning)
    )
    app.include_router(learning.router(lambda: None))

    with TestClient(app) as client:
        bootstrap = client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "admin", "password": PASSWORD, "display_name": "Администратор"},
        )
        assert bootstrap.status_code == 201
        admin_headers = session_headers(bootstrap.json()["session_token"])
        users = {}
        for username, display_name, role in (
            ("teacher1", "Преподаватель 1", "teacher"),
            ("teacher2", "Преподаватель 2", "teacher"),
            ("student1", "Обучающийся 1", "student"),
            ("student2", "Обучающийся 2", "student"),
        ):
            response = client.post(
                "/api/v1/admin/users",
                headers=admin_headers,
                json={
                    "username": username,
                    "password": PASSWORD,
                    "display_name": display_name,
                    "role": role,
                },
            )
            assert response.status_code == 201
            users[username] = response.json()

        headers = {username: login(client, username) for username in users}
        scenario_id = store.first_enabled()["id"]
        group_response = client.post(
            "/api/v1/instructor/groups",
            headers=headers["teacher1"],
            json={"title": "Учебная группа"},
        )
        assert group_response.status_code == 201
        group = group_response.json()
        member_response = client.post(
            f"/api/v1/instructor/groups/{group['id']}/members",
            headers=headers["teacher1"],
            json={"student_id": users["student1"]["id"]},
        )
        assert member_response.status_code == 200
        assignment_response = client.post(
            "/api/v1/instructor/assignments",
            headers=headers["teacher1"],
            json={
                "group_id": group["id"],
                "scenario_id": scenario_id,
                "title": "Первое назначение",
            },
        )
        assert assignment_response.status_code == 201
        assignment = assignment_response.json()
        session_response = client.post(
            "/api/v1/student/sessions",
            headers=headers["student1"],
            json={"scenario_id": scenario_id, "assignment_id": assignment["id"]},
        )
        assert session_response.status_code == 201
        yield {
            "client": client,
            "store": store,
            "users": users,
            "headers": headers,
            "scenario_id": scenario_id,
            "group": group,
            "assignment": assignment,
            "session": session_response.json(),
        }
    store.db.close()


def test_real_accounts_provision_assignment_and_assigned_session(classroom):
    client = classroom["client"]
    assignment = classroom["assignment"]
    session = classroom["session"]
    student_headers = classroom["headers"]["student1"]

    visible = client.get("/api/v1/student/assignments", headers=student_headers)
    assert visible.status_code == 200
    assert [item["id"] for item in visible.json()] == [assignment["id"]]
    assert session["assignment_id"] == assignment["id"]
    assert session["student_id"] == classroom["users"]["student1"]["id"]
    assert session["teacher_id"] == classroom["users"]["teacher1"]["id"]


def test_other_student_cannot_discover_or_mutate_session(classroom):
    client = classroom["client"]
    session = classroom["session"]
    headers = classroom["headers"]["student2"]
    url = f"/api/v1/student/sessions/{session['id']}"

    assert client.get("/api/v1/student/sessions", headers=headers).json() == []
    assert client.get(url, headers=headers).status_code == 404
    assert client.put(
        url + "/card",
        headers=headers,
        json={"revision": session["revision"], "card": session["card"]},
    ).status_code == 404
    assert client.post(url + "/finish", headers=headers).status_code == 404
    assert client.post(url + "/ai-review", headers=headers).status_code == 404


def test_teacher_session_scope_and_student_instructor_denial(classroom):
    client = classroom["client"]
    session = classroom["session"]
    own = classroom["headers"]["teacher1"]
    other = classroom["headers"]["teacher2"]
    student = classroom["headers"]["student1"]
    detail_url = f"/api/v1/instructor/sessions/{session['id']}"
    rubric_url = f"/api/v1/instructor/scenarios/{classroom['scenario_id']}/rubric"

    listing = client.get("/api/v1/instructor/sessions", headers=own)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [session["id"]]
    assert client.get(detail_url, headers=own).status_code == 200
    assert client.get(detail_url, headers=other).status_code == 403
    assert client.get("/api/v1/instructor/groups", headers=student).status_code == 403
    assert client.get(rubric_url, headers=student).status_code == 403
    assert client.put(
        rubric_url, headers=student, json={"revision": 0, "rubric": None}
    ).status_code == 403


def test_teacher_rubrics_are_separate(classroom):
    client = classroom["client"]
    first = classroom["headers"]["teacher1"]
    second = classroom["headers"]["teacher2"]
    url = f"/api/v1/instructor/scenarios/{classroom['scenario_id']}/rubric"
    rubric = {
        "title": "Эталон преподавателя",
        "time_limit_seconds": 30,
        "criteria": [
            {
                "id": "street",
                "label": "Учебная улица",
                "field": "street",
                "mode": "equals",
                "expected": ["Тестовая улица"],
                "weight": 1,
            }
        ],
    }

    saved = client.put(url, headers=first, json={"revision": 0, "rubric": rubric})
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert client.get(url, headers=first).json()["rubric"]["title"] == rubric["title"]
    assert client.get(url, headers=second).json() == {"revision": 0, "rubric": None}


def test_teacher_feedback_is_scoped_idempotent_and_does_not_change_grade(classroom):
    from uuid import uuid4
    client=classroom['client']
    sid=classroom['session']['id']
    headers=classroom['headers']
    url=f'/api/v1/instructor/sessions/{sid}/feedback'
    body={'message_id':str(uuid4()),'text':'Уточните обстоятельства обращения.'}
    assert client.post(url,headers=headers['student1'],json=body).status_code==403
    assert client.post(url,headers=headers['teacher2'],json=body).status_code==404
    first=client.post(url,headers=headers['teacher1'],json=body)
    assert first.status_code==200 and first.json()['phase']=='active'
    assert client.post(url,headers=headers['teacher1'],json=body).json()==first.json()
    assert client.post(url,headers=headers['teacher1'],json={**body,'text':'Другой текст'}).status_code==409
    student_url=f'/api/v1/student/sessions/{sid}'
    current=client.get(student_url,headers=headers['student1']).json()
    assert current['card']==classroom['session']['card']
    assert current['revision']==0 and len(current['teacher_feedback'])==1
    saved=client.put(student_url+'/card',headers=headers['student1'],json={'revision':0,'card':current['card']})
    assert len(saved.json()['teacher_feedback'])==1
    finished=client.post(student_url+'/finish',headers=headers['student1']).json()
    result=client.post(url,headers=headers['teacher1'],json={'message_id':str(uuid4()),'text':'Разберите недостающие сведения.'})
    assert result.json()['phase']=='completed'
    after=client.get(student_url,headers=headers['student1']).json()
    assert after['evaluation']==finished['evaluation']
    assert after['card']==finished['card'] and after['revision']==finished['revision']
    assert len(after['teacher_feedback'])==2
    assert len([e for e in after['events'] if e['type']=='teacher.feedback'])==2
    assert client.post(url,headers=headers['teacher1'],json={'message_id':str(uuid4()),'text':'   '}).status_code==422


def test_teacher_finishes_owned_session_once(classroom):
    c=classroom['client']; h=classroom['headers']; session=classroom['session']; sid=session['id']
    url=f'/api/v1/instructor/sessions/{sid}/finish'
    body={'reason':'Учебное время завершено'}
    assert c.post(url,headers=h['teacher2'],json=body).status_code==404
    assert c.post(url,headers=h['student1'],json=body).status_code==403
    assert c.post(url,headers=h['teacher1'],json={'reason':' '}).status_code==422
    done=c.post(url,headers=h['teacher1'],json=body)
    assert done.status_code==200
    result=done.json()
    assert result['status']=='Завершена' and result['completed_by']['reason']==body['reason']
    assert result['card']==session['card'] and result['revision']==session['revision']
    assert c.post(url,headers=h['teacher1'],json={'reason':'Повтор'}).json()==result
    student_url=f'/api/v1/student/sessions/{sid}'
    assert c.post(student_url+'/finish',headers=h['student1']).json()==result
    assert c.put(student_url+'/card',headers=h['student1'],json={'revision':0,'card':session['card']}).status_code==409
    assert len([e for e in result['events'] if e['type']=='session.finished'])==1


def test_teacher_finish_voice_failure_keeps_session_active(classroom,monkeypatch):
    import json
    c=classroom['client']; store=classroom['store']; sid=classroom['session']['id']
    value=dict(classroom['session']); value['transport']='sip'; value['call_id']='synthetic-call'
    with store.db:
        store.db.execute('UPDATE workspace SET body=? WHERE id=?',(json.dumps(value),sid))
    monkeypatch.delenv('VOICE_API_TOKEN',raising=False)
    response=c.post(f'/api/v1/instructor/sessions/{sid}/finish',headers=classroom['headers']['teacher1'],json={'reason':'Остановить'})
    assert response.status_code==503
    saved=json.loads(store.db.execute('SELECT body FROM workspace WHERE id=?',(sid,)).fetchone()[0])
    assert saved['status']!='Завершена' and 'evaluation' not in saved
    assert not store.load(sid)['ended']


def test_group_lesson_start_queue_finish_and_isolation(classroom):
    c=classroom['client']; h=classroom['headers']
    body={'title':'Практика группы','group_id':classroom['group']['id'],'scenario_ids':[classroom['scenario_id']],'cards_per_student':2}
    assert c.post('/api/v1/instructor/lessons',headers=h['teacher2'],json=body).status_code==404
    r=c.post('/api/v1/instructor/lessons',headers=h['teacher1'],json=body)
    assert r.status_code==201
    lid=r.json()['id']; teacher=f'/api/v1/instructor/lessons/{lid}'; student=f'/api/v1/student/lessons/{lid}/next'
    assert c.post(student,headers=h['student1']).status_code==404
    assert c.post(teacher+'/start',headers=h['teacher2']).status_code==404
    assert c.post(teacher+'/start',headers=h['teacher1']).json()['state']=='running'
    assert c.post(teacher+'/start',headers=h['student1']).status_code==403
    assert c.get('/api/v1/student/lessons',headers=h['student2']).json()==[]
    first=c.post(student,headers=h['student1']).json()
    assert first['lesson_id']==lid
    assert c.post(student,headers=h['student1']).json()['id']==first['id']
    assert c.post(f"/api/v1/student/sessions/{first['id']}/finish",headers=h['student1']).status_code==200
    second=c.post(student,headers=h['student1']).json()
    assert second['id']!=first['id']
    assert c.post(teacher+'/finish',headers=h['teacher1'],json={'reason':'Конец практики'}).json()['state']=='finished'
    assert c.get(f"/api/v1/student/sessions/{second['id']}",headers=h['student1']).json()['status']=='Завершена'
    assert c.post(student,headers=h['student1']).status_code==409
    assert c.post(teacher+'/start',headers=h['teacher1']).status_code==409
    summary=c.get('/api/v1/student/lessons',headers=h['student1']).json()[0]
    assert summary['completed']==2 and 'scenario_ids' not in summary


def test_lesson_card_limit_and_empty_group(classroom):
    c=classroom['client']; h=classroom['headers']; teacher=h['teacher1']; student=h['student1']
    body={'title':'Одна карточка','group_id':classroom['group']['id'],'scenario_ids':[classroom['scenario_id']],'cards_per_student':1}
    lid=c.post('/api/v1/instructor/lessons',headers=teacher,json=body).json()['id']
    c.post(f'/api/v1/instructor/lessons/{lid}/start',headers=teacher)
    url=f'/api/v1/student/lessons/{lid}/next'
    card=c.post(url,headers=student).json()
    c.post(f"/api/v1/student/sessions/{card['id']}/finish",headers=student)
    assert c.post(url,headers=student).status_code==409
    group=c.post('/api/v1/instructor/groups',headers=teacher,json={'title':'Пустая группа'}).json()
    lid=c.post('/api/v1/instructor/lessons',headers=teacher,json={**body,'group_id':group['id']}).json()['id']
    assert c.post(f'/api/v1/instructor/lessons/{lid}/start',headers=teacher).status_code==409


def test_prefilled_lesson_copies_only_card_and_keeps_source_immutable(classroom):
    from uuid import uuid4
    c=classroom['client']; h=classroom['headers']; source=classroom['session']; source_url=f"/api/v1/student/sessions/{source['id']}"
    card={**source['card'],'description':'Учебная авария','services':['Служба 101']}
    c.put(source_url+'/card',headers=h['student1'],json={'revision':0,'card':card})
    original=c.post(source_url+'/finish',headers=h['student1']).json()
    body={'title':'Реагирование','group_id':classroom['group']['id'],'mode':'actions','source_session_ids':[source['id']],'cards_per_student':1}
    lesson=c.post('/api/v1/instructor/lessons',headers=h['teacher1'],json=body)
    assert lesson.status_code==201
    lid=lesson.json()['id'];c.post(f'/api/v1/instructor/lessons/{lid}/start',headers=h['teacher1'])
    attempt=c.post(f'/api/v1/student/lessons/{lid}/next',headers=h['student1']).json()
    assert attempt['id']!=source['id'] and attempt['exercise_mode']=='actions'
    assert attempt['card']==card and attempt['messages']==[]
    assert not attempt['assessment_enabled'] and 'evaluation' not in attempt and 'teacher_feedback' not in attempt
    assert 'templates' not in attempt and 'source_session_ids' not in attempt
    url=f"/api/v1/student/sessions/{attempt['id']}"
    assert c.post(url+'/messages',headers=h['student1'],json={'message_id':str(uuid4()),'text':'Назовите адрес'}).status_code==409
    assert c.post(url+'/services',headers=h['student1'],json={'service':'Служба 101','status':'Принята','comment':'Учебное сообщение принято'}).status_code==200
    result=c.post(url+'/finish',headers=h['student1']).json()
    assert result['action_report']['service_actions']==1
    assert result['evaluation']['score_percent'] is None
    # Балла у готовой карточки нет, но нормативы времени измеряются: реакция и
    # обработка — это то, что заказчик спрашивает именно в режиме ДДС.
    timing=result['evaluation']['timing']
    assert timing['response_limit_seconds']==30 and timing['limit_seconds']==180
    assert timing['response_within_limit'] is True and timing['within_limit'] is True
    assert c.get(source_url,headers=h['student1']).json()==original


def test_prefilled_sources_reject_active_and_foreign_cards(classroom):
    c=classroom['client'];h=classroom['headers'];sid=classroom['session']['id']
    body={'title':'Реагирование','mode':'actions','group_id':classroom['group']['id'],'source_session_ids':[sid]}
    assert c.post('/api/v1/instructor/lessons',headers=h['teacher1'],json=body).status_code==422
    c.post(f'/api/v1/student/sessions/{sid}/finish',headers=h['student1'])
    group=c.post('/api/v1/instructor/groups',headers=h['teacher2'],json={'title':'Другая группа'}).json()
    assert c.post('/api/v1/instructor/lessons',headers=h['teacher2'],json={**body,'group_id':group['id']}).status_code==404
    assert c.post('/api/v1/instructor/lessons',headers=h['teacher1'],json={**body,'mode':'mixed'}).status_code==422


def test_scenario_generated_prefill_and_concurrent_next(classroom):
    from concurrent.futures import ThreadPoolExecutor
    c=classroom['client'];h=classroom['headers'];scenario=classroom['store'].scenario(classroom['scenario_id'])
    body={'title':'Готовые системные карточки','mode':'actions','group_id':classroom['group']['id'],
          'prefilled_scenario_ids':[scenario['id']], 'cards_per_student':None}
    response=c.post('/api/v1/instructor/lessons',headers=h['teacher1'],json=body)
    assert response.status_code==201
    lid=response.json()['id'];c.post(f'/api/v1/instructor/lessons/{lid}/start',headers=h['teacher1'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:c.post(f'/api/v1/student/lessons/{lid}/next',headers=h['student1']).json(),range(4)))
    assert len({r['id'] for r in results})==1
    card=results[0]
    assert card['source_kind']=='scenario' and card['exercise_mode']=='actions'
    assert card['card']['description']==scenario['incident']
    assert card['card']['address_note']==scenario['location'] and card['card']['street']==''
    assert card['messages']==[] and not card['assessment_enabled']


def test_running_lesson_uses_start_snapshot_after_scenario_edit(classroom):
    from server import Scenario
    c=classroom['client'];h=classroom['headers'];store=classroom['store'];scenario=store.scenario(classroom['scenario_id'])
    body={'title':'Зафиксированные обстоятельства','group_id':classroom['group']['id'],'scenario_ids':[scenario['id']]}
    lid=c.post('/api/v1/instructor/lessons',headers=h['teacher1'],json=body).json()['id']
    c.post(f'/api/v1/instructor/lessons/{lid}/start',headers=h['teacher1'])
    store.put_scenario(Scenario.model_validate({**scenario,'location':'Новое место, не для текущего занятия','enabled':False}))
    card=c.post(f'/api/v1/student/lessons/{lid}/next',headers=h['student1']).json()
    assert store.load(card['id'])['scenario']['location']==scenario['location']


def test_unproductive_call_closes_the_card_as_checked(classroom):
    """Нет контакта и срыв звонка закрывают карточку без заполнения."""
    from uuid import uuid4
    c = classroom['client']; h = classroom['headers']['student1']
    card = c.post('/api/v1/student/sessions', headers=h, json={
        'scenario_id': classroom['scenario_id'],
        'assignment_id': classroom['assignment']['id']}).json()
    url = '/api/v1/student/sessions/' + card['id']
    message = {'message_id': str(uuid4()), 'kind': 'no_contact'}
    closed = c.post(url + '/unproductive', headers=h, json=message)
    assert closed.status_code == 200, closed.text
    value = closed.json()
    assert value['card']['no_contact'] is True
    assert value['status'] == 'Завершена' and value['checked_by']
    assert value['incident_status'] == 'Завершена'
    # Норматив реакции измерен: кнопка — действие оператора.
    assert value['evaluation']['timing']['response_seconds'] is not None
    # Повтор того же идентификатора не создаёт второго события.
    again = c.post(url + '/unproductive', headers=h, json=message)
    assert again.status_code == 200
    assert sum(e['type'] == 'card.unproductive' for e in again.json()['events']) == 1


def test_unproductive_refused_when_services_are_assigned(classroom):
    from uuid import uuid4
    c = classroom['client']; h = classroom['headers']['student1']
    session = classroom['session']; url = '/api/v1/student/sessions/' + session['id']
    c.put(url + '/card', headers=h, json={'revision': 0, 'card': {
        **session['card'], 'services': ['Служба 101']}})
    refused = c.post(url + '/unproductive', headers=h,
                     json={'message_id': str(uuid4()), 'kind': 'interrupted'})
    assert refused.status_code == 409


def test_reminder_is_stored_once_per_message_id(classroom):
    from uuid import uuid4
    c = classroom['client']; h = classroom['headers']['student1']
    url = '/api/v1/student/sessions/' + classroom['session']['id']
    body = {'message_id': str(uuid4()), 'text': 'Перезвонить заявителю',
            'at': '2026-09-18T10:00:00+00:00'}
    first = c.post(url + '/reminders', headers=h, json=body)
    assert first.status_code == 200, first.text
    assert first.json()['reminders'][0]['text'] == 'Перезвонить заявителю'
    assert len(c.post(url + '/reminders', headers=h, json=body).json()['reminders']) == 1


def test_operator_workstation_is_used_when_teacher_assigned_none(classroom):
    c = classroom['client']; h = classroom['headers']['student1']
    card = c.post('/api/v1/student/sessions', headers=h, json={
        'scenario_id': classroom['scenario_id'],
        'assignment_id': classroom['assignment']['id'],
        'workstation': 'АРМ-17'}).json()
    assert card['registration']['workstation'] == 'АРМ-17'
    assert card['registration']['workstation_source'] == 'operator'


def test_external_system_service_is_marked_as_vis(classroom):
    """Служба от внешней системы приходит в карточку с источником vis."""
    from uuid import uuid4
    c = classroom['client']; h = classroom['headers']
    session = classroom['session']; url = '/api/v1/student/sessions/' + session['id']
    c.put(url + '/card', headers=h['student1'], json={'revision': 0, 'card': {
        **session['card'], 'services': ['Служба 101']}})
    body = {'message_id': str(uuid4()), 'service': 'ЦОДД',
            'reason': 'Передано внешней системой по решению оператора службы'}
    added = c.post('/api/v1/instructor/sessions/' + session['id'] + '/vis-service',
                   headers=h['teacher1'], json=body)
    assert added.status_code == 200, added.text
    value = added.json()
    assert 'ЦОДД' in value['card']['services']
    assert value['service_states']['ЦОДД']['source'] == 'vis'
    assert value['service_states']['Служба 101'].get('source') is None
    assert any(e['type'] == 'service.vis_added' for e in value['events'])
    # Повтор идентификатора не добавляет службу дважды.
    again = c.post('/api/v1/instructor/sessions/' + session['id'] + '/vis-service',
                   headers=h['teacher1'], json=body)
    assert again.status_code == 200
    assert again.json()['card']['services'].count('ЦОДД') == 1
    # Уже назначенную службу внешняя система не дублирует.
    assert c.post('/api/v1/instructor/sessions/' + session['id'] + '/vis-service',
                  headers=h['teacher1'],
                  json={'message_id': str(uuid4()), 'service': 'ЦОДД',
                        'reason': 'Повтор'}).status_code == 422


def test_vis_service_requires_the_owning_teacher_and_a_saved_card(classroom):
    from uuid import uuid4
    c = classroom['client']; h = classroom['headers']
    session = classroom['session']
    body = {'message_id': str(uuid4()), 'service': 'ЦОДД', 'reason': 'Основание'}
    # Карточка ещё не сохранена обучающимся.
    assert c.post('/api/v1/instructor/sessions/' + session['id'] + '/vis-service',
                  headers=h['teacher1'], json=body).status_code == 409
    c.put('/api/v1/student/sessions/' + session['id'] + '/card', headers=h['student1'],
          json={'revision': 0, 'card': {**session['card'], 'services': ['Служба 101']}})
    # Чужой преподаватель и обучающийся не могут добавить службу от ВИС.
    assert c.post('/api/v1/instructor/sessions/' + session['id'] + '/vis-service',
                  headers=h['teacher2'], json=body).status_code == 404
    assert c.post('/api/v1/instructor/sessions/' + session['id'] + '/vis-service',
                  headers=h['student1'], json=body).status_code == 403
