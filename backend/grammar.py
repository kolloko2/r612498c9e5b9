"""Детерминированная проверка ручного ввода карточки.

Заказчик на Q&A описал проверку грамматики как оценку того, «насколько грамотно
оператор заносит сведения», и отдельно выделил опечатки в адресах: реальный
случай — Дубнинская против Дубининской, из-за чего высылка ушла не туда.

Поэтому проверка делает две вещи и обе без модели:

1. Сверяет текстовые поля с ожидаемыми значениями эталона. Расхождение в одну-две
   буквы — это опечатка (оператор знал нужное слово и промахнулся по клавише), а
   не другое значение. Опечатка в адресном поле считается критической.
2. Ищет механические дефекты набора: смешение кириллицы и латиницы в одном слове,
   утроенные буквы, лишние пробелы перед знаками препинания.

Словарной проверки орфографии здесь нет: она требует словаря русского языка с
топонимами, а выдуманный словарь давал бы ложные ошибки на названиях улиц.
ИИ-разбор текста остаётся отдельной рекомендательной функцией и в это число не
входит.
"""

from __future__ import annotations

import re
from typing import Any

# Поля, опечатка в которых уводит силы не по адресу.
ADDRESS_FIELDS = ('region', 'city', 'district', 'area', 'street', 'house',
                  'building', 'structure', 'apartment', 'entrance', 'object')
TEXT_FIELDS = ADDRESS_FIELDS + ('caller_name', 'description', 'incident_type', 'address_note')
MAX_TYPO_DISTANCE = 2
LOOKALIKE = re.compile(r'\b(?=\w*[а-яё])(?=\w*[a-z])\w+\b', re.I)
TRIPLED = re.compile(r'([а-яё])\1{2,}', re.I)
SPACE_BEFORE_PUNCTUATION = re.compile(r'\s+[,.;:!?]')
DOUBLE_SPACE = re.compile(r'\S {2,}\S')


def distance(first: str, second: str) -> int:
    """Расстояние Левенштейна; строки коротки, поэтому без оптимизаций."""
    if first == second:
        return 0
    previous = list(range(len(second) + 1))
    for i, left in enumerate(first, start=1):
        current = [i]
        for j, right in enumerate(second, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def normalize(value: str) -> str:
    return re.sub(r'\s+', ' ', (value or '').strip().casefold().replace('ё', 'е'))


def expectations(rubric: dict[str, Any] | None) -> dict[str, list[str]]:
    """Ожидаемые значения по полям, взятые из эталона преподавателя."""
    result: dict[str, list[str]] = {}
    for criterion in (rubric or {}).get('criteria', []):
        field = criterion.get('field')
        if field in TEXT_FIELDS and criterion.get('mode') in ('equals', 'contains_all'):
            result.setdefault(field, []).extend(
                value for value in criterion.get('expected', []) if isinstance(value, str))
    return result


def typos(card: dict[str, Any], rubric: dict[str, Any] | None) -> list[dict]:
    """Близкое, но не точное совпадение с эталоном — это опечатка."""
    found = []
    for field, values in expectations(rubric).items():
        actual = card.get(field)
        if not isinstance(actual, str) or not actual.strip():
            continue
        written = normalize(actual)
        best = None
        for expected in values:
            wanted = normalize(expected)
            if not wanted:
                continue
            gap = distance(written, wanted)
            if gap == 0:
                best = None
                break
            if gap <= MAX_TYPO_DISTANCE and (best is None or gap < best['distance']):
                best = {'field': field, 'written': actual.strip(), 'expected': expected,
                        'distance': gap, 'critical': field in ADDRESS_FIELDS}
        if best:
            found.append(best)
    return found


def mechanical(card: dict[str, Any]) -> list[dict]:
    """Дефекты набора, видимые без словаря."""
    found = []
    for field in TEXT_FIELDS:
        value = card.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        for match in LOOKALIKE.finditer(value):
            found.append({'field': field, 'kind': 'mixed_alphabet', 'fragment': match.group(0),
                          'hint': 'В слове смешаны кириллица и латиница'})
        for match in TRIPLED.finditer(value):
            found.append({'field': field, 'kind': 'tripled_letter', 'fragment': match.group(0),
                          'hint': 'Буква повторяется три раза подряд'})
        if SPACE_BEFORE_PUNCTUATION.search(value):
            found.append({'field': field, 'kind': 'space_before_punctuation', 'fragment': '',
                          'hint': 'Пробел перед знаком препинания'})
        if DOUBLE_SPACE.search(value):
            found.append({'field': field, 'kind': 'double_space', 'fragment': '',
                          'hint': 'Двойной пробел внутри строки'})
    return found


def analyze(card: dict[str, Any], rubric: dict[str, Any] | None = None) -> dict[str, Any]:
    """Сводка по ручному вводу одной карточки."""
    if not isinstance(card, dict):
        raise ValueError('card must be an object')
    found_typos, found_mechanical = typos(card, rubric), mechanical(card)
    critical = [item for item in found_typos if item['critical']]
    return {
        'version': 'grammar-v1',
        'typos': found_typos,
        'mechanical': found_mechanical,
        'errors': len(found_typos) + len(found_mechanical),
        'critical_errors': len(critical),
        'critical_fields': sorted({item['field'] for item in critical}),
        'limitations': [
            'Проверка сравнивает ввод с эталоном преподавателя и ищет дефекты набора; '
            'словарная проверка орфографии не выполняется.',
            'Отсутствие найденных ошибок не подтверждает правильность сведений по существу.',
        ],
    }
