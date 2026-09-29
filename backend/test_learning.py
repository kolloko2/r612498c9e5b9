import json
import os

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("DIALOGUE_DB", ":memory:")

from learning import Learning
from server import Store


class AccountsStub:
    def __init__(self):
        self.users = {
            "t1": {"id": "t1", "username": "teacher1", "display_name": "Учитель 1", "role": "teacher", "active": True},
            "t2": {"id": "t2", "username": "teacher2", "display_name": "Учитель 2", "role": "teacher", "active": True},
            "s1": {"id": "s1", "username": "student1", "display_name": "Ученик 1", "role": "student", "active": True},
            "s2": {"id": "s2", "username": "student2", "display_name": "Ученик 2", "role": "student", "active": True},
            "off": {"id": "off", "username": "inactive", "display_name": "Неактивный", "role": "student", "active": False},
        }

    def current(self, token):
        return self.users.get(token)

    def get_user(self, user_id):
        return self.users.get(user_id)

    def list_users(self):
        return list(self.users.values())


@pytest.fixture
def classroom():
    store = Store(":memory:")
    store.db.execute("CREATE TABLE workspace (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
    accounts = AccountsStub()
    learning = Learning(store, accounts)
    app = FastAPI()
    app.include_router(learning.router(lambda: None))
    with TestClient(app) as client:
        yield client, store, learning
    store.db.close()


def headers(user_id):
    return {"X-User-Session": user_id}


def provision(client, teacher="t1", student="s1"):
    group = client.post("/api/v1/instructor/groups", headers=headers(teacher), json={"title": "Группа"}).json()
    assert client.post(
        f"/api/v1/instructor/groups/{group['id']}/members",
        headers=headers(teacher), json={"student_id": student},
    ).status_code == 200
    scenario_id = client.app.state.store.first_enabled()["id"] if hasattr(client.app.state, "store") else None
    return group, scenario_id


def test_teacher_groups_assignments_and_student_visibility(classroom):
    client, store, learning = classroom
    scenario_id = store.first_enabled()["id"]
    group = client.post("/api/v1/instructor/groups", headers=headers("t1"), json={"title": "Первая"}).json()
    assert client.post(f"/api/v1/instructor/groups/{group['id']}/members", headers=headers("t1"), json={"student_id": "s1"}).status_code == 200
    assignment = client.post("/api/v1/instructor/assignments", headers=headers("t1"), json={
        "group_id": group["id"], "scenario_id": scenario_id, "title": "Практика"
    })
    assert assignment.status_code == 201
    assignment = assignment.json()
    assert learning.assignment_for_student(assignment["id"], scenario_id, learning.accounts.users["s1"])["teacher_id"] == "t1"
    assert client.get("/api/v1/student/assignments", headers=headers("s1")).json()[0]["id"] == assignment["id"]
    assert client.get("/api/v1/student/assignments", headers=headers("s2")).json() == []
    assert learning.visible_scenarios(learning.accounts.users["s1"]) == [{"id": scenario_id, "title": store.scenario(scenario_id)["title"], "difficulty": "basic", "dds_profile": "general", "learning_objectives": ""}]
    with pytest.raises(HTTPException) as denied:
        learning.assignment_for_student(assignment["id"], scenario_id, learning.accounts.users["s2"])
    assert denied.value.status_code == 403


def test_two_teacher_isolation_and_wrong_roles(classroom):
    client, store, _ = classroom
    scenario_id = store.first_enabled()["id"]
    first = client.post("/api/v1/instructor/groups", headers=headers("t1"), json={"title": "Чужая"}).json()
    assert client.get("/api/v1/instructor/groups", headers=headers("t2")).json() == []
    assert client.post(f"/api/v1/instructor/groups/{first['id']}/members", headers=headers("t2"), json={"student_id": "s2"}).status_code == 404
    assert client.post("/api/v1/instructor/assignments", headers=headers("t2"), json={
        "group_id": first["id"], "scenario_id": scenario_id, "title": "Нельзя"
    }).status_code == 404
    second = client.post("/api/v1/instructor/groups", headers=headers("t2"), json={"title": "Своя"}).json()
    store.db.execute("INSERT INTO scenario_owners VALUES (?,?)", (scenario_id, "t1"))
    store.db.commit()
    assert client.post("/api/v1/instructor/assignments", headers=headers("t2"), json={
        "group_id": second["id"], "scenario_id": scenario_id, "title": "Чужой сценарий"
    }).status_code == 404
    assert client.get("/api/v1/instructor/groups", headers=headers("s1")).status_code == 403
    assert client.get("/api/v1/student/assignments", headers=headers("t1")).status_code == 403
    students = client.get("/api/v1/instructor/students", headers=headers("t1")).json()
    assert {item["id"] for item in students} == {"s1", "s2"}
    assert all(set(item) == {"id", "display_name", "username"} for item in students)


def test_session_list_and_detail_do_not_leak_across_teachers(classroom):
    client, store, learning = classroom
    values = [
        {"id": "owned", "number": 1, "teacher_id": "t1", "student_id": "s1", "assignment_id": "a1",
         "scenario_id": store.first_enabled()["id"], "title": "Сценарий", "status": "Завершена",
         "created_at": "2026-01-01T00:00:00+00:00", "evaluation": {"score_percent": 80}},
        {"id": "other", "number": 2, "teacher_id": "t2", "student_id": "s2", "assignment_id": "a2",
         "scenario_id": store.first_enabled()["id"], "title": "Сценарий", "status": "Новая",
         "created_at": "2026-01-02T00:00:00+00:00"},
        {"id": "legacy", "number": 3, "scenario_id": store.first_enabled()["id"], "title": "Старое",
         "status": "Завершена", "created_at": "2026-01-03T00:00:00+00:00"},
    ]
    for value in values:
        store.db.execute("INSERT INTO workspace VALUES (?,?)", (value["id"], json.dumps(value)))
    # The database limit must be applied after ownership filtering. A busy second
    # teacher must not push this teacher's session out of the result window.
    for number in range(110):
        foreign = {**values[1], "id": f"foreign-{number}", "number": 100 + number}
        store.db.execute("INSERT INTO workspace VALUES (?,?)", (foreign["id"], json.dumps(foreign)))
    store.db.commit()
    store.save("owned", {"messages": [{"role": "assistant", "content": "Учебная реплика"}],
                         "scenario": {"known_facts": ["скрыто"]}, "evaluation_rubric": {"rubric": {"expected": "скрыто"}}})
    listed = client.get("/api/v1/instructor/sessions", headers=headers("t1")).json()
    assert [item["id"] for item in listed] == ["owned"]
    assert set(listed[0]) == {"id", "number", "student_id", "student_name", "assignment_id", "status", "created_at", "score_percent", "scenario_title", "difficulty", "dds_profile", "learning_objectives", "communication", "attempt_number", "restarted_from", "restarted_to", "attempt_outcome"}
    detail = client.get("/api/v1/instructor/sessions/owned", headers=headers("t1"))
    assert detail.status_code == 200 and detail.json()["messages"][0]["content"] == "Учебная реплика"
    assert "scenario" not in detail.json() and "evaluation_rubric" not in detail.json()
    assert client.get("/api/v1/instructor/sessions/owned", headers=headers("t2")).status_code == 403
    assert client.get("/api/v1/instructor/sessions/legacy", headers=headers("t1")).status_code == 403
    assert learning.owns_session(learning.accounts.users["s1"], values[0])
    assert not learning.owns_session(learning.accounts.users["s2"], values[0])


def test_assignment_deactivation_revokes_new_student_access(classroom):
    client, store, learning = classroom
    scenario_id = store.first_enabled()["id"]
    group = client.post("/api/v1/instructor/groups", headers=headers("t1"), json={"title": "Группа"}).json()
    client.post(f"/api/v1/instructor/groups/{group['id']}/members", headers=headers("t1"), json={"student_id": "s1"})
    assignment = client.post("/api/v1/instructor/assignments", headers=headers("t1"), json={
        "group_id": group["id"], "scenario_id": scenario_id, "title": "Задание"
    }).json()
    assert client.patch(f"/api/v1/instructor/assignments/{assignment['id']}", headers=headers("t2"), json={"active": False}).status_code == 404
    assert client.patch(f"/api/v1/instructor/assignments/{assignment['id']}", headers=headers("t1"), json={"active": False}).json()["active"] is False
    assert client.get("/api/v1/student/assignments", headers=headers("s1")).json() == []
    with pytest.raises(HTTPException) as denied:
        learning.assignment_for_student(assignment["id"], scenario_id, learning.accounts.users["s1"])
    assert denied.value.status_code == 403


def test_teacher_removes_member_and_archives_group(classroom):
    client, store, learning = classroom
    scenario_id = store.first_enabled()["id"]
    group = client.post("/api/v1/instructor/groups", headers=headers("t1"), json={"title": "Группа"}).json()
    assert group["archived"] is False
    client.post(f"/api/v1/instructor/groups/{group['id']}/members", headers=headers("t1"), json={"student_id": "s1"})
    client.post("/api/v1/instructor/assignments", headers=headers("t1"), json={
        "group_id": group["id"], "scenario_id": scenario_id, "title": "Задание"
    })
    assert client.get("/api/v1/student/assignments", headers=headers("s1")).json()
    member = f"/api/v1/instructor/groups/{group['id']}/members/s1"
    assert client.delete(member, headers=headers("t2")).status_code == 404
    assert client.delete(member, headers=headers("s1")).status_code == 403
    assert client.delete(member, headers=headers("t1")).json()["member_ids"] == []
    assert client.get("/api/v1/student/assignments", headers=headers("s1")).json() == []

    path = f"/api/v1/instructor/groups/{group['id']}"
    assert client.patch(path, headers=headers("t2"), json={"archived": True}).status_code == 404
    archived = client.patch(path, headers=headers("t1"), json={"archived": True}).json()
    assert archived["archived"] is True and archived["archived_at"]
    assert client.get("/api/v1/instructor/groups", headers=headers("t1")).json()[0]["archived"] is True
    assert client.patch(path, headers=headers("t1"), json={"archived": False}).json()["archived"] is False


def test_assignment_reports_whether_hints_can_be_enabled(classroom):
    client, store, _ = classroom
    scenario_id = store.first_enabled()["id"]
    group = client.post("/api/v1/instructor/groups", headers=headers("t1"), json={"title": "Группа"}).json()
    assignment = client.post("/api/v1/instructor/assignments", headers=headers("t1"), json={
        "group_id": group["id"], "scenario_id": scenario_id, "title": "Задание"
    }).json()
    assert assignment["practice_available"] is False
    refused = client.patch(f"/api/v1/instructor/assignments/{assignment['id']}", headers=headers("t1"),
                           json={"active": True, "practice_with_hints": True})
    assert refused.status_code == 409
