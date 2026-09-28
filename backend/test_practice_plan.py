import copy
import pytest
from fastapi import HTTPException
from practice_plan import draft, fingerprint, approved, hint, require_approved, validate_plan


def plan():
    s = {'title': 'Учебный случай', 'updates': [{'id': 'arrived', 'text': 'Дом 13, а не 12', 'unlocks_status': 'Прибытие'}]}
    s['practice_plan'] = draft(s)
    s['practice_approved_version'] = fingerprint(s)
    return s


def test_approval_bound_to_entire_scenario_and_plan():
    s = plan()
    assert approved(s)
    frozen = copy.deepcopy(s)
    s['updates'][0]['text'] = 'Изменённый доклад'
    assert not approved(s) and approved(frozen)
    with pytest.raises(HTTPException): require_approved(s)
    s = plan(); s['practice_plan'][0]['text'] = 'Изменённая подсказка'
    assert not approved(s)


def test_only_delivered_update_can_supply_hint():
    s = plan()
    value = dict(practice_with_hints=True, exercise_mode='actions', owner_service='ДДС',
                 service_states={'ДДС': {'status': 'Принята'}}, events=[
                     {'seq': 1, 'type': 'notification.recorded', 'detail': {'source': 'briefing', 'counterpart': 'superior'}}])
    assert hint(s, value)['phase'] == 'wait'
    assert 'Дом 13' not in str(hint(s, value))
    value['events'].append({'seq': 2, 'type': 'situation.update', 'detail': {'id': 'arrived', 'unlocks_status': 'Прибытие'}})
    assert hint(s, value)['update_id'] == 'arrived'
    assert 'Дом 13' in hint(s, value)['text']
    value['text_input_allowed'] = False
    voice_hint = hint(s, value)
    assert voice_hint['phase'] == 'update'
    assert 'Дом 13' not in str(voice_hint)
    assert 'Прибытие' not in str(voice_hint['text'])
    value['events'].append({'seq': 3, 'type': 'service.updated', 'detail': {'service': 'ДДС', 'status': 'Прибытие'}})
    assert hint(s, value)['phase'] == 'wait'
    value['practice_with_hints'] = False
    assert hint(s, value) is None
    value['practice_with_hints'] = True; s['practice_approved_version'] = ''
    assert hint(s, value) is None


def test_refusal_and_plan_validation():
    s = {'dds_expectation': {'should_accept': False}}
    steps = draft(s)
    assert 'Не принята' in steps[0]['text']
    s['practice_plan'] = steps + [steps[0]]
    with pytest.raises(ValueError): validate_plan(s)
    s = plan(); s['practice_plan'][6]['update_id'] = 'unknown'
    with pytest.raises(ValueError): validate_plan(s)
