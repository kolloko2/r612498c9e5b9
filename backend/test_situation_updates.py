"""Динамические вводные: обстановка меняется по ходу работы с карточкой."""

import json
from datetime import datetime, timedelta, timezone

from test_rbac_integration import classroom  # noqa: F401  (фикстура класса)

UPDATES = [
    {"id": "worse", "after_seconds": 30, "text": "Состояние пострадавшего ухудшилось",
     "source": "Служба 112"},
    {"id": "blocked", "after_seconds": 120, "text": "Бригада не может проехать: двор перекрыт",
     "source": "Служба 101"},
]


def arm(client, sid, headers):
    return client.post(f"/api/v1/student/sessions/{sid}/updates", headers=headers, json=None)


def plant(store, sid, updates, created_shift_seconds=0):
    """Положить вводные в снимок сценария и при нужде сдвинуть выдачу в прошлое."""
    state = store.load(str(sid))
    state["scenario"] = {**state.get("scenario", {}), "updates": updates}
    store.save(str(sid), state)
    if created_shift_seconds:
        row = store.db.execute("SELECT body FROM workspace WHERE id=?", (str(sid),)).fetchone()
        value = json.loads(row[0])
        moved = datetime.now(timezone.utc) - timedelta(seconds=created_shift_seconds)
        value["created_at"] = moved.isoformat()
        with store.db:
            store.db.execute(
                "INSERT INTO workspace VALUES (?,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body",
                (str(sid), json.dumps(value, ensure_ascii=False)))


def test_update_arrives_only_when_its_time_has_come(classroom):
    c = classroom
    client, h, sid = c["client"], c["headers"]["student1"], c["session"]["id"]
    plant(c["store"], sid, UPDATES)

    # Сразу после выдачи карточки срок не наступил ни у одной вводной.
    assert arm(client, sid, h).json().get("situation_updates", []) == []

    # Через полминуты приходит первая, вторая ещё ждёт.
    plant(c["store"], sid, UPDATES, created_shift_seconds=40)
    delivered = arm(client, sid, h).json()["situation_updates"]
    assert [item["id"] for item in delivered] == ["worse"]
    assert delivered[0]["source"] == "Служба 112"
    assert "at" in delivered[0]

    # Повторный запрос не дублирует уже доставленную вводную.
    assert [item["id"] for item in arm(client, sid, h).json()["situation_updates"]] == ["worse"]

    # Когда наступает срок второй, приходит и она.
    plant(c["store"], sid, UPDATES, created_shift_seconds=200)
    assert [item["id"] for item in arm(client, sid, h).json()["situation_updates"]] == ["worse", "blocked"]


def test_update_is_recorded_as_an_event_but_not_as_a_student_action(classroom):
    """Вводная — событие системы: она не закрывает норматив реакции."""
    c = classroom
    client, h, sid = c["client"], c["headers"]["student1"], c["session"]["id"]
    plant(c["store"], sid, UPDATES, created_shift_seconds=40)
    value = arm(client, sid, h).json()
    events = [event for event in value["events"] if event["type"] == "situation.update"]
    assert len(events) == 1 and events[0]["detail"]["id"] == "worse"
    # Реакция остаётся неизмеренной: человек ещё ничего не сделал.
    assert not value.get("first_action_at")


def test_finished_card_receives_no_updates(classroom):
    c = classroom
    client, h, sid = c["client"], c["headers"]["student1"], c["session"]["id"]
    client.post(f"/api/v1/student/sessions/{sid}/finish", headers=h)
    plant(c["store"], sid, UPDATES, created_shift_seconds=400)
    assert arm(client, sid, h).json().get("situation_updates", []) == []


def test_scenario_without_updates_is_unaffected(classroom):
    c = classroom
    client, h, sid = c["client"], c["headers"]["student1"], c["session"]["id"]
    plant(c["store"], sid, [], created_shift_seconds=400)
    value = arm(client, sid, h).json()
    assert value.get("situation_updates", []) == []
    assert not any(event["type"] == "situation.update" for event in value["events"])


def test_updates_belong_to_their_owner(classroom):
    c = classroom
    client, sid = c["client"], c["session"]["id"]
    plant(c["store"], sid, UPDATES, created_shift_seconds=400)
    assert arm(client, sid, c["headers"]["student2"]).status_code == 404


def test_dds_sip_report_unlocks_only_after_connected_call_is_confirmed(classroom, monkeypatch):
    async def fake_voice(*args):
        return {'status': 'ended'}
    monkeypatch.setattr('workspace.voice_request', fake_voice)
    c = classroom
    client, h, sid = c['client'], c['headers']['student1'], c['session']['id']
    reports = [{**UPDATES[0], 'unlocks_status': 'Начало реагирования'}]
    plant(c['store'], sid, reports, created_shift_seconds=40)
    row = c['store'].db.execute('SELECT body FROM workspace WHERE id=?', (sid,)).fetchone()
    value = json.loads(row[0])
    value.update(exercise_mode='actions', sip_extension='201', planned_unlocks={'worse': 'Начало реагирования'})
    with c['store'].db:
        c['store'].db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), sid))
    pending = arm(client, sid, h).json()
    assert pending.get('situation_updates', []) == []
    assert pending['pending_phone_reports'][0]['id'] == 'worse'
    assert client.post(f'/api/v1/student/sessions/{sid}/updates/worse/confirm', headers=h).status_code == 409
    report_sid = '9f572d2b-c4f4-42fc-8656-347b582ea3f8'
    value['field_report_calls'] = {'worse': {'session_id': report_sid, 'call_id': report_sid}}
    with c['store'].db:
        c['store'].db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), sid))
    c['store'].save(report_sid, {'messages': [], 'replies': {}, 'seq': 0, 'ended': False})
    assert client.post(f'/api/v1/student/sessions/{sid}/updates/worse/confirm', headers=h).status_code == 409
    c['store'].save(report_sid, {'messages': [{'role': 'assistant', 'content': reports[0]['text']}],
                                  'replies': {}, 'seq': 1, 'ended': False})
    assert client.post(f'/api/v1/student/sessions/{sid}/updates/worse/confirm', headers=h).status_code == 409
    c['store'].save(report_sid, {'messages': [{'role': 'assistant', 'content': reports[0]['text']}],
                               'replies': {'event': {'type': 'caller.reply', 'payload': {'reply_id': 'reply-1'}}},
                               'playback': {'reply-1': 'played'}, 'seq': 1, 'ended': False})
    confirmed = client.post(f'/api/v1/student/sessions/{sid}/updates/worse/confirm', headers=h)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['situation_updates'][0]['transport'] == 'sip'
    assert confirmed.json()['pending_phone_reports'] == []
    assert len(client.post(f'/api/v1/student/sessions/{sid}/updates/worse/confirm', headers=h).json()['situation_updates']) == 1


def test_scenario_model_bounds_updates():
    from server import Scenario
    import pytest

    base = {"id": "upd_case", "title": "Учебный сценарий", "victim_name": "Заявитель",
            "incident": "Учебное происшествие", "location": "Учебный адрес",
            "known_facts": ["Факт"], "emotion": "Спокоен", "opening": "Здравствуйте"}
    scenario = Scenario.model_validate({**base, "updates": UPDATES})
    assert [item.id for item in scenario.updates] == ["worse", "blocked"]
    # Не больше шести: иначе занятие превращается в поток уведомлений.
    too_many = [{**UPDATES[0], "id": f"u{i}"} for i in range(7)]
    with pytest.raises(Exception):
        Scenario.model_validate({**base, "updates": too_many})
    # Мгновенная вводная запрещена: она пришла бы вместе с карточкой.
    with pytest.raises(Exception):
        Scenario.model_validate({**base, "updates": [{**UPDATES[0], "after_seconds": 1}]})


def test_inbox_poll_updates_all_owned_cards_without_leaking_plan(classroom):
    c = classroom
    sid = c['session']['id']
    plant(c['store'], sid, UPDATES, created_shift_seconds=40)
    prepare_own_service(c['store'], sid)
    response = c['client'].post('/api/v1/student/inbox/poll', headers=c['headers']['student1'])
    assert response.status_code == 200, response.text
    card = next(item for item in response.json() if item['id'] == sid)
    assert card['situation_updates'][0]['id'] == 'worse'
    assert 'planned_unlocks' not in card and 'dds_expectation' not in card
    other = c['client'].post('/api/v1/student/inbox/poll', headers=c['headers']['student2'])
    assert all(item['id'] != sid for item in other.json())


def test_terminal_dds_card_is_read_only_before_session_finishes(classroom):
    c = classroom
    sid = c['session']['id']
    prepare_own_service(c['store'], sid)
    value = json.loads(c['store'].db.execute('SELECT body FROM workspace WHERE id=?', (sid,)).fetchone()[0])
    value['exercise_mode'] = 'actions'
    value['service_states']['Служба 101']['status'] = 'Работы завершены'
    with c['store'].db:
        c['store'].db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value), sid))
    path = f'/api/v1/student/sessions/{sid}'
    h = c['headers']['student1']
    assert c['client'].get(path, headers=h).json()['card_locked'] is True
    assert c['client'].put(path+'/card', headers=h, json={'revision': value['revision'], 'card': value['card']}).status_code == 409


OPERATIONAL = [
    {"id": "dispatched", "after_seconds": 40, "source": "Дежурный Службы 101",
     "text": "Наряд направлен по адресу", "unlocks_status": "Начало реагирования"},
    {"id": "arrived", "after_seconds": 100, "source": "Дежурный Службы 101",
     "text": "Наряд прибыл на адрес", "unlocks_status": "Прибытие"},
]


def test_103_initial_completion_records_receipt_despite_future_updates(classroom):
    c = classroom
    sid = c['session']['id']
    prepare_own_service(c['store'], sid, 'Служба 103')
    db = c['store'].db
    value = json.loads(db.execute('SELECT body FROM workspace WHERE id=?', (sid,)).fetchone()[0])
    value['exercise_mode'] = 'actions'
    value['service_states']['Служба 103']['status'] = 'Получена службой'
    value['crew_options'] = [{'id': '17'}]
    value['assigned_crew'] = None
    with db:
        db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(value, ensure_ascii=False), sid))
    h = c['headers']['student1']
    invalid = set_status(c['client'], sid, h, 'Служба 103', 'Работы завершены', 'Просто закончил')
    assert invalid.status_code in (409, 422)
    result = set_status(c['client'], sid, h, 'Служба 103', 'Работы завершены', 'Завершение работ без бригады.')
    assert result.status_code == 200, result.text
    persisted = json.loads(db.execute('SELECT body FROM workspace WHERE id=?', (sid,)).fetchone()[0])
    assert persisted['receipt_decided_at']
    assert persisted['service_states']['Служба 103']['status'] == 'Работы завершены'


def prepare_own_service(store, sid, service="Служба 101"):
    """Карточка с назначенной своей ДДС и планом разблокировок."""
    row = store.db.execute("SELECT body FROM workspace WHERE id=?", (str(sid),)).fetchone()
    value = json.loads(row[0])
    value["owner_service"] = service
    value["card"]["services"] = [service, "ЦОДД"]
    value["service_states"] = {
        service: {"status": "Принята", "comment": "", "at": "2026-09-18T00:00:00+00:00"},
        "ЦОДД": {"status": "Добавлена", "comment": "", "at": "2026-09-18T00:00:00+00:00"},
    }
    value["planned_unlocks"] = {item["id"]: item["unlocks_status"] for item in OPERATIONAL}
    value["revision"] = max(value.get("revision", 0), 1)
    with store.db:
        store.db.execute(
            "INSERT INTO workspace VALUES (?,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body",
            (str(sid), json.dumps(value, ensure_ascii=False)))


def set_status(client, sid, headers, service, status, comment="", order=""):
    from uuid import uuid4
    return client.post(f"/api/v1/student/sessions/{sid}/services", headers=headers, json={
        "message_id": str(uuid4()), "service": service, "status": status,
        "comment": comment, "order_number": order})


def test_dispatcher_leads_only_their_own_service(classroom):
    c = classroom
    client, h, sid = c["client"], c["headers"]["student1"], c["session"]["id"]
    prepare_own_service(c["store"], sid)
    refused = set_status(client, sid, h, "ЦОДД", "Начало реагирования")
    assert refused.status_code == 403
    assert "только свою службу" in refused.json()["detail"]
    # Чужая служба не предлагает ни одного статуса.
    value = client.get(f"/api/v1/student/sessions/{sid}", headers=h).json()
    assert value["allowed_service_statuses"]["ЦОДД"] == []
    assert value["owner_service"] == "Служба 101"


def test_progress_status_waits_for_the_field_report(classroom):
    """Нельзя прокликать цепочку: статус открывает сообщение с места."""
    c = classroom
    client, h, sid = c["client"], c["headers"]["student1"], c["session"]["id"]
    prepare_own_service(c["store"], sid)
    early = set_status(client, sid, h, "Служба 101", "Начало реагирования")
    assert early.status_code == 409 and "не подтверждён с места" in early.json()["detail"]
    assert set_status(client, sid, h, "Служба 101", "Выезд").status_code == 409
    assert set_status(client, sid, h, "Служба 101", "Завершение", comment="Все работы выполнены").status_code == 409
    # Пока вводная не пришла, статус не предлагается и в интерфейсе.
    value = client.get(f"/api/v1/student/sessions/{sid}", headers=h).json()
    assert "Начало реагирования" not in value["allowed_service_statuses"]["Служба 101"]

    plant(c["store"], sid, OPERATIONAL, created_shift_seconds=50)
    prepare_own_service(c["store"], sid)
    plant(c["store"], sid, OPERATIONAL, created_shift_seconds=50)
    arm(client, sid, h)
    assert set_status(client, sid, h, "Служба 101", "Начало реагирования").status_code == 200
    # Следующий статус всё ещё закрыт: о прибытии пока не сообщали.
    assert set_status(client, sid, h, "Служба 101", "Прибытие").status_code == 409


def test_closing_works_requires_the_result_in_the_comment(classroom):
    c = classroom
    client, h, sid = c["client"], c["headers"]["student1"], c["session"]["id"]
    prepare_own_service(c["store"], sid)
    done = [{"id": "done", "after_seconds": 10, "source": "Дежурный",
             "text": "Работы закончены", "unlocks_status": "Работы завершены"}]
    plant(c["store"], sid, done, created_shift_seconds=50)
    prepare_own_service(c["store"], sid)
    value = json.loads(c["store"].db.execute(
        "SELECT body FROM workspace WHERE id=?", (str(sid),)).fetchone()[0])
    value["planned_unlocks"] = {"done": "Работы завершены"}
    with c["store"].db:
        c["store"].db.execute(
            "INSERT INTO workspace VALUES (?,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body",
            (str(sid), json.dumps(value, ensure_ascii=False)))
    plant(c["store"], sid, done, created_shift_seconds=50)
    arm(client, sid, h)

    short = set_status(client, sid, h, "Служба 101", "Работы завершены", comment="ок")
    assert short.status_code == 422 and "результат" in short.json()["detail"]
    assert set_status(client, sid, h, "Служба 101", "Завершение", comment="ок").status_code == 422
    full = set_status(client, sid, h, "Служба 101", "Работы завершены",
                      comment="Возгорание ликвидировано, пострадавших нет, объект передан собственнику")
    assert full.status_code == 200


def test_scenario_without_unlocks_keeps_the_old_behaviour(classroom):
    """Сценарии без оперативных вводных работают как раньше."""
    c = classroom
    client, h, sid = c["client"], c["headers"]["student1"], c["session"]["id"]
    prepare_own_service(c["store"], sid)
    value = json.loads(c["store"].db.execute(
        "SELECT body FROM workspace WHERE id=?", (str(sid),)).fetchone()[0])
    value.pop("planned_unlocks", None)
    with c["store"].db:
        c["store"].db.execute(
            "INSERT INTO workspace VALUES (?,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body",
            (str(sid), json.dumps(value, ensure_ascii=False)))
    assert set_status(client, sid, h, "Служба 101", "Начало реагирования").status_code == 200
