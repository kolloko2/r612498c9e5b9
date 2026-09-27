"""Lifecycle coverage for teacher-controlled group lesson queues."""

import json

from server import Scenario
from test_rbac_integration import classroom


def create_lesson(classroom, **overrides):
    body = {
        "title": "Групповая практика",
        "group_id": classroom["group"]["id"],
        "scenario_ids": [classroom["scenario_id"]],
        "cards_per_student": 1,
    }
    body.update(overrides)
    return classroom["client"].post(
        "/api/v1/instructor/lessons",
        headers=classroom["headers"]["teacher1"],
        json=body,
    )


def student_lesson(classroom, lesson_id, username="student1"):
    response = classroom["client"].get(
        "/api/v1/student/lessons", headers=classroom["headers"][username]
    )
    assert response.status_code == 200
    return next(item for item in response.json() if item["id"] == lesson_id)


def put_scenario(classroom, scenario_id, category_id, *, enabled=True):
    value = classroom["store"].scenario(classroom["scenario_id"])
    value.update(
        id=scenario_id,
        title=f"Сценарий {scenario_id}",
        category_id=category_id,
        enabled=enabled,
    )
    classroom["store"].put_scenario(Scenario.model_validate(value))


def test_lesson_categories_resolve_enabled_pool_and_reject_mismatch(classroom):
    put_scenario(classroom, "lesson_fire", "fire")
    put_scenario(classroom, "lesson_medical", "medical")
    put_scenario(classroom, "lesson_fire_disabled", "fire", enabled=False)

    automatic = create_lesson(
        classroom,
        scenario_ids=[],
        category_ids=["fire"],
    )
    assert automatic.status_code == 201
    assert automatic.json()["category_ids"] == ["fire"]
    assert automatic.json()["scenario_ids"] == ["lesson_fire"]

    mismatch = create_lesson(
        classroom,
        scenario_ids=["lesson_fire"],
        category_ids=["medical"],
    )
    assert mismatch.status_code == 422
    assert create_lesson(classroom, scenario_ids=[], category_ids=["police"]).status_code == 422


def test_finite_lesson_student_summary_and_membership_freeze(classroom):
    client = classroom["client"]
    headers = classroom["headers"]
    lesson = create_lesson(classroom, cards_per_student=1)
    assert lesson.status_code == 201
    lesson_id = lesson.json()["id"]
    teacher_url = f"/api/v1/instructor/lessons/{lesson_id}"
    next_url = f"/api/v1/student/lessons/{lesson_id}/next"

    planned = student_lesson(classroom, lesson_id)
    assert planned["state"] == "planned"
    assert planned["cards_per_student"] == 1
    assert planned["completed"] == 0
    assert planned["active_session_id"] is None
    assert planned["exhausted"] is False
    assert not ({"scenario_ids", "templates", "members", "cards"} & planned.keys())

    started = client.post(teacher_url + "/start", headers=headers["teacher1"])
    assert started.status_code == 200
    assert started.json()["state"] == "running"
    added_late = client.post(
        f"/api/v1/instructor/groups/{classroom['group']['id']}/members",
        headers=headers["teacher1"],
        json={"student_id": classroom["users"]["student2"]["id"]},
    )
    assert added_late.status_code == 200
    assert client.get("/api/v1/student/lessons", headers=headers["student2"]).json() == []
    assert client.post(next_url, headers=headers["student2"]).status_code == 404

    card = client.post(next_url, headers=headers["student1"])
    assert card.status_code == 200
    assert student_lesson(classroom, lesson_id)["active_session_id"] == card.json()["id"]
    finished = client.post(
        f"/api/v1/student/sessions/{card.json()['id']}/finish",
        headers=headers["student1"],
    )
    assert finished.status_code == 200
    summary = student_lesson(classroom, lesson_id)
    assert summary["completed"] == 1
    assert summary["active_session_id"] is None
    assert summary["exhausted"] is True
    assert client.post(next_url, headers=headers["student1"]).status_code == 409


def test_null_card_limit_allows_teacher_controlled_unlimited_queue(classroom):
    client = classroom["client"]
    headers = classroom["headers"]
    lesson = create_lesson(classroom, cards_per_student=None)
    assert lesson.status_code == 201
    lesson_id = lesson.json()["id"]
    assert client.post(
        f"/api/v1/instructor/lessons/{lesson_id}/start", headers=headers["teacher1"]
    ).status_code == 200

    issued = []
    for _ in range(3):
        response = client.post(
            f"/api/v1/student/lessons/{lesson_id}/next", headers=headers["student1"]
        )
        assert response.status_code == 200
        issued.append(response.json()["id"])
        assert client.post(
            f"/api/v1/student/sessions/{issued[-1]}/finish", headers=headers["student1"]
        ).status_code == 200

    assert len(set(issued)) == 3
    summary = student_lesson(classroom, lesson_id)
    assert summary["cards_per_student"] is None
    assert summary["completed"] == 3
    assert summary["active_session_id"] is None
    assert summary["exhausted"] is False
    assert client.post(
        f"/api/v1/student/lessons/{lesson_id}/next", headers=headers["student1"]
    ).status_code == 200


def test_next_transition_is_idempotent_after_successor_completion(classroom):
    client = classroom["client"]
    headers = classroom["headers"]["student1"]
    lesson = create_lesson(classroom, cards_per_student=3).json()
    lesson_id = lesson["id"]
    classroom["client"].post(
        f"/api/v1/instructor/lessons/{lesson_id}/start",
        headers=classroom["headers"]["teacher1"],
    )
    next_url = f"/api/v1/student/lessons/{lesson_id}/next"
    first = client.post(next_url, headers=headers).json()
    client.post(f"/api/v1/student/sessions/{first['id']}/finish", headers=headers)

    transition = {"after_session_id": first["id"]}
    successor = client.post(next_url, headers=headers, json=transition)
    assert successor.status_code == 200
    assert successor.json()["previous_session_id"] == first["id"]
    client.post(
        f"/api/v1/student/sessions/{successor.json()['id']}/finish", headers=headers
    )

    repeated = client.post(next_url, headers=headers, json=transition)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == successor.json()["id"]
    assert student_lesson(classroom, lesson_id)["completed"] == 2


def test_teacher_report_and_stop_are_owned_complete_and_idempotent(classroom):
    client = classroom["client"]
    headers = classroom["headers"]
    lesson = create_lesson(classroom, cards_per_student=None).json()
    lesson_id = lesson["id"]
    teacher_url = f"/api/v1/instructor/lessons/{lesson_id}"
    next_url = f"/api/v1/student/lessons/{lesson_id}/next"
    client.post(teacher_url + "/start", headers=headers["teacher1"])

    completed = client.post(next_url, headers=headers["student1"]).json()
    client.post(
        f"/api/v1/student/sessions/{completed['id']}/finish", headers=headers["student1"]
    )
    active = client.post(next_url, headers=headers["student1"]).json()

    assert client.get(teacher_url + "/report", headers=headers["teacher2"]).status_code == 404
    assert client.get(teacher_url + "/report", headers=headers["student1"]).status_code == 403
    report_response = client.get(teacher_url + "/report", headers=headers["teacher1"])
    assert report_response.status_code == 200
    report = report_response.json()
    assert report["summary"] == {"participants": 1, "issued": 2, "completed": 1}
    assert len(report["participants"]) == 1
    participant = report["participants"][0]
    assert participant["student_id"] == classroom["users"]["student1"]["id"]
    assert {card["id"] for card in participant["cards"]} == {completed["id"], active["id"]}
    assert all("scenario" not in card and "messages" not in card for card in participant["cards"])

    stopped = client.post(
        teacher_url + "/finish",
        headers=headers["teacher1"],
        json={"reason": "Время занятия завершено"},
    )
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "finished"
    closed = client.get(
        f"/api/v1/student/sessions/{active['id']}", headers=headers["student1"]
    ).json()
    assert closed["status"] == "Завершена"
    assert closed["completed_by"]["reason"] == "Время занятия завершено"

    before = classroom["store"].db.execute(
        "SELECT body FROM lessons WHERE id=?", (lesson_id,)
    ).fetchone()[0]
    duplicate = client.post(
        teacher_url + "/finish",
        headers=headers["teacher1"],
        json={"reason": "Повтор не должен менять отчёт"},
    )
    after = classroom["store"].db.execute(
        "SELECT body FROM lessons WHERE id=?", (lesson_id,)
    ).fetchone()[0]
    assert duplicate.status_code == 200
    assert duplicate.json() == stopped.json()
    assert json.loads(after) == json.loads(before)


def test_parallel_cards_allow_several_open_incidents(classroom):
    """Диспетчер ведёт несколько происшествий сразу; норматив идёт по каждому."""
    client, h = classroom["client"], classroom["headers"]
    lesson = create_lesson(classroom, cards_per_student=4, parallel_cards=2).json()
    assert lesson["parallel_cards"] == 2
    assert client.post("/api/v1/instructor/lessons/" + lesson["id"] + "/start",
                       headers=h["teacher1"]).status_code == 200

    url = "/api/v1/student/lessons/" + lesson["id"] + "/next"
    first = client.post(url, headers=h["student1"]).json()
    second = client.post(url, headers=h["student1"]).json()
    assert first["id"] != second["id"], "вторая карточка должна выдаваться параллельно"

    # Лимит одновременных достигнут: выдаётся самая ранняя открытая.
    third = client.post(url, headers=h["student1"]).json()
    assert third["id"] == first["id"]

    lessons = client.get("/api/v1/student/lessons", headers=h["student1"]).json()
    row = next(item for item in lessons if item["id"] == lesson["id"])
    assert sorted(row["active_session_ids"]) == sorted([first["id"], second["id"]])

    # После закрытия одной карточки освобождается место под новую.
    assert client.post("/api/v1/student/sessions/" + first["id"] + "/finish",
                       headers=h["student1"]).status_code == 200
    fourth = client.post(url, headers=h["student1"]).json()
    assert fourth["id"] not in (first["id"], second["id"])


def test_single_card_lesson_keeps_one_open_card(classroom):
    client, h = classroom["client"], classroom["headers"]
    lesson = create_lesson(classroom, cards_per_student=3).json()
    assert lesson["parallel_cards"] == 1
    client.post("/api/v1/instructor/lessons/" + lesson["id"] + "/start", headers=h["teacher1"])
    url = "/api/v1/student/lessons/" + lesson["id"] + "/next"
    first = client.post(url, headers=h["student1"]).json()
    assert client.post(url, headers=h["student1"]).json()["id"] == first["id"]


def test_targeted_assignment_and_workstation_numbers(classroom):
    """Преподаватель назначает конкретному месту конкретное задание."""
    client, h = classroom["client"], classroom["headers"]
    student = classroom["users"]["student1"]["id"]
    lesson = create_lesson(classroom, cards_per_student=2,
                           workstations={student: "АРМ-05"},
                           student_scenarios={student: classroom["scenario_id"]}).json()
    assert lesson["workstations"][student] == "АРМ-05"
    client.post("/api/v1/instructor/lessons/" + lesson["id"] + "/start", headers=h["teacher1"])

    card = client.post("/api/v1/student/lessons/" + lesson["id"] + "/next", headers=h["student1"]).json()
    assert card["scenario_id"] == classroom["scenario_id"]
    assert card["registration"]["workstation"] == "АРМ-05"

    client.post("/api/v1/student/sessions/" + card["id"] + "/finish", headers=h["student1"])
    report = client.get("/api/v1/instructor/lessons/" + lesson["id"] + "/report", headers=h["teacher1"]).json()
    row = next(p for p in report["participants"] if p["student_id"] == student)
    assert row["workstation"] == "АРМ-05"
    assert row["cards"][0]["workstation"] == "АРМ-05"
    assert "response_seconds" in row["cards"][0]


def test_targeted_assignment_must_belong_to_the_lesson(classroom):
    student = classroom["users"]["student1"]["id"]
    response = create_lesson(classroom, student_scenarios={student: "scenario-not-in-lesson"})
    assert response.status_code == 422


def test_adaptive_difficulty_reports_its_decision(classroom):
    """Адаптация объясняет решение и не молчит, когда менять уровень нечем."""
    client, h = classroom["client"], classroom["headers"]
    lesson = create_lesson(classroom, cards_per_student=4, adaptive_difficulty=True).json()
    assert lesson["adaptive_difficulty"] is True
    client.post("/api/v1/instructor/lessons/" + lesson["id"] + "/start", headers=h["teacher1"])
    url = "/api/v1/student/lessons/" + lesson["id"] + "/next"

    first = client.post(url, headers=h["student1"]).json()
    # На первой карточке история пуста, но решение всё равно объявлено.
    assert first["difficulty_advice"]["considered"] == 0
    assert first["difficulty_advice"]["changed"] is False
    client.post("/api/v1/student/sessions/" + first["id"] + "/finish", headers=h["student1"])

    second = client.post(url, headers=h["student1"], json={"after_session_id": first["id"]}).json()
    advice = second["difficulty_advice"]
    assert advice["considered"] == 1 and advice["changed"] is False
    assert "Недостаточно" in advice["reason"]
    client.post("/api/v1/student/sessions/" + second["id"] + "/finish", headers=h["student1"])

    third = client.post(url, headers=h["student1"], json={"after_session_id": second["id"]}).json()
    assert third["difficulty_advice"]["considered"] == 2
    assert third["difficulty_advice"]["outcomes"]


def test_adaptive_is_off_by_default(classroom):
    lesson = create_lesson(classroom).json()
    assert lesson["adaptive_difficulty"] is False


def test_guided_step_is_broadcast_to_the_group(classroom):
    """«Делай как я»: преподаватель ведёт группу по шагам вводного курса."""
    client, h = classroom["client"], classroom["headers"]
    lesson = create_lesson(classroom).json()
    url = "/api/v1/instructor/lessons/" + lesson["id"] + "/guided-step"
    client.post("/api/v1/instructor/lessons/" + lesson["id"] + "/start", headers=h["teacher1"])

    def student_view():
        rows = client.get("/api/v1/student/lessons", headers=h["student1"]).json()
        return next(item for item in rows if item["id"] == lesson["id"])

    assert student_view()["guided_step"] is None

    assert client.put(url, headers=h["teacher1"], json={"step": 4}).status_code == 200
    assert student_view()["guided_step"] == 4

    # Снятие показа возвращает обучающимся самостоятельность.
    assert client.put(url, headers=h["teacher1"], json={"step": None}).status_code == 200
    assert student_view()["guided_step"] is None

    # Шаг вне диапазона и чужая роль отклоняются.
    assert client.put(url, headers=h["teacher1"], json={"step": 0}).status_code == 422
    assert client.put(url, headers=h["teacher1"], json={"step": 99}).status_code == 422
    assert client.put(url, headers=h["student1"], json={"step": 2}).status_code == 403
    assert client.put(url, headers=h["teacher2"], json={"step": 2}).status_code == 404

    # Показ фиксируется в журнале занятия.
    client.put(url, headers=h["teacher1"], json={"step": 2})
    report = client.get("/api/v1/instructor/lessons/" + lesson["id"] + "/report", headers=h["teacher1"]).json()
    assert any(event["type"] == "lesson.guided_step" for event in report["events"])


def test_mode_switch_requires_permission_and_material(classroom):
    """Полный цикл 112 или работа ДДС: режим выбирает преподаватель."""
    client, h = classroom["client"], classroom["headers"]

    # Переключение вне смешанного занятия невозможно: материала для второго режима нет.
    refused = create_lesson(classroom, allow_mode_switch=True)
    assert refused.status_code == 422

    lesson = create_lesson(classroom, cards_per_student=4, allow_mode_switch=True, mode="mixed",
                           prefilled_scenario_ids=[classroom["scenario_id"]]).json()
    assert lesson["allow_mode_switch"] is True
    client.post("/api/v1/instructor/lessons/" + lesson["id"] + "/start", headers=h["teacher1"])
    url = "/api/v1/student/lessons/" + lesson["id"] + "/next"

    # Обучающийся видит, что переключение разрешено.
    rows = client.get("/api/v1/student/lessons", headers=h["student1"]).json()
    assert next(item for item in rows if item["id"] == lesson["id"])["allow_mode_switch"] is True

    ready = client.post(url, headers=h["student1"], json={"mode": "actions"}).json()
    assert ready["exercise_mode"] == "actions"
    client.post("/api/v1/student/sessions/" + ready["id"] + "/finish", headers=h["student1"])

    full = client.post(url, headers=h["student1"],
                       json={"mode": "fill", "after_session_id": ready["id"]}).json()
    assert full.get("exercise_mode", "fill") == "fill"


def test_mode_switch_is_refused_when_not_allowed(classroom):
    client, h = classroom["client"], classroom["headers"]
    lesson = create_lesson(classroom, cards_per_student=2).json()
    assert lesson["allow_mode_switch"] is False
    client.post("/api/v1/instructor/lessons/" + lesson["id"] + "/start", headers=h["teacher1"])
    response = client.post("/api/v1/student/lessons/" + lesson["id"] + "/next",
                           headers=h["student1"], json={"mode": "actions"})
    assert response.status_code == 409
    assert "не разрешил" in response.json()["detail"]


def test_restart_gives_the_group_a_fresh_lesson_and_keeps_old_results(classroom):
    c, h = classroom["client"], classroom["headers"]
    lid = create_lesson(classroom).json()["id"]
    c.post(f"/api/v1/instructor/lessons/{lid}/start", headers=h["teacher1"])
    card = c.post(f"/api/v1/student/lessons/{lid}/next", headers=h["student1"], json={})
    assert card.status_code == 200, card.text
    restarted = c.post(f"/api/v1/instructor/lessons/{lid}/restart", headers=h["teacher1"])
    assert restarted.status_code == 201, restarted.text
    fresh = restarted.json()
    assert fresh["id"] != lid and fresh["state"] == "running" and fresh["restarted_from"] == lid
    lessons = {item["id"]: item for item in c.get("/api/v1/instructor/lessons", headers=h["teacher1"]).json()}
    assert lessons[lid]["state"] == "finished" and lessons[lid]["cards"][0]["status"] == "Завершена"
    assert lessons[fresh["id"]]["cards"] == []
    assert student_lesson(classroom, fresh["id"])["state"] == "running"
