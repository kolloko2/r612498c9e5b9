"""Прогноз результата следующей попытки и проверка его достоверности.

Метод выбран объяснимым и заранее зафиксированным: двойное экспоненциальное
сглаживание (метод Хольта) по ряду баллов обучающегося. Уровень — «текущий
навык», тренд — прирост за попытку. Параметры не подгоняются под данные стенда,
поэтому проверка на данных стенда честная: модель видит только попытки до
прогнозируемой. Значения α и β выбраны на синтетических когортах (см.
synthetic_cohort) среди стандартных вариантов 0,2–0,5.

Достоверность проверяется скользящим прогнозом: для каждой попытки, начиная с
третьей, строится прогноз по предыдущим и сравнивается с фактом. Для сравнения
считаются два простых способа — «последний балл» и «средний балл». Интервал
прогноза берётся из фактических ошибок этой же проверки (80-й процентиль).
"""
from __future__ import annotations

import math
import random
from statistics import mean

ALPHA = 0.3          # вес новой попытки в уровне
BETA = 0.1           # вес нового прироста в тренде
MIN_HISTORY = 2      # прогноз строится, когда есть хотя бы две попытки
PASS_SCORE = 70.0    # порог готовности по умолчанию, если преподаватель не задал свой
INTERVAL_QUANTILE = 0.8
METHOD = {
    'name': 'Двойное экспоненциальное сглаживание (метод Хольта)',
    'alpha': ALPHA, 'beta': BETA, 'min_history': MIN_HISTORY,
    'description': ('Уровень навыка и прирост за попытку обновляются после каждой попытки. '
                    'Прогноз следующей попытки = уровень + прирост, ограниченный 0–100 баллами. '
                    'Параметры α=0,3 и β=0,1 выбраны заранее на синтетических когортах (три зерна генератора) '
                    'как лучшие из стандартных значений; по данным стенда они не подбираются. '
                    'Метод консервативен: рост учитывается после того, как подтвердится несколькими попытками.'),
}


def clip(value: float) -> float:
    return max(0.0, min(100.0, value))


def holt(scores: list[float]) -> tuple[float, float]:
    """Уровень и тренд после всех попыток ряда."""
    # Начальный прирост нулевой: при разбросе ±8 баллов разница первых двух попыток
    # чаще отражает шум, чем навык. Поэтому на ровном росте прогноз консервативен —
    # готовность не объявляется раньше, чем рост подтвердится несколькими попытками.
    level, trend = scores[0], 0.0
    for score in scores[1:]:
        previous = level
        level = ALPHA * score + (1 - ALPHA) * (level + trend)
        trend = BETA * (level - previous) + (1 - BETA) * trend
    return level, trend


def predict(scores: list[float]) -> float:
    level, trend = holt(scores)
    return clip(level + trend)


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def backtest(series: dict[str, list[float]], threshold: float = PASS_SCORE) -> dict:
    """Скользящая проверка: прогноз каждой попытки только по предыдущим."""
    rows = []
    for key, scores in series.items():
        for k in range(MIN_HISTORY, len(scores)):
            history = scores[:k]
            rows.append({'student': key, 'attempt': k + 1, 'actual': scores[k],
                         'forecast': round(predict(history), 2),
                         'last': history[-1], 'mean': round(mean(history), 2)})
    if not rows:
        return {'predictions': 0, 'students': len(series), 'rows': [],
                'note': 'Недостаточно данных: нужна хотя бы одна серия из трёх оценённых попыток.'}

    def errors(key):
        diffs = [r[key] - r['actual'] for r in rows]
        return {'mae': round(mean(abs(d) for d in diffs), 2),
                'rmse': round(math.sqrt(mean(d * d for d in diffs)), 2),
                'bias': round(mean(diffs), 2),
                'within_10': round(100 * sum(abs(d) <= 10 for d in diffs) / len(diffs), 1)}

    residuals = [abs(r['forecast'] - r['actual']) for r in rows]
    width = round(quantile(residuals, INTERVAL_QUANTILE), 2)
    covered = sum(r['forecast'] - width <= r['actual'] <= r['forecast'] + width for r in rows)
    confusion = {'true_ready': 0, 'false_ready': 0, 'true_not_ready': 0, 'false_not_ready': 0}
    for r in rows:
        predicted, actual = r['forecast'] >= threshold, r['actual'] >= threshold
        confusion[('true_' if predicted == actual else 'false_') + ('ready' if predicted else 'not_ready')] += 1
    bins = []
    for low, high in ((0, 40), (40, 60), (60, 80), (80, 101)):
        inside = [r for r in rows if low <= r['forecast'] < high]
        if inside:
            bins.append({'range': f'{low}–{min(high, 100)}', 'count': len(inside),
                         'forecast_mean': round(mean(r['forecast'] for r in inside), 1),
                         'actual_mean': round(mean(r['actual'] for r in inside), 1)})
    model, last, average = errors('forecast'), errors('last'), errors('mean')
    return {
        'predictions': len(rows), 'students': sum(len(s) > MIN_HISTORY for s in series.values()),
        'model': model, 'baseline_last': last, 'baseline_mean': average,
        'better_than_baselines': model['mae'] <= min(last['mae'], average['mae']),
        'interval_width': width, 'interval_coverage': round(100 * covered / len(rows), 1),
        'threshold': threshold, 'readiness_accuracy': round(100 * (confusion['true_ready'] + confusion['true_not_ready']) / len(rows), 1),
        'confusion': confusion, 'calibration': bins, 'rows': rows[-300:],
    }


def student_forecast(scores: list[float], width: float, threshold: float = PASS_SCORE,
                     timing_misses: int = 0, timed: int = 0, top_errors: list[str] | None = None) -> dict:
    """Прогноз следующей попытки одного обучающегося и объяснение, что на него влияет."""
    if len(scores) < MIN_HISTORY:
        return {'status': 'insufficient', 'attempts': len(scores),
                'note': f'Прогноз появится после {MIN_HISTORY} оценённых попыток.'}
    level, trend = holt(scores)
    forecast = clip(level + trend)
    low, high = clip(forecast - width), clip(forecast + width)
    readiness = 'ready' if low >= threshold else 'close' if forecast >= threshold else 'not_ready'
    factors = []
    if trend >= 1:
        factors.append(f'Результат растёт: +{trend:.1f} балла за попытку.')
    elif trend <= -1:
        factors.append(f'Результат снижается: {trend:.1f} балла за попытку.')
    else:
        factors.append('Результат стабилен: заметного роста между попытками нет.')
    if timed and timing_misses:
        factors.append(f'Нормативы времени нарушены в {timing_misses} из {timed} попыток.')
    for label in (top_errors or [])[:2]:
        factors.append(f'Частая ошибка: {label}.')
    if len(scores) < 4:
        factors.append('Попыток мало — интервал прогноза широкий.')
    to_threshold = None
    if forecast < threshold and trend > 0.5:
        to_threshold = min(20, math.ceil((threshold - forecast) / trend) + 1)
    return {'status': 'ok', 'attempts': len(scores), 'last_score': scores[-1],
            'level': round(level, 1), 'trend': round(trend, 2), 'forecast': round(forecast, 1),
            'interval': [round(low, 1), round(high, 1)], 'threshold': threshold,
            'readiness': readiness, 'attempts_to_threshold': to_threshold, 'factors': factors}


def synthetic_cohort(students: int = 12, attempts: int = 10, seed: int = 112) -> dict[str, list[float]]:
    """Синтетическая когорта для демонстрации свойств метода; в базу не пишется.

    Модель поведения: начальный навык 20–55 баллов, прирост 1–6 баллов за попытку
    с насыщением к 95, случайный разброс ±8 баллов (нормальный шум), у трети
    обучающихся рост останавливается на середине серии (плато).
    """
    rng = random.Random(seed)
    cohort = {}
    for index in range(students):
        skill, growth = rng.uniform(20, 55), rng.uniform(1, 6)
        plateau = attempts // 2 if index % 3 == 0 else None
        scores = []
        for attempt in range(attempts):
            if plateau is None or attempt < plateau:
                skill += growth * (1 - skill / 95)
            scores.append(round(clip(skill + rng.gauss(0, 8)), 1))
        cohort[f'synthetic-{index + 1:02d}'] = scores
    return cohort


SYNTHETIC_MODEL = ('Синтетическая когорта: 12 обучающихся по 10 попыток; начальный навык 20–55 баллов, '
                   'прирост 1–6 баллов за попытку с насыщением к 95, разброс ±8 баллов, у трети — плато '
                   'с середины серии. Генератор детерминирован (зерно 112) и в базу не пишет.')
