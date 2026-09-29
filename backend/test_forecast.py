import forecast
from test_assessment import configure, finish, grading, new_card  # noqa: F401 - фикстуры
from test_rbac_integration import classroom  # noqa: F401 - фикстура


def test_holt_follows_growth_conservatively_and_is_clipped():
    # Рост учитывается, но осторожно: прогноз выше среднего и ниже прямой экстраполяции.
    assert 55 < forecast.predict([40, 50, 60, 70]) < 80
    assert forecast.predict([40, 50, 60, 70, 80, 90]) > forecast.predict([40, 50, 60, 70])
    assert forecast.predict([100, 100, 100, 100]) == 100 and forecast.predict([0, 0, 0]) == 0
    assert forecast.predict([80, 70, 60]) < 80  # снижение тоже учитывается


def test_backtest_uses_only_previous_attempts_and_compares_baselines():
    result = forecast.backtest({'a': [40, 50, 60, 70], 'b': [80, 80]}, threshold=65)
    # Серия «a» даёт два прогноза (3-я и 4-я попытки), «b» — ни одного.
    assert result['predictions'] == 2 and result['students'] == 1
    first = result['rows'][0]
    assert first['actual'] == 60 and first['last'] == 50 and first['mean'] == 45
    assert first['forecast'] == round(forecast.predict([40, 50]), 2)
    assert set(result['model']) == {'mae', 'rmse', 'bias', 'within_10'}
    assert sum(result['confusion'].values()) == 2
    assert forecast.backtest({'x': [50]})['predictions'] == 0


def test_synthetic_cohort_is_deterministic_and_model_beats_simple_baselines():
    assert forecast.synthetic_cohort() == forecast.synthetic_cohort()
    for seed in (112, 7, 2026):
        result = forecast.backtest(forecast.synthetic_cohort(seed=seed))
        assert result['model']['mae'] < result['baseline_last']['mae']
        assert result['model']['mae'] < result['baseline_mean']['mae']
        assert 70 <= result['interval_coverage'] <= 90


def test_student_forecast_readiness_and_explanation():
    ready = forecast.student_forecast([75, 82, 88, 92], width=5, threshold=70)
    assert ready['readiness'] == 'ready' and ready['interval'][0] >= 70
    growing = forecast.student_forecast([30, 40, 50], width=10, threshold=70, timing_misses=2, timed=3,
                                        top_errors=['Адрес'])
    assert growing['readiness'] == 'not_ready' and growing['attempts_to_threshold']
    assert any('Нормативы' in f for f in growing['factors']) and any('Адрес' in f for f in growing['factors'])
    assert forecast.student_forecast([50], width=10)['status'] == 'insufficient'


def test_forecast_api_scopes_and_threshold(grading):  # noqa: F811
    c = grading
    configure(c)
    client, h = c['client'], c['headers']
    for name in ('', 'Учебный', 'Учебный'):
        card = new_card(c)
        if name:
            assert client.put('/api/v1/student/sessions/' + card['id'] + '/card', headers=h['student1'],
                              json={'revision': 0, 'card': {**card['card'], 'caller_name': name}}).status_code == 200
        finish(c, card)
    teacher = client.get('/api/v1/instructor/forecast?threshold=60', headers=h['teacher1']).json()
    assert teacher['threshold'] == 60 and teacher['method']['alpha'] == forecast.ALPHA
    student = next(s for s in teacher['students'] if s['display_name'] == 'Обучающийся 1')
    assert student['scores'] == [0.0, 100.0, 100.0] and student['status'] == 'ok'
    assert teacher['validation']['real']['predictions'] == 1
    assert teacher['validation']['synthetic']['predictions'] > 50
    assert 'синтетическая' in teacher['interval_source']
    assert teacher['source']['scored'] == 3
    own = client.get('/api/v1/student/forecast', headers=h['student1']).json()
    assert 'rows' not in own['validation']['real'] and 'rows' not in own['validation']['synthetic']
    assert [s['display_name'] for s in own['students']] == ['Обучающийся 1']
    assert client.get('/api/v1/student/forecast', headers=h['student2']).json()['students'] == []
    assert client.get('/api/v1/instructor/forecast', headers=h['student1']).status_code == 403
    assert client.get('/api/v1/instructor/forecast?threshold=10', headers=h['teacher1']).status_code == 422
    stats = client.get('/api/v1/instructor/statistics', headers=h['teacher1']).json()
    assert stats['source']['completed'] == 3 and len(stats['timing']) == 2
    assert all('active_seconds' in item for item in stats['progress'])
