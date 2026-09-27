"""Pure service-response and incident-status rules for the ARM training UI.

These rules reproduce the ARM 112 training source and never contact any
emergency service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


SOURCE_VERSION = "Работа с АРМ 112, учебный источник, стр. 21–27"
SERVICE_WORKFLOW_SOURCE = SOURCE_VERSION

STATUS_ALIASES: dict[str, str] = {
    "Выезд": "Начало реагирования",
    "Завершение": "Работы завершены",
    "Отбой": "Отказ от выполнения работ",
}

RESPONSE_STATUSES: tuple[str, ...] = (
    "Добавлена",
    "Получена службой",
    "Принята",
    "Не принята",
    "Начало реагирования",
    "Прибытие",
    "Проведение работ",
    "Работы завершены",
    "Отказ от выполнения работ",
)

NO_BRIGADE_COMMENT = "Завершение работ без бригады."

_PROGRESS = (
    "Начало реагирования",
    "Прибытие",
    "Проведение работ",
    "Работы завершены",
)


def _canonical(status: str) -> str:
    if not isinstance(status, str):
        raise ValueError("status must be a string")
    value = status.strip()
    value = STATUS_ALIASES.get(value, value)
    if value not in RESPONSE_STATUSES:
        raise ValueError(f"unknown service status: {status}")
    return value


def _is_ambulance(service: str) -> bool:
    return isinstance(service, str) and "103" in service


def no_brigade_completion(service: str, old: str | None, new: str, comment: str) -> bool:
    """The source's explicit initial 103 completion, not arbitrary early closure."""
    return (_is_ambulance(service) and old in (None, '', 'Добавлена', 'Получена службой')
            and new == 'Работы завершены'
            and NO_BRIGADE_COMMENT.rstrip('.').casefold() in (comment or '').casefold())


def allowed_statuses(service: str, current_status: str | None) -> list[str]:
    """Return canonical statuses reachable in one edit.

    The source permits skipping forward after acceptance, but never moving a
    responding service backwards. Technical receipt is automatic.
    """
    if current_status is None or (
        isinstance(current_status, str) and not current_status.strip()
    ):
        current = "Добавлена"
    else:
        try:
            current = _canonical(current_status)
        except ValueError:
            return []
    ambulance = _is_ambulance(service)

    if current == "Добавлена":
        result = ["Принята", "Не принята"]
    elif current == "Получена службой":
        result = ["Принята", "Не принята"]
    elif current == "Не принята":
        result = ["Принята"]
    elif current == "Принята":
        result = list(_PROGRESS) + ["Отказ от выполнения работ"]
    elif current in _PROGRESS[:-1]:
        index = _PROGRESS.index(current)
        result = list(_PROGRESS[index + 1 :]) + ["Отказ от выполнения работ"]
    else:
        # Both terminal outcomes prohibit further service edits.
        result = []

    if ambulance:
        result = [
            status
            for status in result
            if status not in {"Не принята", "Отказ от выполнения работ"}
        ]
        if current in {"Добавлена", "Получена службой"}:
            result.append("Работы завершены")
    return result


def validate_transition(
    service: str, old: str | None, new: str, comment: str | None = None
) -> str:
    """Validate a service edit and return the canonical new status."""
    if old is None or (isinstance(old, str) and not old.strip()):
        old_status = "Добавлена"
    else:
        try:
            old_status = _canonical(old)
        except ValueError:
            old_status = str(old).strip()
    new_status = _canonical(new)
    if new_status not in allowed_statuses(service, old_status):
        raise ValueError(
            f"transition is not allowed for {service}: {old_status} -> {new_status}"
        )

    text = comment.strip() if isinstance(comment, str) else ""
    if new_status in {"Не принята", "Отказ от выполнения работ"} and not text:
        raise ValueError(f"comment is required for status: {new_status}")

    # For ambulance, a direct completion without response phases is the source's
    # representation of completion without a brigade, and must remain explicit.
    if (
        _is_ambulance(service)
        and new_status == "Работы завершены"
        and old_status in {"Добавлена", "Получена службой"}
        and NO_BRIGADE_COMMENT.rstrip(".").casefold() not in text.casefold()
    ):
        raise ValueError(
            f"direct ambulance completion requires comment: {NO_BRIGADE_COMMENT}"
        )
    return new_status


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _selected_services(value: Any) -> list[str]:
    card = _get(value, "card", {})
    services = _get(card, "services", [])
    if not isinstance(services, (list, tuple)):
        return []
    return [service for service in services if isinstance(service, str)]


def _service_states(value: Any) -> dict[str, Any]:
    states = _get(value, "service_states", {})
    return dict(states) if isinstance(states, Mapping) else {}


def incident_status(value: Any, now_datetime: datetime | None = None) -> str:
    """Derive the source-backed incident status without mutating ``value``.

    A refusal is reported as ``Отказ`` only after ``checked_by`` is present.  This
    guard avoids representing the source's required verification as if the
    simulator had performed it automatically.
    """
    finished_at = _as_datetime(_get(value, "finished_at"))
    now_value = finished_at or (
        _as_datetime(now_datetime)
        if now_datetime is not None
        else datetime.now(timezone.utc)
    )
    if now_value is None:
        raise ValueError("now_datetime must be a datetime")

    revision = _get(value, "revision", 0)
    revision = (
        revision
        if isinstance(revision, int) and not isinstance(revision, bool)
        else 0
    )
    services = _selected_services(value)
    states = _service_states(value)

    canonical_states: dict[str, str] = {}
    for service, entry in states.items():
        raw_status = _get(entry, "status", "Добавлена")
        try:
            canonical_states[str(service)] = _canonical(raw_status)
        except ValueError:
            # Legacy/corrupt entries cannot establish a terminal derived state.
            continue

    statuses = [canonical_states.get(service, "Добавлена") for service in services]
    if revision > 0 and not services:
        return "Завершена"
    if services and all(status == "Работы завершены" for status in statuses):
        return "Завершена"

    refused = any(
        status in {"Не принята", "Отказ от выполнения работ"}
        for status in statuses
    )
    if refused and _get(value, "checked_by"):
        return "Отказ"

    registered_at = _as_datetime(_get(value, "registered_at"))
    if registered_at is None:
        registered_at = _as_datetime(_get(value, "saved_at"))
    if (
        revision > 0
        and services
        and registered_at is not None
        and (now_value - registered_at).total_seconds() > 48 * 60 * 60
    ):
        return "Не завершено"

    for service in services if revision > 0 else ():
        entry = states.get(service, {})
        status = canonical_states.get(service, "Добавлена")
        added_at = _as_datetime(_get(entry, "added_at"))
        if added_at is None and status in {"Добавлена", "Получена службой"}:
            added_at = _as_datetime(_get(entry, "at"))
        if status in {"Добавлена", "Получена службой"} and added_at is not None:
            if (now_value - added_at).total_seconds() > 30:
                return "Не оповещено"

    if _get(value, "processed_at"):
        return "Отработана"
    return "Зарегистрирована" if revision > 0 else "Новая"


__all__ = [
    "NO_BRIGADE_COMMENT",
    "RESPONSE_STATUSES",
    "SERVICE_WORKFLOW_SOURCE",
    "SOURCE_VERSION",
    "STATUS_ALIASES",
    "allowed_statuses",
    "incident_status",
    "validate_transition",
]
