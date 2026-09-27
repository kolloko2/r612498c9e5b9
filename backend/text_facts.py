"""Local Russian word forms and assertion polarity for training facts.

Conservative rules, not an unrestricted semantic judge. Never send text outside
the classroom and never silently reinterpret an ambiguous fact as confirmed.
"""
import re
from functools import lru_cache
import pymorphy3

@lru_cache(maxsize=1)
def morphology():
    return pymorphy3.MorphAnalyzer()

@lru_cache(maxsize=20000)
def lemma(word):
    word = word.casefold().replace('ё', 'е')
    return morphology().parse(word)[0].normal_form.replace('ё', 'е')

@lru_cache(maxsize=20000)
def word_forms(word):
    return {p.normal_form.replace('ё','е') for p in morphology().parse(word.casefold().replace('ё','е'))}

def tokens(text):
    return re.findall(r'[а-яёa-z0-9]+', (text or '').casefold())

def asserted(text, phrase):
    """A matching phrase must have the same local negation as its reference."""
    expected_tokens = tokens(phrase)
    expected = [lemma(w) for w in expected_tokens]
    if not expected:
        return False
    negative = 'не' in expected or 'нет' in expected or 'без' in expected
    core = [w for w in expected_tokens if w not in {'не', 'нет', 'без'}]
    if not core:
        return False
    matches = []
    for clause in re.split(r'[.!?;\n]|\b(?:но|однако)\b', text.casefold()):
        raw_words = tokens(clause)
        words = [lemma(w) for w in raw_words]
        for i in range(len(words)-len(core)+1):
            if not all(word_forms(left) & word_forms(right) for left,right in zip(raw_words[i:i+len(core)], core)):
                continue
            before, after = words[max(0, i-3):i], words[i+len(core):i+len(core)+3]
            negated = any(w in {'не', 'нет', 'без', 'отсутствовать', 'отсутствие'} for w in before)
            negated |= bool(after and after[0] in {'не', 'нет', 'отсутствовать'})
            negated |= after[:2] in [['не', 'подтвердить'], ['не', 'подтвержденный']]
            matches.append(negated == negative)
    # Conflicting assertions need clarification, even if one sentence matches.
    return bool(matches) and all(matches)

def incident_asserted(text, incident):
    # The canonical classifier contains negative states such as «Без сознания».
    # Checking «сознания» on its own reverses the expected meaning.
    if tokens(incident)[:1] in (['без'], ['не'], ['нет']):
        return asserted(text, incident)
    groups = [({'пожар', 'загорание', 'возгорание', 'горение'}, ['пожар', 'возгорание', 'горит']),
              ({'дтп', 'столкновение'}, ['дтп', 'столкновение']),
              ({'задымление', 'дым'}, ['задымление', 'дым'])]
    expected = {lemma(w) for w in tokens(incident)}
    if expected <= {'повреждение', 'труба', 'водоснабжение'} and {'повреждение', 'труба'} <= expected:
        # A bounded alternative for the water-pipe exercise, not a generic
        # equivalence between every utility incident and any mention of water.
        leak = any(asserted(text, phrase) for phrase in (
            'вода вытекает из трубы', 'вода течёт из трубы', 'течёт вода из трубы',
            'прорвало водопроводную трубу', 'прорыв трубы водоснабжения'))
        if leak:
            return True
    for keys, alternatives in groups:
        if expected & keys:
            present = [word for word in alternatives if lemma(word) in {lemma(w) for w in tokens(text)}]
            return bool(present) and all(asserted(text, word) for word in present)
    # No category synonym dictionary: require all substantive terms, not any word.
    content = [w for w in tokens(incident) if len(w) > 3]
    return bool(content) and all(asserted(text, w) for w in content)


# Распознавание речи пишет числа словами («дом двенадцать»), карточка — цифрами.
_UNITS = {'ноль': 0, 'один': 1, 'два': 2, 'три': 3, 'четыре': 4, 'пять': 5, 'шесть': 6,
          'семь': 7, 'восемь': 8, 'девять': 9}
_TEENS = {'десять': 10, 'одиннадцать': 11, 'двенадцать': 12, 'тринадцать': 13, 'четырнадцать': 14,
          'пятнадцать': 15, 'шестнадцать': 16, 'семнадцать': 17, 'восемнадцать': 18, 'девятнадцать': 19}
_TENS = {'двадцать': 20, 'тридцать': 30, 'сорок': 40, 'пятьдесят': 50, 'шестьдесят': 60,
         'семьдесят': 70, 'восемьдесят': 80, 'девяносто': 90}
_HUNDREDS = {'сто': 100, 'двести': 200, 'триста': 300, 'четыреста': 400, 'пятьсот': 500,
             'шестьсот': 600, 'семьсот': 700, 'восемьсот': 800, 'девятьсот': 900}
_ORDINALS = {'первый': 1, 'второй': 2, 'третий': 3, 'четвёртый': 4, 'четвертый': 4, 'пятый': 5,
             'шестой': 6, 'седьмой': 7, 'восьмой': 8, 'девятый': 9, 'десятый': 10}


def _number_value(word):
    base = lemma(word)
    for table, rank in ((_HUNDREDS, 3), (_TENS, 2), (_TEENS, 1), (_UNITS, 0)):
        if base in table:
            return table[base], rank
    # «двести»/«сорок» и родительный падеж иногда не приводятся к норме.
    for table, rank in ((_HUNDREDS, 3), (_TENS, 2), (_TEENS, 1)):
        for key, value in table.items():
            if word.startswith(key[:-1]) and len(word) <= len(key) + 2:
                return value, rank
    return None


def spoken_numbers_to_digits(text):
    """«дом двенадцать» -> «дом 12», «сто двадцать три» -> «123». Порядок разрядов строгий."""
    # Слова отделяются и от пробелов, и от знаков препинания: «двенадцать,» и
    # «тринадцать.» тоже числа.
    words = re.findall(r'[А-Яа-яЁё]+|\s+|[^А-Яа-яЁё\s]+', text or '')
    out, total, rank = [], None, 9
    def flush():
        nonlocal total, rank
        if total is not None:
            out.append(str(total))
        total, rank = None, 9
    for part in words:
        if not part or part.isspace():
            if total is None:
                out.append(part)
            continue
        found = _number_value(part.casefold().replace('ё', 'е')) if re.fullmatch(r'[а-яё]+', part.casefold()) else None
        if found and (total is None or found[1] < rank and not (rank == 1 or (rank == 2 and found[1] == 1))):
            total = (total or 0) + found[0]
            rank = found[1]
            continue
        if total is not None:
            flush()
            if part[0].isalpha():
                out.append(' ')
        if found:
            total, rank = found
            continue
        out.append(part)
    flush()
    return re.sub(r' {2,}', ' ', ''.join(out)).strip()
