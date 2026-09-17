from datetime import datetime, timedelta, timezone

import pytest

from service_workflow import (
    NO_BRIGADE_COMMENT,
    allowed_statuses,
    incident_status,
    validate_transition,
)


NOW = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)


def _incident(*, revision=1, services=("Служба 101",), states=None, **extra):
    return {
        "revision": revision,
        "registered_at": (NOW - timedelta(minutes=1)).isoformat(),
        "card": {"services": list(services)},
        "service_states": states or {},
        **extra,
    }


def test_forward_skips_aliases_and_terminal_states() -> None:
    assert allowed_statuses("Служба 101", "Принята") == [
        "Начало реагирования",
        "Прибытие",
        "Проведение работ",
        "Работы завершены",
        "Отказ от выполнения работ",
    ]
    assert (
        validate_transition("Служба 101", "Выезд", "Завершение", "")
        == "Работы завершены"
    )
    assert allowed_statuses("Служба 101", "Завершение") == []
    with pytest.raises(ValueError, match="not allowed"):
        validate_transition("Служба 101", "Прибытие", "Начало реагирования", "")


def test_rejection_and_refusal_require_comments() -> None:
    with pytest.raises(ValueError, match="comment is required"):
        validate_transition("Служба 101", "Добавлена", "Не принята", "  ")
    assert validate_transition("Служба 101", "Не принята", "Принята", "") == "Принята"
    with pytest.raises(ValueError, match="comment is required"):
        validate_transition("Служба 101", "Принята", "Отбой", None)


def test_ambulance_has_no_rejection_or_refusal_status() -> None:
    statuses = allowed_statuses("Служба 103", "Принята")
    assert "Не принята" not in allowed_statuses("Служба 103", "Добавлена")
    assert "Отказ от выполнения работ" not in statuses
    with pytest.raises(ValueError, match="not allowed"):
        validate_transition("Служба 103", "Принята", "Отказ от выполнения работ", "Нет бригады")
    assert (
        validate_transition("Служба 103", "Принята", "Работы завершены", "")
        == "Работы завершены"
    )
    with pytest.raises(ValueError, match="requires comment"):
        validate_transition("Служба 103", "Добавлена", "Работы завершены", "")
    assert validate_transition(
        "Служба 103",
        "Получена службой",
        "Работы завершены",
        "Нет выезда — ЗАВЕРШЕНИЕ РАБОТ БЕЗ БРИГАДЫ; причина учебная",
    ) == "Работы завершены"


def test_initial_choices_hide_automatic_receipt_and_tolerate_legacy_state() -> None:
    assert allowed_statuses("Служба 101", None) == ["Принята", "Не принята"]
    assert allowed_statuses("Служба 103", "") == ["Принята", "Работы завершены"]
    assert allowed_statuses("Служба 101", "Старый неизвестный статус") == []
    with pytest.raises(ValueError, match="unknown service status"):
        validate_transition("Служба 101", "Добавлена", "Неверный новый", "")


def test_incident_notification_boundary_is_strictly_over_30_seconds() -> None:
    at_30 = _incident(
        states={
            "Служба 101": {
                "status": "Добавлена",
                "added_at": (NOW - timedelta(seconds=30)).isoformat(),
            }
        }
    )
    at_31 = _incident(
        states={
            "Служба 101": {
                "status": "Добавлена",
                "added_at": (NOW - timedelta(seconds=31)).isoformat(),
            }
        }
    )
    assert incident_status(at_30, NOW) == "Зарегистрирована"
    assert incident_status(at_31, NOW) == "Не оповещено"
    received = _incident(
        states={
            "Служба 101": {
                "status": "Получена службой",
                "added_at": (NOW - timedelta(seconds=31)).isoformat(),
            }
        }
    )
    assert incident_status(received, NOW) == "Не оповещено"


def test_incident_48_hour_boundary_and_terminal_completion() -> None:
    at_48 = _incident(registered_at=(NOW - timedelta(hours=48)).isoformat())
    over_48 = _incident(
        registered_at=(NOW - timedelta(hours=48, seconds=1)).isoformat()
    )
    assert incident_status(at_48, NOW) == "Зарегистрирована"
    assert incident_status(over_48, NOW) == "Не завершено"
    completed = _incident(
        services=("Служба 101", "Служба 103"),
        states={
            "Служба 101": {"status": "Завершение"},
            "Служба 103": {"status": "Работы завершены", "comment": NO_BRIGADE_COMMENT},
        },
    )
    assert incident_status(completed, NOW) == "Завершена"
    assert incident_status(_incident(services=()), NOW) == "Завершена"


def test_refusal_needs_recorded_check_before_incident_becomes_refusal() -> None:
    value = _incident(states={"Служба 101": {"status": "Не принята"}})
    assert incident_status(value, NOW) == "Зарегистрирована"
    value["checked_by"] = "teacher-id"
    assert incident_status(value, NOW) == "Отказ"


def test_processed_and_new_fallbacks() -> None:
    value = _incident(
        states={"Служба 101": {"status": "Принята"}},
        processed_at=NOW.isoformat(),
    )
    assert incident_status(value, NOW) == "Отработана"
    assert incident_status(_incident(revision=0, services=()), NOW) == "Новая"


def test_finished_timestamp_freezes_age_based_derivation() -> None:
    value = _incident(
        registered_at=(NOW - timedelta(hours=47)).isoformat(),
        finished_at=NOW.isoformat(),
        states={"Служба 101": {"status": "Принята"}},
    )
    assert incident_status(value, NOW + timedelta(days=10)) == "Зарегистрирована"
