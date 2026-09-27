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
    for keys, alternatives in groups:
        if expected & keys:
            present = [word for word in alternatives if lemma(word) in {lemma(w) for w in tokens(text)}]
            return bool(present) and all(asserted(text, word) for word in present)
    # No category synonym dictionary: require all substantive terms, not any word.
    content = [w for w in tokens(incident) if len(w) > 3]
    return bool(content) and all(asserted(text, w) for w in content)
