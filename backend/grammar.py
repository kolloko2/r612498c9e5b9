"""Детерминированная проверка ручного ввода карточки.

Заказчик на Q&A описал проверку грамматики как оценку того, «насколько грамотно
оператор заносит сведения», и отдельно выделил опечатки в адресах: реальный
случай — Дубнинская против Дубининской, из-за чего высылка ушла не туда.

Поэтому проверка делает две вещи и обе без модели:

1. Сверяет поля с эталоном: близкое написание — расхождение, а не доказательство
   опечатки. Адрес не исправляется автоматически.
2. Ищет механические дефекты набора.
3. Даёт отдельные словарные и морфологические подсказки. Неизвестные словарю
   названия и неоднозначные формы не снижают балл автоматически.
"""

from __future__ import annotations

import re
from typing import Any
from text_facts import morphology

# Поля, опечатка в которых уводит силы не по адресу.
ADDRESS_FIELDS = ('region', 'city', 'district', 'area', 'street', 'house',
                  'building', 'structure', 'apartment', 'entrance', 'object')
TEXT_FIELDS = ADDRESS_FIELDS + ('caller_name', 'description', 'incident_type', 'address_note')
MAX_TYPO_DISTANCE = 2
LOOKALIKE = re.compile(r'\b(?=\w*[а-яё])(?=\w*[a-z])\w+\b', re.I)
TRIPLED = re.compile(r'([а-яё])\1{2,}', re.I)
SPACE_BEFORE_PUNCTUATION = re.compile(r'\s+[,.;:!?]')
DOUBLE_SPACE = re.compile(r'\S {2,}\S')
COMMON_TYPOS = {'проишествие': 'происшествие', 'проишествия': 'происшествия',
                'диспетчерр': 'диспетчер', 'бригадаа': 'бригада',
                'пожалуста': 'пожалуйста', 'зделано': 'сделано',
                'приехол': 'приехал', 'втечении': 'в течение',
                'незнаю': 'не знаю', 'постродавший': 'пострадавший'}
COMMON_TYPOS.update({'происшествее': 'происшествие', 'пострадавшые': 'пострадавшие',
                     'эвокуация': 'эвакуация',
                     'возгарание': 'возгорание', 'задымленее': 'задымление',
                     'територия': 'территория', 'принемать': 'принимать',
                     'сообшение': 'сообщение', 'устраненно': 'устранено'})


def syntax_suggestions(text, field):
    """Conservative local syntax hints; never automatic corrections or penalties."""
    found = []
    def add(kind, match, hint):
        found.append({'field': field, 'kind': kind, 'fragment': match, 'hint': hint})
    for match in re.finditer(r'\b([а-яё]{2,})\s+\1\b', text, re.I):
        add('repeated_word', match.group(), 'Проверьте повтор слова')
    for opening, closing in [('(', ')'), ('«', '»')]:
        if text.count(opening) != text.count(closing):
            add('unpaired_delimiter', opening + closing, 'Проверьте парность скобок или кавычек')
    for match in re.finditer(r'(?<=[а-яё])[,;:](?=[а-яё])', text, re.I):
        add('missing_space', match.group(), 'После знака препинания нужен пробел')
    for match in re.finditer(r'\b(согласно|благодаря|вопреки)\s+([а-яё]+)', text, re.I):
        parses = morphology().parse(match.group(2))
        nouns = [p for p in parses if p.tag.POS == 'NOUN' and p.score >= .05]
        if nouns and all(p.tag.case != 'datv' for p in nouns):
            add('preposition_case', match.group(), 'После этого предлога проверьте дательный падеж: согласно чему?')
    words = list(re.finditer(r'\b[а-яё]+\b', text, re.I))[:3000]
    for left, right in zip(words, words[1:]):
        if text[left.end():right.start()].strip():
            continue
        subjects = [p for p in morphology().parse(left.group()) if p.tag.POS == 'NOUN' and p.tag.case == 'nomn' and p.score >= .05]
        verbs = [p for p in morphology().parse(right.group()) if p.tag.POS == 'VERB' and p.score >= .05]
        if not subjects or not verbs:
            continue
        compatible = any(a.tag.number == b.tag.number and
                         (b.tag.tense != 'past' or b.tag.number == 'plur' or a.tag.gender == b.tag.gender)
                         for a in subjects for b in verbs)
        if not compatible:
            add('subject_verb_agreement', text[left.start():right.end()],
                'Проверьте согласование подлежащего и сказуемого по числу и роду')
    return found[:100]


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
                        'distance': gap, 'critical': field in ADDRESS_FIELDS,
                        'kind':'reference_mismatch', 'hint':'Расхождение с эталоном; проверьте исходную вводную, это может быть другое название'}
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
        if field in ('description', 'address_note'):
            for match in re.finditer(r'\b[а-яё]+\b', value, re.I):
                replacement = COMMON_TYPOS.get(match.group().casefold())
                if replacement:
                    found.append({'field': field, 'kind': 'common_typo', 'fragment': match.group(),
                                  'hint': 'Проверьте написание: ' + replacement})
    return found


def language_suggestions(text, field):
    words = list(re.finditer(r'\b[а-яё]+\b', text or '', re.I))
    result = []
    morph = morphology()
    for match in words[:3000]:
        word = match.group()
        if len(word) > 3 and word.islower() and not morph.word_is_known(word):
            result.append({'field':field, 'kind':'dictionary', 'fragment':word,
                           'hint':'Слово не найдено в локальном словаре: проверьте написание или специальное название'})
    for left, right in zip(words, words[1:]):
        if text[left.end():right.start()].strip():
            continue
        a, b = morph.parse(left.group())[0], morph.parse(right.group())[0]
        if a.score < .7 or b.score < .7 or a.tag.POS != 'ADJF' or b.tag.POS != 'NOUN':
            continue
        if a.tag.case != b.tag.case:
            continue
        mismatch = a.tag.number != b.tag.number or (a.tag.number == b.tag.number == 'sing' and a.tag.gender != b.tag.gender)
        if mismatch:
            result.append({'field':field, 'kind':'agreement', 'fragment':text[left.start():right.end()],
                           'hint':'Проверьте согласование прилагательного и существительного'})
    return (result + syntax_suggestions(text or '', field))[:100]


def analyze(card: dict[str, Any], rubric: dict[str, Any] | None = None,
            comments: list[str] | None = None) -> dict[str, Any]:
    """Сводка по ручному вводу одной карточки."""
    if not isinstance(card, dict):
        raise ValueError('card must be an object')
    found_typos, found_mechanical = typos(card, rubric), mechanical(card)
    for index, comment in enumerate(comments or []):
        for issue in mechanical({'description': comment}):
            found_mechanical.append({**issue, 'field': f'service_comment:{index + 1}'})
    suggestions = [issue for field in ('description', 'address_note') for issue in language_suggestions(card.get(field, ''), field)]
    for index, comment in enumerate(comments or []):
        suggestions.extend(language_suggestions(comment, f'service_comment:{index+1}'))
    critical = [item for item in found_typos if item['critical']]
    return {
        'version': 'grammar-v3',
        'suggestions': suggestions,
        'typos': found_typos,
        'mechanical': found_mechanical,
        'errors': len(found_typos) + len(found_mechanical),
        'critical_errors': len(critical),
        'critical_fields': sorted({item['field'] for item in critical}),
        'limitations': [
            'Проверка сравнивает ввод с эталоном преподавателя и ищет дефекты набора; '
            'словарные и морфологические подсказки не снижают балл автоматически.',
            'Отсутствие найденных ошибок не подтверждает правильность сведений по существу.',
        ],
    }
