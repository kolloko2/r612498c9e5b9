"""Оценка решений диспетчера ДДС, а не заполненности карточки.

В основном режиме карточка приходит от Службы 112 уже заполненной, и эталон по
полям почти ничего не измеряет: поля и так верные. Оценивать надо то, что
диспетчер действительно решает:

* принял ли профильную карточку — и правильно ли отказался от чужой;
* обосновал ли отказ, как того требует памятка;
* отразил ли доклад с места нужным статусом и за какое время;
* доложил ли дежурному своей службы и полным ли был доклад;
* записал ли результат перед закрытием работ.

Все проверки выводятся из журнала событий карточки: одно и то же занятие даёт
один и тот же результат, модель в оценке не участвует. Сюда не входит
содержательная правильность реагирования — её оценивает преподаватель.
"""

from __future__ import annotations

from datetime import datetime

ACCEPTED = "Принята"
REFUSED = "Не принята"
WORKS_DONE = "Работы завершены"
WORK_REFUSED = "Отказ от выполнения работ"
MIN_RESULT_CHARS = 10

REFUSAL_LABELS = {
    "foreign_territory": "происшествие не на территории службы",
    "duplicate": "дубль уже отрабатываемой карточки",
    "no_works": "работы выполняться не будут",
}


def _moment(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _service_events(value: dict, service: str) -> list[dict]:
    return [event for event in value.get("events", [])
            if event.get("type") == "service.updated"
            and event.get("detail", {}).get("service") == service]


def _check(check_id: str, label: str, passed: bool | None, detail: str = "",
           critical: bool = False) -> dict:
    return {"id": check_id, "label": label, "passed": passed,
            "detail": detail, "critical": critical}


def _acceptance(value: dict, expectation: dict, service: str) -> list[dict]:
    events = _service_events(value, service)
    statuses = [event["detail"].get("status") for event in events]
    accepted = ACCEPTED in statuses
    refused = REFUSED in statuses
    should_accept = expectation.get("should_accept", True)

    if should_accept:
        return [_check("acceptance", "Профильная карточка принята", accepted,
                       "Карточка профильная: её следовало принять."
                       if not accepted else "", critical=True)]

    kind = expectation.get("refusal_kind", "")
    reason = REFUSAL_LABELS.get(kind, "карточка не подлежит приёму этой службой")
    checks = [_check("refusal", f"Карточка правильно не принята ({reason})", refused and not accepted,
                     "Карточку следовало не принять и обосновать отказ."
                     if not refused or accepted else "", critical=True)]
    # Памятка требует не просто отказа, а объяснения: почему и куда передано.
    comment = ""
    for event in events:
        if event["detail"].get("status") == REFUSED:
            comment = (event["detail"].get("comment") or "").strip()
            break
    keywords = [word.casefold() for word in expectation.get("refusal_keywords", []) if word]
    if refused:
        missing = [word for word in keywords if word not in comment.casefold()]
        checks.append(_check(
            "refusal_reason", "Отказ обоснован в комментарии",
            bool(comment) and not missing,
            ("В обосновании отсутствует: " + ", ".join(missing)) if missing
            else ("Отказ без обоснования." if not comment else ""),
            critical=True))
    return checks


def _update_reactions(value: dict, expectation: dict, service: str) -> list[dict]:
    """Отразил ли диспетчер доклад с места и за какое время."""
    plan = value.get("planned_unlocks") or {}
    if not plan:
        return []
    limit = int(expectation.get("update_response_limit_seconds", 90))
    events = _service_events(value, service)
    checks = []
    for event in value.get("events", []):
        if event.get("type") != "situation.update":
            continue
        update_id = event.get("detail", {}).get("id")
        status = plan.get(update_id)
        if not status:
            continue
        arrived = _moment(event.get("at"))
        reaction = next((item for item in events
                         if item["detail"].get("status") == status
                         and (_moment(item.get("at")) or arrived) >= arrived), None)
        if reaction is None:
            checks.append(_check(f"update:{update_id}", f"Доклад отражён статусом «{status}»",
                                 False, "Статус не выставлен после сообщения с места.",
                                 critical=True))
            continue
        seconds = round(((_moment(reaction.get("at")) or arrived) - arrived).total_seconds())
        checks.append(_check(
            f"update:{update_id}", f"Доклад отражён статусом «{status}»",
            seconds <= limit,
            f"Реакция заняла {seconds} с при нормативе {limit} с." if seconds > limit
            else f"Реакция за {seconds} с."))
    return checks


def _result_recorded(value: dict, service: str) -> list[dict]:
    events = _service_events(value, service)
    closing = next((event for event in events
                    if event["detail"].get("status") in (WORKS_DONE, WORK_REFUSED)), None)
    if closing is None:
        return []
    comment = (closing["detail"].get("comment") or "").strip()
    return [_check("result", "Результат работ записан в комментарий",
                   len(comment) >= MIN_RESULT_CHARS,
                   "Перед закрытием работ не записан итог." if len(comment) < MIN_RESULT_CHARS else "",
                   critical=True)]


def _briefing(value: dict, expectation: dict) -> list[dict]:
    service = expectation.get("brief_service", "")
    if not service:
        return []
    reports = [item for item in value.get("notifications", [])
               if item.get("service") == service]
    delivered = bool(reports)
    checks = [_check("briefing", f"Доклад дежурному ({service}) состоялся", delivered,
                     "Доклад дежурному службы не передан." if not delivered else "",
                     critical=True)]
    if delivered:
        # Факты доклада сверены при его приёме: незавершённый доклад в
        # телефонограмму не попадает, поэтому наличие записи и есть полнота.
        spoken = (reports[-1].get("comment") or "")
        card = value.get("card", {})
        lost = [name for key, name in (("street", "улица"), ("house", "дом"))
                if (card.get(key) or "").strip()
                and (card[key] or "").strip().casefold() not in spoken.casefold()]
        checks.append(_check("briefing_facts", "Адрес передан без потерь", not lost,
                             ("В докладе не прозвучало: " + ", ".join(lost)) if lost else "",
                             critical=True))
    return checks


def review(value: dict, expectation: dict | None) -> dict | None:
    """Разбор решений диспетчера. ``None`` — ожидания не заданы."""
    if not expectation:
        return None
    service = value.get("owner_service") or ""
    if not service:
        return None

    checks: list[dict] = []
    checks += _acceptance(value, expectation, service)
    checks += _update_reactions(value, expectation, service)
    checks += _result_recorded(value, service)
    checks += _briefing(value, expectation)

    judged = [check for check in checks if check["passed"] is not None]
    passed = [check for check in judged if check["passed"]]
    failed_critical = [check for check in judged
                       if not check["passed"] and check["critical"]]
    return {
        "version": "dds-review-v1",
        "service": service,
        "checks": checks,
        "passed_count": len(passed),
        "total_count": len(judged),
        "score_percent": round(len(passed) * 100 / len(judged), 2) if judged else None,
        "critical_errors": [check["label"] for check in failed_critical],
        "note": "Оценка решений по журналу карточки. Содержательную правильность "
                "реагирования подтверждает преподаватель.",
    }


__all__ = ["review", "REFUSAL_LABELS"]
