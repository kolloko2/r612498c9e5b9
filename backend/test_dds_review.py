"""Оценка решений диспетчера ДДС по журналу карточки."""

from dds_review import review

SERVICE = "Служба 101"


def test_ambulance_without_brigade_does_not_require_future_reports():
    from dds_review import unfinished
    value = card([event(1, 'service.updated', '2026-09-18T10:00:00+00:00',
                       service='Служба 103', status='Работы завершены',
                       comment='Завершение работ без бригады.')],
                 expectation_service='Служба 103', unlocks={'later': 'Прибытие'})
    value['crew_options'] = [{'id': '17'}]
    value['processed_at'] = '2026-09-18T10:00:01+00:00'
    result = review(value, {'should_accept': True})
    assert verdict(result, 'acceptance')['passed'] is True
    assert not any(check['id'].startswith('update:') for check in result['checks'])
    assert unfinished(value, {'should_accept': True}) == []
    # A teacher explicitly expecting a brigade still detects a wrong decision.
    result = review(value, {'should_accept': True, 'expected_crew_id': '17'})
    assert verdict(result, 'crew_choice')['passed'] is False


def test_other_service_cannot_use_ambulance_exception():
    value = card([event(1, 'service.updated', '2026-09-18T10:00:00+00:00',
                       service=SERVICE, status='Работы завершены',
                       comment='Завершение работ без бригады.')])
    assert verdict(review(value, {'should_accept': True}), 'acceptance')['passed'] is False


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

    sent = [event(1, 'notification.recorded', '2026-09-18T10:00:00+00:00',
                  service=SERVICE, source='briefing', message_id='report-1')]
    lost = card(sent, notifications=[{"service": SERVICE, "message_id": "report-1",
                                      "comment": "Докладываю, пожар в квартире"}])
    facts = verdict(review(lost, expectation), "briefing_facts")
    assert facts["passed"] is False and "Улица" in facts["detail"]

    full = card(sent, notifications=[{"service": SERVICE, "message_id": "report-1",
                                      "comment": "Берзарина, дом 21, пожар в квартире"}])
    assert verdict(review(full, expectation), "briefing_facts")["passed"] is True
    manual = card([], notifications=full['notifications'])
    assert verdict(review(manual, expectation), 'briefing')['passed'] is False


def test_scenario_facts_are_checked_in_brief_and_result():
    events = [event(1, 'notification.recorded', '2026-09-18T10:00:00+00:00',
                    service=SERVICE, source='briefing', message_id='report-1'),
              event(2, 'service.updated', '2026-09-18T10:10:00+00:00',
                    service=SERVICE, status='Работы завершены', comment='Работы закончены')]
    value = card(events, notifications=[{'service': SERVICE, 'message_id': 'report-1',
                                         'comment': 'Берзарина, дом 210, пожар в квартире'}])
    expectation = {'should_accept': True, 'brief_service': SERVICE,
                   'brief_keywords': ['пострадавший'], 'result_keywords': ['ликвидировано']}
    result = review(value, expectation)
    assert verdict(result, 'briefing_facts')['passed'] is False
    assert verdict(result, 'result')['passed'] is False


def test_edited_card_cannot_rewrite_briefing_reference():
    sent = [event(1, 'notification.recorded', '2026-09-18T10:00:00+00:00',
                  service=SERVICE, source='briefing', message_id='report-2')]
    value = card(sent, notifications=[{'service': SERVICE, 'message_id': 'report-2',
                                      'comment': 'Берзарина, дом 99, пожар в квартире'}])
    value['initial_card'] = dict(value['card'])
    value['card']['house'] = '99'
    assert verdict(review(value, {'should_accept': True, 'brief_service': SERVICE}), 'briefing_facts')['passed'] is False


def test_teacher_verified_correction_is_used_for_card_and_briefing():
    sent = [event(1, 'notification.recorded', '2026-09-18T10:00:00+00:00',
                  service=SERVICE, source='briefing', message_id='report-3')]
    value = card(sent, notifications=[{'service': SERVICE, 'message_id': 'report-3',
                                      'comment': 'Берзарина, дом 22, пожар в квартире'}])
    expectation = {'should_accept': True, 'brief_service': SERVICE,
                   'expected_corrections': {'house': '22'}}
    # ДДС не правит карточку 112, а сообщает об ошибке в 112.
    assert 'Ошибка не передана' in verdict(review(value, expectation), 'correction:house')['detail']
    value['error_reports'] = [{'field': 'house', 'correct_value': '15', 'source': 'Старший бригады'},
                              {'field': 'house', 'correct_value': '22 или 23', 'source': 'Старший бригады'}]
    # Неверное значение и догадка с альтернативой не засчитываются.
    assert verdict(review(value, expectation), 'correction:house')['passed'] is False
    value['error_reports'].append({'field': 'house', 'correct_value': '22', 'source': 'Старший бригады'})
    result = review(value, expectation)
    assert verdict(result, 'correction:house')['passed'] is True
    assert verdict(result, 'briefing_facts')['passed'] is True


def test_crew_choice_and_leadership_are_assessed():
    value = card([
        event(1, 'service.updated', '2026-09-18T10:00:00+00:00',
              service=SERVICE, status='Принята'),
        event(2, 'crew.assigned', '2026-09-18T10:01:00+00:00',
              id='Наряд 17'),
        event(3, 'service.updated', '2026-09-18T10:02:00+00:00',
              service=SERVICE, status='Начало реагирования')])
    value['crew_options'] = [{'id': 'Наряд 17'}]
    value['assigned_crew'] = {'id': 'Наряд 17', 'decision_by': 'leadership',
                              'decision_note': 'Руководитель смены разрешил выезд'}
    result = review(value, {'should_accept': True, 'expected_crew_id': 'Наряд 17',
                            'leadership_decision_required': True})
    assert all(verdict(result, key)['passed'] for key in
               ('crew_assignment', 'crew_choice', 'leadership_decision'))


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


def test_card_is_worked_out_only_when_every_cycle_status_is_set():
    """Карточка отработана, когда все статусы активированы."""
    from dds_review import unfinished
    at = '2026-09-18T10:00:00+00:00'
    partial = card([event(1, 'service.updated', at, service=SERVICE, status='Принята', comment='Принято'),
                    event(2, 'service.updated', at, service=SERVICE, status='Работы завершены',
                          comment='Пожар ликвидирован')])
    partial['processed_at'] = at
    assert unfinished(partial, {'should_accept': True}) == [
        'статусы цикла: Начало реагирования, Прибытие, Проведение работ']
    refused = card([event(1, 'service.updated', at, service=SERVICE, status='Принята', comment='Принято'),
                    event(2, 'service.updated', at, service=SERVICE, status='Отказ от выполнения работ',
                          comment='Работы выполнит другая служба')])
    refused['processed_at'] = at
    assert unfinished(refused, {'should_accept': True}) == []


def test_briefing_before_correction_accepts_card_value():
    from briefing import check, reference_card
    value = {'card': {'street': 'Берзарина', 'house': '20', 'incident_type': 'Пожар в квартире'}}
    expectation = {'expected_corrections': {'house': '22'}, 'brief_required_fields': ['street', 'house']}
    card = reference_card(value, expectation)
    # До прибытия бригады диспетчер знает дом из карточки 112, после — уточнённый.
    assert check('Берзарина, дом 20', card)['complete']
    assert check('Берзарина, дом 22', card)['complete']
    assert not check('Берзарина, дом 21', card)['complete']


def test_correction_opens_only_with_its_report_and_then_card_value_stops_counting():
    from briefing import check, reference_card
    from field_dialogue import report_context
    from workspace import correction_evidence_visible
    expectation = {'expected_corrections': {'house': '22'}, 'correction_update_id': 'arrived',
                   'brief_required_fields': ['street', 'house']}
    value = {'exercise_mode': 'actions', 'planned_unlocks': {'dispatched': 'Начало реагирования', 'arrived': 'Прибытие'},
             'card': {'street': 'Берзарина', 'house': '20'}, 'dds_expectation': expectation, 'events': [
                 {'type': 'situation.update', 'at': '2026-09-18T10:01:00+00:00',
                  'detail': {'id': 'dispatched', 'unlocks_status': 'Начало реагирования'}}]}
    # Доклад о выезде не открывает исправление, которое прозвучит только при прибытии.
    assert correction_evidence_visible(value) is False
    assert report_context(value, 'Бригада', 'Выехали', update_id='dispatched')['card']['house'] == '20'
    assert report_context(value, 'Бригада', 'Прибыли', update_id='arrived')['card']['house'] == '22'
    assert check('Берзарина, дом 20', reference_card(value))['complete']
    value['events'].append({'type': 'situation.update', 'at': '2026-09-18T10:03:00+00:00',
                            'detail': {'id': 'arrived', 'unlocks_status': 'Прибытие'}})
    assert correction_evidence_visible(value) is True
    # После уточнения неверный дом из карточки больше не засчитывается,
    # а доклад, сделанный до прибытия, оценивается по тому, что было известно.
    assert not check('Берзарина, дом 20', reference_card(value))['complete']
    assert check('Берзарина, дом 22', reference_card(value))['complete']
    assert check('Берзарина, дом 20', reference_card(value, at='2026-09-18T10:02:00+00:00'))['complete']


def test_negated_correct_value_is_not_a_correction():
    sent = [event(1, 'notification.recorded', '2026-09-18T10:00:00+00:00',
                  service=SERVICE, source='briefing', message_id='report-3')]
    value = card(sent, notifications=[{'service': SERVICE, 'message_id': 'report-3',
                                      'comment': 'Берзарина, дом 22, пожар в квартире'}])
    expectation = {'should_accept': True, 'brief_service': SERVICE, 'expected_corrections': {'house': '22'}}
    value['error_reports'] = [{'field': 'house', 'correct_value': 'не 22, а 23', 'source': 'Старший бригады'}]
    assert verdict(review(value, expectation), 'correction:house')['passed'] is False
    value['error_reports'] = [{'field': 'house', 'correct_value': 'дом 22', 'source': 'Старший бригады'}]
    assert verdict(review(value, expectation), 'correction:house')['passed'] is True
