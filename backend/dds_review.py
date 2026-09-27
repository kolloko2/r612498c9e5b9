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
import re
from text_facts import asserted, lemma, tokens
from briefing import check as briefing_check, reference_card
from service_workflow import no_brigade_completion

ACCEPTED = "Принята"
REFUSED = "Не принята"
WORKS_DONE = "Работы завершены"
WORK_REFUSED = "Отказ от выполнения работ"
# Промежуточные статусы, которые должны быть активированы до итогового.
CYCLE = (ACCEPTED, "Начало реагирования", "Прибытие", "Проведение работ")
MIN_RESULT_CHARS = 10


def _contains(text: str, phrase: str) -> bool:
    """Match a whole fact, so house 10 is not accepted as house 100."""
    actual, wanted = [lemma(w) for w in tokens(text)], [lemma(w) for w in tokens(phrase)]
    return bool(wanted) and any(actual[i:i+len(wanted)] == wanted for i in range(len(actual)-len(wanted)+1))

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
    no_brigade = _closed_without_brigade(value, service)
    accepted = ACCEPTED in statuses or (no_brigade and expectation.get('should_accept', True))
    refused = REFUSED in statuses or (no_brigade and not expectation.get('should_accept', True))
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
        if event["detail"].get("status") == REFUSED or (no_brigade and event['detail'].get('status') == WORKS_DONE):
            comment = (event["detail"].get("comment") or "").strip()
            break
    keywords = [word.casefold() for word in expectation.get("refusal_keywords", []) if word]
    if refused:
        missing = [word for word in keywords if not _contains(comment, word)]
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
    delivered = {event.get('detail', {}).get('id') for event in value.get('events', [])
                 if event.get('type') == 'situation.update'}
    for update_id, status in plan.items():
        if update_id not in delivered:
            checks.append(_check(f'update:{update_id}', f'Доклад отражён статусом «{status}»',
                                 False, 'Карточка завершена до поступления и обработки вводной.',
                                 critical=True))
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
        keywords = expectation.get('update_keywords', {}).get(update_id, [])
        if keywords:
            comment = reaction['detail'].get('comment', '')
            lost = [word for word in keywords if not asserted(comment, word)]
            checks.append(_check(f'update_facts:{update_id}',
                                 f'Обстановка записана при статусе «{status}»', not lost,
                                 'В комментарии не отражено: ' + ', '.join(lost) if lost else
                                 'Существенные сведения доклада сохранены.', critical=True))
    return checks


def _closed_without_brigade(value: dict, service: str) -> bool:
    events = _service_events(value, service)
    return bool(events and not value.get('assigned_crew') and no_brigade_completion(
        service, None, events[0]['detail'].get('status'), events[0]['detail'].get('comment', '')))


def unfinished(value: dict, expectation: dict | None) -> list[str]:
    """Required DDS work still missing; teacher may force-finish and retain failures."""
    if not expectation or not value.get('owner_service'):
        return []
    service = value['owner_service']
    if _closed_without_brigade(value, service):
        return [] if value.get('processed_at') else ['отметка об отработке происшествия']
    statuses = [item.get('detail', {}).get('status') for item in _service_events(value, service)]
    if not expectation.get('should_accept', True):
        return [] if REFUSED in statuses else ['отказ от непрофильной карточки']
    missing = []
    if value.get('crew_options') and not value.get('assigned_crew'):
        missing.append('назначение реагирующей бригады')
    if not any(status in (WORKS_DONE, WORK_REFUSED) for status in statuses):
        missing.append('итоговый статус своей службы')
    # Карточка отработана, когда активированы все статусы цикла (ответ
    # заказчика 27.09.2026). Мотивированный отказ от работ завершает цикл раньше.
    if WORK_REFUSED not in statuses:
        skipped = [status for status in CYCLE if status not in statuses]
        if skipped:
            missing.append('статусы цикла: ' + ', '.join(skipped))
    if not value.get('processed_at'):
        missing.append('отметка об отработке происшествия')
    delivered = {item.get('detail', {}).get('id') for item in value.get('events', [])
                 if item.get('type') == 'situation.update'}
    if any(update_id not in delivered for update_id in (value.get('planned_unlocks') or {})):
        missing.append('запланированные оперативные вводные')
    return missing


def _result_recorded(value: dict, service: str, expectation: dict) -> list[dict]:
    events = _service_events(value, service)
    closing = next((event for event in events
                    if event["detail"].get("status") in (WORKS_DONE, WORK_REFUSED)), None)
    if closing is None:
        return []
    comment = (closing["detail"].get("comment") or "").strip()
    missing = [word for word in expectation.get('result_keywords', []) if not asserted(comment, word)]
    return [_check("result", "Результат работ записан в комментарий",
                   len(comment) >= MIN_RESULT_CHARS and not missing,
                   "В итоге не отражено: " + ', '.join(missing) if missing else
                   ("Перед закрытием работ не записан итог." if len(comment) < MIN_RESULT_CHARS else ""),
                   critical=True)]


def _briefing(value: dict, expectation: dict) -> list[dict]:
    service = expectation.get("brief_service", "")
    if not service:
        return []
    briefing_ids = {item.get('detail', {}).get('message_id') for item in value.get('events', [])
                    if item.get('type') == 'notification.recorded'
                    and item.get('detail', {}).get('source') == 'briefing'
                    and (not value.get('sip_extension') or value.get('exercise_mode') != 'actions'
                         or item.get('detail', {}).get('transport') == 'sip')}
    reports = [item for item in value.get("notifications", [])
               if item.get("service") == service and item.get('message_id') in briefing_ids]
    delivered = bool(reports)
    checks = [_check("briefing", f"Доклад по телефону ({service}) состоялся", delivered,
                     "Доклад по телефону не передан." if not delivered else "",
                     critical=True)]
    if delivered:
        # Факты доклада сверены при его приёме: незавершённый доклад в
        # телефонограмму не попадает, поэтому наличие записи и есть полнота.
        spoken = (reports[-1].get("comment") or "")
        # A correction to a received card must not rewrite the source facts
        # against which the dispatcher is assessed.
        card = reference_card(value, expectation)
        lost = [item['label'] for item in briefing_check(spoken, card)['checks']
                if not item['passed']]
        lost.extend(word for word in expectation.get('brief_keywords', [])
                    if not asserted(spoken, word))
        checks.append(_check("briefing_facts", "Сведения переданы без потерь", not lost,
                             ("В докладе не прозвучало: " + ", ".join(lost)) if lost else "",
                             critical=True))
    return checks


def _card_corrections(value: dict, expectation: dict) -> list[dict]:
    allowed = {'city', 'district', 'area', 'object', 'street', 'house', 'building',
               'structure', 'apartment', 'entrance', 'address_note', 'description',
               'incident_type'}
    # ДДС не правит карточку 112, а сообщает об ошибке в 112 по телефону
    # (ответ заказчика 27.09.2026). Засчитывается сообщение с правильным значением.
    expected = expectation.get('expected_corrections') or {}
    reports = value.get('error_reports') or []
    checks = []
    for field, answer in expected.items():
        if field not in allowed or not isinstance(answer, str):
            continue
        told = [item for item in reports if item.get('field') == field]
        right = any(item.get('correct_value', '').strip().casefold() == answer.strip().casefold()
                    or _contains(item.get('correct_value', ''), answer) for item in told)
        checks.append(_check(f'correction:{field}', f'В 112 сообщено об ошибке в поле «{field}»', right,
                             '' if right else (f'Сообщено неверное значение. Ожидается: {answer}' if told
                                               else f'Ошибка не передана в 112. Ожидается: {answer}'),
                             critical=field in {'street', 'house', 'incident_type'}))
    return checks


def _crew_decision(value: dict, expectation: dict) -> list[dict]:
    if not value.get('crew_options') or not expectation.get('should_accept', True):
        return []
    crew = value.get('assigned_crew') or {}
    events = value.get('events', [])
    assigned = next((event for event in events if event.get('type') == 'crew.assigned'), None)
    departure = next((event for event in events if event.get('type') == 'service.updated'
                      and event.get('detail', {}).get('status') == 'Начало реагирования'), None)
    checks = [_check('crew_assignment', 'Бригада выбрана до начала реагирования',
                     bool(crew and assigned and (not departure or assigned['seq'] < departure['seq'])),
                     'Назначьте бригаду перед регистрацией выезда.', critical=True)]
    expected = expectation.get('expected_crew_id')
    if expected:
        checks.append(_check('crew_choice', 'Выбрана нужная бригада',
                             crew.get('id') == expected, f'Ожидается: {expected}', critical=True))
    if expectation.get('leadership_decision_required'):
        checks.append(_check('leadership_decision', 'Решение принято руководством',
                             crew.get('decision_by') == 'leadership' and bool(crew.get('decision_note')),
                             'Укажите решение руководителя.', critical=True))
    return checks


def review(value: dict, expectation: dict | None) -> dict | None:
    """Разбор решений диспетчера. ``None`` — ожидания не заданы."""
    if not expectation:
        return None
    service = value.get("owner_service") or ""
    if not service:
        return None

    checks: list[dict] = []
    timing = (value.get('evaluation') or {}).get('timing') or {}
    if value.get('status') == 'Завершена':
        # Нормативы по ответу заказчика 27.09.2026; остальные сроки не нормируются.
        checks.append(_check('receipt_time',
                             f"Карточка открыта за {timing.get('response_limit_seconds') or 30} секунд",
                             timing.get('response_within_limit') is True,
                             'Карточка не открыта.' if not value.get('opened_at')
                             else f"Открытие через {timing.get('response_seconds')} с.",
                             critical=True))
        checks.append(_check('first_record',
                             f"Первая запись (статус и текст) за {timing.get('limit_seconds') or 180} секунд",
                             timing.get('within_limit') is True,
                             'Запись со статусом и текстом не внесена.' if not value.get('first_record_at')
                             else f"Первая запись через {timing.get('first_record_seconds')} с."))
    checks += _acceptance(value, expectation, service)
    # A terminal no-brigade decision has no subsequent reports to wait for.
    if not _closed_without_brigade(value, service):
        checks += _update_reactions(value, expectation, service)
    checks += _result_recorded(value, service, expectation)
    checks += _briefing(value, expectation)
    checks += _card_corrections(value, expectation)
    if not _closed_without_brigade(value, service) or expectation.get('expected_crew_id'):
        checks += _crew_decision(value, expectation)
    if value.get('status') == 'Завершена':
        for label in unfinished(value, expectation):
            checks.append(_check('completion:' + label, label.capitalize(), False,
                                 'Полный цикл карточки не завершён.', critical=True))

    judged = [check for check in checks if check["passed"] is not None]
    for check in judged:
        check['weight'] = expectation.get('check_weights', {}).get(check['id'], 1)
    passed = [check for check in judged if check["passed"]]
    total_weight = sum(check['weight'] for check in judged)
    score = round(sum(check['weight'] for check in passed) * 100 / total_weight, 2) if total_weight else None
    failed_critical = [check for check in judged
                       if not check["passed"] and check["critical"]]
    return {
        "version": "dds-review-v1",
        "service": service,
        "checks": checks,
        "passed_count": len(passed),
        "total_count": len(judged),
        "score_percent": score,
        "passed": bool(judged) and score >= expectation.get('pass_percent', 100) and not failed_critical,
        "critical_errors": [check["label"] for check in failed_critical],
        "note": "Оценка решений по журналу карточки. Содержательную правильность "
                "реагирования подтверждает преподаватель.",
    }


__all__ = ["review", "REFUSAL_LABELS"]
