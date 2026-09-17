from __future__ import annotations

import pytest
from pydantic import ValidationError

from evaluation import Rubric, evaluate, normalize


def _rubric(**overrides) -> dict:
    value = {
        "title": "Учебная проверка",
        "time_limit_seconds": 30,
        "criteria": [
            {
                "id": "caller",
                "label": "Имя заявителя",
                "field": "caller_name",
                "expected": ["Алёна", "Алена Иванова"],
                "mode": "equals",
                "weight": 2,
            },
            {
                "id": "details",
                "label": "Описание",
                "field": "description",
                "expected": ["учебный адрес", "вход закрыт"],
                "mode": "contains_all",
            },
            {
                "id": "access",
                "label": "Нет доступа",
                "field": "no_access",
                "expected": ["true"],
                "mode": "equals",
                "weight": 3,
            },
            {
                "id": "services",
                "label": "Выбранные службы",
                "field": "services",
                "expected": ["Служба 101", "Служба 103"],
                "mode": "set_equals",
                "weight": 4,
            },
        ],
    }
    value.update(overrides)
    return value


def test_normalization_is_exact_and_bounded() -> None:
    assert normalize("  АЛЁНА\tИванова  ") == "алена иванова"
    assert normalize("адрес,  дом") == "адрес, дом"
    assert normalize("адрес, дом") != normalize("адрес дом")


def test_weighted_evaluation_and_timing_are_separate() -> None:
    result = evaluate(
        _rubric(),
        {
            "caller_name": "  АЛЁНА ",
            "description": "Вход закрыт. Назван УЧЕБНЫЙ АДРЕС.",
            "no_access": False,
            "services": ["служба 103", "СЛУЖБА 101", "служба 101"],
        },
        31,
    )

    assert result["status"] == "evaluated"
    assert result["rubric_title"] == "Учебная проверка"
    assert result["earned_weight"] == 7
    assert result["total_weight"] == 10
    assert result["score_percent"] == 70.0
    assert [item["passed"] for item in result["criteria"]] == [True, True, False, True]
    assert result["criteria"][2]["actual"] is False
    assert "Проверьте признак" in result["criteria"][2]["recommendation"]
    assert result["timing"] == {
        "elapsed_seconds": 31,
        "limit_seconds": 30,
        "within_limit": False,
        "response_seconds": None,
        "response_limit_seconds": 30,
        "response_within_limit": None,
    }
    assert len(result["limitations"]) == 2


def test_contains_all_is_literal_not_regex_or_fuzzy() -> None:
    rubric = _rubric(
        criteria=[
            {
                "id": "literal",
                "label": "Описание",
                "field": "description",
                "expected": ["дом. 5", "вход"],
                "mode": "contains_all",
            }
        ]
    )
    result = evaluate(rubric, {"description": "ДомX 5, вход открыт"}, 1)
    assert result["criteria"][0]["passed"] is False


def test_not_configured_does_not_infer_a_score_or_expectations() -> None:
    result = evaluate(None, {"description": "любые данные"}, 7)
    assert result == {
        "version": "rubric-v1",
        "status": "not_configured",
        "rubric_title": None,
        "score_percent": None,
        "earned_weight": 0,
        "total_weight": 0,
        "criteria": [],
        "timing": {"elapsed_seconds": 7, "limit_seconds": None, "within_limit": None,
                   "response_seconds": None, "response_limit_seconds": None,
                   "response_within_limit": None},
        "limitations": [
            "Балл по эталону не учитывает семантику, грамматику и качество голосового взаимодействия; ИИ-разбор запрашивается отдельно.",
            "Настраиваемая рубрика является учебной конфигурацией, а не официальным регламентом 112.",
        ],
    }


@pytest.mark.parametrize(
    "criterion",
    [
        {"id": "bad id", "label": "Поле", "field": "city", "expected": ["Москва"], "mode": "equals"},
        {"id": "x", "label": "Поле", "field": "secret", "expected": ["x"], "mode": "equals"},
        {"id": "x", "label": "Флаг", "field": "injured", "expected": ["да"], "mode": "equals"},
        {"id": "x", "label": "Флаг", "field": "injured", "expected": ["true"], "mode": "contains_all"},
        {"id": "x", "label": "Службы", "field": "services", "expected": ["101"], "mode": "equals"},
        {"id": "x", "label": "Текст", "field": "description", "expected": ["x"], "mode": "set_equals"},
        {"id": "x", "label": "Текст", "field": "description", "expected": ["Алёна", " алена "], "mode": "equals"},
    ],
)
def test_invalid_field_mode_and_expected_combinations_are_rejected(criterion: dict) -> None:
    with pytest.raises(ValidationError):
        Rubric.model_validate(_rubric(criteria=[criterion]))


def test_schema_rejects_duplicates_extras_and_bounds() -> None:
    duplicate = _rubric(criteria=[_rubric()["criteria"][0], _rubric()["criteria"][0]])
    with pytest.raises(ValidationError, match="unique"):
        Rubric.model_validate(duplicate)
    with pytest.raises(ValidationError):
        Rubric.model_validate(_rubric(extra=True))
    with pytest.raises(ValidationError):
        Rubric.model_validate(_rubric(title="no"))
    with pytest.raises(ValidationError):
        Rubric.model_validate(_rubric(time_limit_seconds=0))
    with pytest.raises(ValidationError):
        Rubric.model_validate(_rubric(criteria=[]))


def test_wrong_card_types_and_elapsed_are_rejected_without_coercion() -> None:
    rubric = _rubric(criteria=[{"id": "flag", "label": "Флаг", "field": "injured", "expected": ["true"], "mode": "equals"}])
    with pytest.raises(ValueError, match="boolean"):
        evaluate(rubric, {"injured": 1}, 2)
    with pytest.raises(ValueError, match="elapsed"):
        evaluate(None, {}, -1)


def test_response_and_handling_norms_are_measured_separately() -> None:
    """30 секунд на реакцию и 3 минуты на обработку — разные нормативы."""
    rubric = _rubric()
    assert rubric["time_limit_seconds"] == 30  # задан явно в фикстуре
    from evaluation import Rubric
    defaults = Rubric.model_validate({"title": "Эталон по умолчанию", "criteria": rubric["criteria"]})
    assert defaults.time_limit_seconds == 180 and defaults.response_limit_seconds == 30

    card = {"caller_name": "Алёна", "description": "Вход закрыт. Назван УЧЕБНЫЙ АДРЕС.",
            "no_access": False, "services": ["служба 103", "СЛУЖБА 101"]}
    quick = evaluate(rubric, card, 20, 5)
    assert quick["timing"]["response_seconds"] == 5
    assert quick["timing"]["response_within_limit"] is True
    assert quick["timing"]["within_limit"] is True

    slow = evaluate(rubric, card, 20, 45)
    assert slow["timing"]["response_within_limit"] is False
    # Просроченная реакция не отменяет соблюдение норматива обработки.
    assert slow["timing"]["within_limit"] is True

    unmeasured = evaluate(rubric, card, 20)
    assert unmeasured["timing"]["response_seconds"] is None
    assert unmeasured["timing"]["response_within_limit"] is None

    with pytest.raises(ValueError):
        evaluate(rubric, card, 20, -1)
