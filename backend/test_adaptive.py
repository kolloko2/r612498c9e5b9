from adaptive import attempt_view, outcome, recommend


def attempt(score=90, within=True, response=True, critical=0, status='Завершена'):
    return {'status': status, 'score_percent': score,
            'timing': {'within_limit': within, 'response_within_limit': response},
            'grammar': {'critical_errors': critical}}


def test_outcome_classification():
    assert outcome(attempt()) == 'strong'
    assert outcome(attempt(score=65)) == 'neutral'
    assert outcome(attempt(score=30)) == 'weak'
    # Норматив важнее балла: просрочка делает попытку слабой.
    assert outcome(attempt(score=95, within=False)) == 'weak'
    assert outcome(attempt(score=95, response=False)) == 'weak'
    # Опечатка в адресе тоже переводит попытку в слабые.
    assert outcome(attempt(score=95, critical=1)) == 'weak'


def test_no_change_without_enough_attempts():
    result = recommend('basic', [attempt()])
    assert result['changed'] is False and result['level'] == 'basic'
    assert 'Недостаточно' in result['reason']


def test_promotion_after_two_strong_attempts():
    result = recommend('basic', [attempt(), attempt()])
    assert result['changed'] is True and result['level'] == 'standard'
    assert result['previous'] == 'basic'

    top = recommend('advanced', [attempt(), attempt()])
    assert top['changed'] is False and top['level'] == 'advanced'


def test_demotion_after_two_weak_attempts():
    result = recommend('standard', [attempt(score=20), attempt(within=False)])
    assert result['changed'] is True and result['level'] == 'basic'

    bottom = recommend('basic', [attempt(score=20), attempt(score=20)])
    assert bottom['changed'] is False and bottom['level'] == 'basic'


def test_mixed_results_keep_the_level():
    result = recommend('standard', [attempt(), attempt(score=20)])
    assert result['changed'] is False and result['level'] == 'standard'


def test_unfinished_attempts_are_ignored():
    unfinished = attempt(status='В работе')
    assert recommend('basic', [unfinished, unfinished])['changed'] is False


def test_attempts_without_rubric_use_norms_only():
    no_score = {'status': 'Завершена', 'score_percent': None,
                'timing': {'within_limit': False, 'response_within_limit': True},
                'grammar': {'critical_errors': 0}}
    assert outcome(no_score) == 'weak'
    assert recommend('standard', [no_score, no_score])['level'] == 'basic'


def test_attempt_view_extracts_only_measured_fields():
    card = {'status': 'Завершена', 'difficulty': 'standard',
            'evaluation': {'score_percent': 75, 'timing': {'within_limit': True}},
            'grammar': {'errors': 2, 'critical_errors': 0}, 'card': {'secret': 'не нужно'}}
    view = attempt_view(card)
    assert view == {'status': 'Завершена', 'difficulty': 'standard', 'score_percent': 75,
                    'timing': {'within_limit': True}, 'grammar': {'errors': 2, 'critical_errors': 0}}
