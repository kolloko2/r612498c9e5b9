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
