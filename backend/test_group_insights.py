import json

import pytest

import group_insights
from accounts import Accounts
from learning import Learning
from test_rbac_integration import classroom


@pytest.fixture
def insights(classroom):
    c = classroom
    accounts = Accounts(c["store"])
    c["client"].app.include_router(
        group_insights.router(c["store"], accounts, Learning(c["store"], accounts), lambda: None)
    )
    return c


def _complete(c, caller_name="Учебный"):
    created = c["client"].post("/api/v1/student/sessions", headers=c["headers"]["student1"], json={
        "scenario_id": c["scenario_id"], "assignment_id": c["assignment"]["id"]
    }).json()
    saved = c["client"].put("/api/v1/student/sessions/" + created["id"] + "/card",
                            headers=c["headers"]["student1"],
                            json={"revision": 0, "card": {**created["card"], "caller_name": caller_name}})
    assert saved.status_code == 200, saved.text
    finished = c["client"].post("/api/v1/student/sessions/" + created["id"] + "/finish",
                                headers=c["headers"]["student1"])
    assert finished.status_code == 200, finished.text
    return finished.json()


def _url(c):
    return "/api/v1/instructor/groups/" + c["group"]["id"] + "/insights"


def test_insufficient_owner_boundary_mock_cache_and_stale(insights, monkeypatch):
    c = insights
    url = _url(c)
    assert c["client"].get(url, headers=c["headers"]["teacher1"]).json()["status"] == "not_generated"
    assert c["client"].post(url, headers=c["headers"]["teacher1"]).status_code == 409
    assert c["client"].get(url, headers=c["headers"]["teacher2"]).status_code == 404

    _complete(c)
    _complete(c)
    calls = 0

    async def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("mock mode must not call a model")

    monkeypatch.setattr(group_insights.llm, "configuration", lambda: {
        "provider": "mock", "model": "scenario-mock", "configured": True
    })
    monkeypatch.setattr(group_insights.llm, "complete", forbidden)
    result = c["client"].post(url, headers=c["headers"]["teacher1"])
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "mock" and "модель не анализировала" in body["summary"]
    assert body["recommendations"] and calls == 0
    assert body["evidence"] and body["aggregate"]["participant_count"] == 1
    assert any("Малая выборка" in item for item in body["limitations"])
    assert c["client"].post(url, headers=c["headers"]["teacher1"]).json() == body
    assert calls == 0

    added = c["client"].post("/api/v1/instructor/groups/" + c["group"]["id"] + "/members",
                             headers=c["headers"]["teacher1"],
                             json={"student_id": c["users"]["student2"]["id"]})
    assert added.status_code == 200
    stale = c["client"].get(url, headers=c["headers"]["teacher1"]).json()
    assert stale["stale"] is True and stale["stale_reasons"]


def test_strict_references_privacy_failure_retry_and_no_grade_mutation(insights, monkeypatch):
    c = insights
    first, second = _complete(c, "Первый"), _complete(c, "Второй")
    original = json.dumps([first.get("evaluation"), second.get("evaluation")], sort_keys=True)
    captured = []
    monkeypatch.setattr(group_insights.llm, "configuration", lambda: {
        "provider": "ollama", "model": "test-local", "configured": True
    })

    async def invalid(messages, *, max_tokens, json_mode=False):
        captured.append(messages)
        return json.dumps({"summary": "x", "difficult_skills": [], "recommendations": [{
            "title": "x", "rationale": "x", "scenario_ids": ["invented"], "evidence_keys": ["invented"]
        }]})

    monkeypatch.setattr(group_insights.llm, "complete", invalid)
    url = _url(c)
    failed = c["client"].post(url, headers=c["headers"]["teacher1"])
    assert failed.status_code == 502 and "invented" not in failed.text
    assert c["client"].get(url, headers=c["headers"]["teacher1"]).json()["status"] == "not_generated"
    prompt = captured[0][1]["content"]
    assert "Первый" not in prompt and "Второй" not in prompt
    assert c["users"]["student1"]["display_name"] not in prompt

    payload = json.loads(prompt)
    evidence_key = payload["evidence"][0]["key"]
    scenario_id = payload["available_scenarios"][0]["id"]

    async def valid(messages, *, max_tokens, json_mode=False):
        return json.dumps({"summary": "Нужна дополнительная практика.", "difficult_skills": [{
            "label": "Сбор данных", "explanation": "Есть повторяющиеся ошибки.", "evidence_key": evidence_key
        }], "recommendations": [{
            "title": "Повторить сценарий", "rationale": "Связано с агрегированной ошибкой.",
            "scenario_ids": [scenario_id], "evidence_keys": [evidence_key]
        }]}, ensure_ascii=False)

    monkeypatch.setattr(group_insights.llm, "complete", valid)
    ready = c["client"].post(url, headers=c["headers"]["teacher1"])
    assert ready.status_code == 200 and ready.json()["status"] == "ready"
    assert ready.json()["recommendations"][0]["scenario_ids"] == [scenario_id]
    after = [c["client"].get("/api/v1/student/sessions/" + sid, headers=c["headers"]["student1"]).json()
             for sid in (first["id"], second["id"])]
    assert json.dumps([item.get("evaluation") for item in after], sort_keys=True) == original


def test_schema_rejects_duplicate_and_unknown_references():
    payload = {"evidence": [{"key": "error_1"}], "available_scenarios": [{"id": "existing"}]}
    with pytest.raises(ValueError):
        group_insights._validated({"summary": "x", "difficult_skills": [], "recommendations": [{
            "title": "x", "rationale": "x", "scenario_ids": ["existing", "existing"],
            "evidence_keys": ["error_1"]
        }]}, payload)
    with pytest.raises(ValueError):
        group_insights._validated({"summary": "x", "difficult_skills": [{
            "label": "x", "explanation": "x", "evidence_key": "unknown"
        }], "recommendations": [{"title": "x", "rationale": "x", "scenario_ids": ["existing"],
                                  "evidence_keys": ["error_1"]}]}, payload)


def test_aggregate_separates_revisions_and_uses_eligible_denominator(insights):
    c = insights
    base = {"status": "Завершена", "teacher_id": c["users"]["teacher1"]["id"],
            "group_id": c["group"]["id"], "scenario_id": c["scenario_id"]}
    samples = [
        {**base, "id": "aggregate-rev1-fail", "evaluation": {"rubric_revision": 1, "criteria": [
            {"id": "address", "label": "Адрес", "passed": False}]}},
        {**base, "id": "aggregate-rev1-pass", "evaluation": {"rubric_revision": 1, "criteria": [
            {"id": "address", "label": "Адрес", "passed": True}]}},
        {**base, "id": "aggregate-rev2-fail", "evaluation": {"rubric_revision": 2, "criteria": [
            {"id": "address", "label": "Адрес", "passed": False}]}},
    ]
    with c["store"].db:
        for sample in samples:
            c["store"].db.execute("INSERT INTO workspace VALUES (?,?)", (sample["id"], json.dumps(sample)))
    learning = Learning(c["store"], Accounts(c["store"]))
    group = learning._group(c["group"]["id"], c["users"]["teacher1"]["id"])
    payload = group_insights.aggregate(c["store"], group, c["users"]["teacher1"]["id"])
    errors = [item for item in payload["evidence"] if item["kind"] == "field"]
    assert [(item["configuration_revision"], item["error_count"], item["eligible_attempts"], item["rate_percent"])
            for item in errors] == [(1, 1, 2, 50), (2, 1, 1, 100)]


def test_change_during_generation_returns_stale_snapshot(insights, monkeypatch):
    c = insights
    _complete(c)
    _complete(c)
    monkeypatch.setattr(group_insights.llm, "configuration", lambda: {
        "provider": "ollama", "model": "test-local", "configured": True
    })

    async def changes_group(messages, *, max_tokens, json_mode=False):
        payload = json.loads(messages[1]["content"])
        with c["store"].db:
            c["store"].db.execute("INSERT OR IGNORE INTO group_members VALUES (?,?)",
                                  (c["group"]["id"], c["users"]["student2"]["id"]))
        evidence = payload["evidence"][0]["key"]
        scenario = payload["available_scenarios"][0]["id"]
        return json.dumps({"summary": "Снимок", "difficult_skills": [], "recommendations": [{
            "title": "Практика", "rationale": "Агрегат", "scenario_ids": [scenario],
            "evidence_keys": [evidence]
        }]}, ensure_ascii=False)

    monkeypatch.setattr(group_insights.llm, "complete", changes_group)
    result = c["client"].post(_url(c), headers=c["headers"]["teacher1"]).json()
    assert result["stale"] is True and "во время" in result["stale_reasons"][0]


def test_error_evidence_truncation_is_reported(insights):
    c = insights
    sample = {
        "id": "many-group-errors", "status": "Завершена",
        "teacher_id": c["users"]["teacher1"]["id"], "group_id": c["group"]["id"],
        "scenario_id": c["scenario_id"], "evaluation": {"rubric_revision": 7, "criteria": [
            {"id": f"criterion_{index}", "label": f"Навык {index}", "passed": False}
            for index in range(group_insights.ERROR_EVIDENCE_LIMIT + 3)
        ]},
    }
    with c["store"].db:
        c["store"].db.execute("INSERT INTO workspace VALUES (?,?)", (sample["id"], json.dumps(sample)))
    learning = Learning(c["store"], Accounts(c["store"]))
    group = learning._group(c["group"]["id"], c["users"]["teacher1"]["id"])
    payload = group_insights.aggregate(c["store"], group, c["users"]["teacher1"]["id"])
    errors = [item for item in payload["evidence"] if item["kind"] == "field"]
    assert len(errors) == group_insights.ERROR_EVIDENCE_LIMIT
    assert any("не передано: 3" in item for item in payload["input_limitations"])


def test_stale_legacy_snapshot_keeps_unknown_participant_count():
    saved = {"data_fingerprint": "old", "aggregate": {"completed_attempts": 10, "current_group_size": 4},
             "limitations": []}
    payload = {"aggregate": {"completed_attempts": 12, "current_group_size": 5, "participant_count": 4},
               "evidence": []}
    hydrated = group_insights._hydrate_saved(saved, payload, "new")
    assert hydrated["aggregate"]["participant_count"] is None
    assert any("Число участников старого снимка неизвестно" in item for item in hydrated["limitations"])
    assert not any("Малая выборка" in item for item in hydrated["limitations"])


def test_dds_decisions_drive_group_evidence(insights):
    c = insights
    base = {"status": "Завершена", "teacher_id": c["users"]["teacher1"]["id"], "exercise_mode": "actions",
            "group_id": c["group"]["id"], "scenario_id": c["scenario_id"],
            # Поля готовой карточки не являются навыком ДДС и не должны попасть в выводы.
            "evaluation": {"score_percent": 100, "criteria": [{"id": "address", "label": "Адрес", "passed": True}]}}
    samples = [
        {**base, "id": "dds-1", "dds_review": {"score_percent": 80, "checks": [
            {"id": "briefing", "label": "Доклад вышестоящему начальнику", "passed": False},
            {"id": "update_facts:working", "label": "Обстановка записана", "passed": True}]}},
        {**base, "id": "dds-2", "dds_review": {"score_percent": 60, "checks": [
            {"id": "briefing", "label": "Доклад вышестоящему начальнику", "passed": False},
            {"id": "update_facts:working", "label": "Обстановка записана", "passed": False}]}},
    ]
    with c["store"].db:
        for sample in samples:
            c["store"].db.execute("INSERT INTO workspace VALUES (?,?)", (sample["id"], json.dumps(sample)))
    learning = Learning(c["store"], Accounts(c["store"]))
    group = learning._group(c["group"]["id"], c["users"]["teacher1"]["id"])
    payload = group_insights.aggregate(c["store"], group, c["users"]["teacher1"]["id"])
    errors = [item for item in payload["evidence"] if item["kind"] != "scenario_performance"]
    assert [(item["kind"], item["label"], item["error_count"]) for item in errors] == [
        ("dds", "Доклад вышестоящему начальнику", 2), ("dds", "Обстановка записана", 1)]
    scenario = next(item for item in payload["evidence"] if item["kind"] == "scenario_performance")
    assert scenario["average_score"] == 70
