"""Подбор уровня сложности по результатам обучающегося.

Система смотрит, укладывается ли человек в нормативы, и сама переводит его на
более сложные или более простые задания. Решение строится на уже измеренных
попытках, поэтому оно объяснимо и воспроизводимо.

Решение принимается только по тому, что измерено детерминированно: балл по
эталону преподавателя, соблюдение нормативов реакции и обработки, число ошибок
ручного ввода. Модель в подборе уровня не участвует. Итог носит рекомендательный
характер: преподаватель отключает адаптацию или назначает задание адресно.
"""

from __future__ import annotations

from typing import Any

LEVELS = ('basic', 'standard', 'advanced')
WINDOW = 3            # сколько последних попыток учитывается
STREAK = 2            # сколько подряд нужно для перевода
STRONG_SCORE = 80.0   # балл, при котором попытка считается уверенной
WEAK_SCORE = 50.0


def _level_index(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 0


def outcome(attempt: dict[str, Any]) -> str:
    """Оценка одной попытки: уверенная, слабая или обычная."""
    score = attempt.get('score_percent')
    timing = attempt.get('timing') or {}
    within = timing.get('within_limit')
    response_within = timing.get('response_within_limit')
    critical = (attempt.get('grammar') or {}).get('critical_errors', 0) or attempt.get('dds_critical', 0)

    missed_norm = within is False or response_within is False
    if score is None:
        # Без настроенного эталона судим только по нормативам и адресным опечаткам.
        return 'weak' if missed_norm or critical else 'neutral'
    if score >= STRONG_SCORE and not missed_norm and not critical and attempt.get('dds_passed') is not False:
        return 'strong'
    if score < WEAK_SCORE or missed_norm or critical or attempt.get('dds_passed') is False:
        return 'weak'
    return 'neutral'


def recommend(current: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Рекомендованный уровень и человекочитаемая причина."""
    level = current if current in LEVELS else 'basic'
    recent = [attempt for attempt in history if attempt.get('status') == 'Завершена'][-WINDOW:]
    outcomes = [outcome(attempt) for attempt in recent]
    index = _level_index(level)

    if len(outcomes) < STREAK:
        return {'level': level, 'previous': level, 'changed': False,
                'reason': 'Недостаточно завершённых попыток для изменения уровня',
                'considered': len(outcomes), 'outcomes': outcomes}

    tail = outcomes[-STREAK:]
    if all(item == 'strong' for item in tail) and index < len(LEVELS) - 1:
        index += 1
        reason = f'Последние {STREAK} попытки выполнены в нормативы с высоким баллом'
    elif all(item == 'weak' for item in tail) and index > 0:
        index -= 1
        reason = f'Последние {STREAK} попытки вне нормативов или с ошибками в адресе'
    else:
        return {'level': level, 'previous': level, 'changed': False,
                'reason': 'Устойчивого изменения результатов нет', 'considered': len(outcomes),
                'outcomes': outcomes}

    return {'level': LEVELS[index], 'previous': level, 'changed': True, 'reason': reason,
            'considered': len(outcomes), 'outcomes': outcomes}


def attempt_view(card: dict[str, Any]) -> dict[str, Any]:
    """Сжатие завершённой карточки до полей, влияющих на подбор уровня."""
    evaluation = card.get('evaluation') or {}
    dds = card.get('dds_review') or {}
    result = {'status': card.get('status'), 'difficulty': card.get('difficulty', 'basic'),
            'score_percent': dds.get('score_percent') if dds else evaluation.get('score_percent'),
            'timing': evaluation.get('timing'), 'grammar': card.get('grammar')}
    if dds:
        result.update(dds_passed=dds.get('passed'), dds_critical=len(dds.get('critical_errors', [])))
    return result
