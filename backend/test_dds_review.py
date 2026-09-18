"""Оценка решений диспетчера ДДС по журналу карточки."""

from dds_review import review

SERVICE = "Служба 101"


def event(seq, kind, at, **detail):
    return {"seq": seq, "type": kind, "at": at, "detail": detail}


def card(events, *, notifications=None, unlocks=None, expectation_service=SERVICE):
    return {
        "owner_service": expectation_service,
        "card": {"street": "Берзарина", "house": "21", "incident_type": "Пожар в квартире"},
        "events": events,
        "notifications": notifications or [],
        "planned_unlocks": unlocks or {},
    }


def verdict(result, check_id):
    return next(check for check in result["checks"] if check["id"] == check_id)


def test_profile_card_must_be_accepted():
    accepted = card([event(1, "service.updated", "2026-09-18T10:00:00+00:00",
                           service=SERVICE, status="Принята", comment="")])
    result = review(accepted, {"should_accept": True})
    assert verdict(result, "acceptance")["passed"] is True
    assert result["critical_errors"] == []

    ignored = card([])
    failed = review(ignored, {"should_accept": True})
    assert verdict(failed, "acceptance")["passed"] is False
    assert failed["critical_errors"]


def test_foreign_card_must_be_refused_with_a_reason():
    """Памятка требует не просто отказа, а обоснования: почему и куда передано."""
    expectation = {"should_accept": False, "refusal_kind": "foreign_territory",
                   "refusal_keywords": ["территория", "передано"]}

    wrong = card([event(1, "service.updated", "2026-09-18T10:00:00+00:00",
                        service=SERVICE, status="Принята", comment="")])
    assert verdict(review(wrong, expectation), "refusal")["passed"] is False

    bare = card([event(1, "service.updated", "2026-09-18T10:00:00+00:00",
                       service=SERVICE, status="Не принята", comment="не наше")])
    result = review(bare, expectation)
    assert verdict(result, "refusal")["passed"] is True
    reason = verdict(result, "refusal_reason")
    assert reason["passed"] is False and "территория" in reason["detail"]

    good = card([event(1, "service.updated", "2026-09-18T10:00:00+00:00", service=SERVICE,
                       status="Не принята",
                       comment="Не наша территория, передано в профильную ДДС округа")])
    assert verdict(review(good, expectation), "refusal_reason")["passed"] is True


def test_field_report_must_be_reflected_in_time():
    unlocks = {"dispatched": "Начало реагирования"}
    quick = card([
        event(1, "situation.update", "2026-09-18T10:00:00+00:00", id="dispatched"),
        event(2, "service.updated", "2026-09-18T10:00:30+00:00",
              service=SERVICE, status="Начало реагирования", comment=""),
    ], unlocks=unlocks)
    result = review(quick, {"should_accept": True, "update_response_limit_seconds": 90})
    assert verdict(result, "update:dispatched")["passed"] is True
    assert "30 с" in verdict(result, "update:dispatched")["detail"]

    slow = card([
        event(1, "situation.update", "2026-09-18T10:00:00+00:00", id="dispatched"),
        event(2, "service.updated", "2026-09-18T10:05:00+00:00",
              service=SERVICE, status="Начало реагирования", comment=""),
    ], unlocks=unlocks)
    assert verdict(review(slow, {"should_accept": True,
                                 "update_response_limit_seconds": 90}),
                   "update:dispatched")["passed"] is False

    ignored = card([event(1, "situation.update", "2026-09-18T10:00:00+00:00", id="dispatched")],
                   unlocks=unlocks)
    missed = verdict(review(ignored, {"should_accept": True}), "update:dispatched")
    assert missed["passed"] is False and missed["critical"] is True


def test_result_must_be_recorded_before_closing_works():
    closed = card([event(1, "service.updated", "2026-09-18T10:10:00+00:00", service=SERVICE,
                         status="Работы завершены", comment="ок")])
    assert verdict(review(closed, {"should_accept": True}), "result")["passed"] is False

    proper = card([event(1, "service.updated", "2026-09-18T10:10:00+00:00", service=SERVICE,
                         status="Работы завершены",
                         comment="Возгорание ликвидировано, пострадавших нет")])
    assert verdict(review(proper, {"should_accept": True}), "result")["passed"] is True


def test_briefing_and_its_facts_are_checked():
    expectation = {"should_accept": True, "brief_service": SERVICE}
    silent = card([])
    assert verdict(review(silent, expectation), "briefing")["passed"] is False

    lost = card([], notifications=[{"service": SERVICE, "comment": "Докладываю, пожар в квартире"}])
    facts = verdict(review(lost, expectation), "briefing_facts")
    assert facts["passed"] is False and "улица" in facts["detail"]

    full = card([], notifications=[{"service": SERVICE,
                                    "comment": "Берзарина, дом 21, пожар в квартире"}])
    assert verdict(review(full, expectation), "briefing_facts")["passed"] is True


def test_score_counts_only_judged_checks():
    unlocks = {"dispatched": "Начало реагирования"}
    value = card([
        event(1, "service.updated", "2026-09-18T10:00:00+00:00",
              service=SERVICE, status="Принята", comment=""),
        event(2, "situation.update", "2026-09-18T10:01:00+00:00", id="dispatched"),
        event(3, "service.updated", "2026-09-18T10:01:20+00:00",
              service=SERVICE, status="Начало реагирования", comment=""),
    ], unlocks=unlocks)
    result = review(value, {"should_accept": True})
    assert result["total_count"] == 2 and result["passed_count"] == 2
    assert result["score_percent"] == 100.0


def test_review_is_skipped_without_expectation_or_service():
    assert review(card([]), None) is None
    assert review({**card([]), "owner_service": ""}, {"should_accept": True}) is None
