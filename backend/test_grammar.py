import pytest

from grammar import analyze, distance


def test_service_comments_are_checked_without_modification():
    comment = 'Проишествие  устранено , зделано'
    result = analyze({}, comments=[comment])
    assert result['errors'] >= 4
    assert all(issue['field'] == 'service_comment:1' for issue in result['mechanical'])
    assert comment == 'Проишествие  устранено , зделано'

RUBRIC = {'title': 'Эталон', 'time_limit_seconds': 180, 'criteria': [
    {'id': 'street', 'label': 'Улица', 'field': 'street', 'mode': 'equals',
     'expected': ['Дубнинская'], 'weight': 1},
    {'id': 'house', 'label': 'Дом', 'field': 'house', 'mode': 'equals',
     'expected': ['12'], 'weight': 1},
    {'id': 'name', 'label': 'Заявитель', 'field': 'caller_name', 'mode': 'equals',
     'expected': ['Иванова Елена Сергеевна'], 'weight': 1},
]}


def card(**changes):
    base = {'street': 'Дубнинская', 'house': '12', 'caller_name': 'Иванова Елена Сергеевна',
            'description': 'Учебное сообщение', 'incident_type': 'Пожар'}
    base.update(changes)
    return base


def test_distance_basics():
    assert distance('дубнинская', 'дубнинская') == 0
    assert distance('дубнинская', 'дубининская') == 1
    assert distance('кот', 'собака') > 2


def test_clean_card_has_no_errors():
    result = analyze(card(), RUBRIC)
    assert result['errors'] == 0 and result['critical_errors'] == 0
    assert result['typos'] == [] and result['mechanical'] == []


def test_street_typo_is_critical():
    """Дубнинская вместо Дубининской — критическая опечатка."""
    result = analyze(card(street='Дубининская'), RUBRIC)
    assert result['errors'] == 1 and result['critical_errors'] == 1
    assert result['critical_fields'] == ['street']
    typo = result['typos'][0]
    assert typo['written'] == 'Дубининская' and typo['expected'] == 'Дубнинская'
    assert typo['distance'] == 1 and typo['critical'] is True


def test_typo_outside_address_is_counted_but_not_critical():
    result = analyze(card(caller_name='Иванова Елена Сергевна'), RUBRIC)
    assert result['errors'] == 1 and result['critical_errors'] == 0


def test_completely_different_value_is_not_a_typo():
    """Другое значение — ошибка по существу, её ловит эталон, а не проверка набора."""
    result = analyze(card(street='Тверская'), RUBRIC)
    assert result['typos'] == []


def test_mechanical_defects_are_detected():
    result = analyze(card(description='Пожаррр в доме , дым  идёт', street='Лeсная'), None)
    kinds = {item['kind'] for item in result['mechanical']}
    assert 'tripled_letter' in kinds
    assert 'space_before_punctuation' in kinds
    assert 'double_space' in kinds
    # Латинская e внутри русского слова.
    assert 'mixed_alphabet' in kinds
    assert result['errors'] == len(result['mechanical'])


def test_without_rubric_only_mechanical_checks_run():
    result = analyze(card(street='Дубининская'), None)
    assert result['typos'] == [] and result['critical_errors'] == 0


def test_invalid_card_rejected():
    with pytest.raises(ValueError):
        analyze('не карточка', RUBRIC)


def test_local_syntax_hints_do_not_penalize_or_rewrite():
    text = 'Бригада прибыли. Согласно приказа работы работы завершены (результат'
    result = analyze({'description': text})
    kinds = {item['kind'] for item in result['suggestions']}
    assert {'subject_verb_agreement', 'preposition_case', 'repeated_word', 'unpaired_delimiter'} <= kinds
    assert result['errors'] == 0
    assert not any(item['kind'] == 'subject_verb_agreement' for item in
                   analyze({'description': 'Бригада прибыла. Рабочие прибыли.'})['suggestions'])
