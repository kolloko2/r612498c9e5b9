import asyncio
import json

import pytest

import llm
import report_facts
from briefing import check, check_live
from text_facts import asserted

CARD = {'city': 'Москва', 'street': 'Профсоюзная', 'house': '12', 'incident_type': 'Повреждение трубы водоснабжения',
        '_brief_required_fields': ['city', 'street', 'house', 'incident_type']}


@pytest.mark.parametrize('text,phrase,expected', [
    # Распознанная речь без знаков препинания: «нет» относится к «пострадавших».
    ('вода вытекает во дворе пострадавших нет москва учебная дом двенадцать', 'москва', True),
    ('не было пострадавших москва', 'москва', True),
    ('пострадавших нет', 'пострадавшие', False),
    ('нет пострадавших', 'пострадавшие', False),
    ('нет доступа в квартиру', 'доступ', False),
    ('возгорание не обнаружено', 'возгорание', False),
])
def test_negation_does_not_leak_into_the_next_fact(text, phrase, expected):
    assert asserted(text, phrase) is expected


def empty():
    return {field: {'value': '', 'quote': ''} for field in report_facts.FIELDS}


def model(monkeypatch, fields):
    monkeypatch.setattr(llm, 'phone_configuration', lambda: {'provider': 'ollama', 'configured': True, 'model': 'm'})

    async def complete(messages, **kwargs):
        return json.dumps(fields, ensure_ascii=False)
    monkeypatch.setattr(llm, 'complete', complete)


def test_model_reading_credits_only_literal_matching_facts(monkeypatch):
    card = {**CARD, 'street': 'Маршала Жукова'}
    # Порядок слов переставлен: правила улицу не находят, модель понимает.
    spoken = 'москва жукова маршала дом двенадцать прорвало трубу'
    assert 'Улица или ориентир' in check(spoken, card, approximate=False)['missing']
    # Без модели улица засчитывается приблизительно, с пометкой.
    assert next(i for i in check(spoken, card)['checks'] if i['id'] == 'street')['granted_by'] == 'approximate'
    model(monkeypatch, {**empty(), 'street': {'value': 'Маршала Жукова', 'quote': 'жукова маршала'}})
    evidence = asyncio.run(report_facts.extract(spoken, timeout=5))
    report = check(spoken, card, None, evidence)
    street = next(item for item in report['checks'] if item['id'] == 'street')
    assert street['passed'] and street['granted_by'] == 'model' and street['quote'] == 'жукова маршала'
    # Цитата, которой нет в речи, не засчитывается.
    model(monkeypatch, {**empty(), 'street': {'value': 'Маршала Жукова', 'quote': 'проспект маршала жукова'}})
    evidence = asyncio.run(report_facts.extract(spoken, timeout=5))
    # Прочтение модели есть — приблизительное правило не подменяет её решение.
    assert 'Улица или ориентир' in check(spoken, card, None, evidence)['missing']
    # Модель назвала «Москва», а ученик сказал «Химки»: цитата не подтверждает.
    assert report_facts.credited('city', 'Москва', {'value': 'Москва', 'quote': 'химки'}, 'химки жукова маршала') is None


def test_model_cannot_override_a_named_wrong_house(monkeypatch):
    spoken = 'москва профсоюзная дом тринадцать прорвало трубу'
    model(monkeypatch, {**empty(), 'house': {'value': '12', 'quote': 'дом тринадцать'}})
    evidence = asyncio.run(report_facts.extract(spoken, timeout=5))
    assert 'Дом' in check(spoken, CARD, None, evidence)['missing']
    hedged = 'москва профсоюзная дом десять или двенадцать прорвало трубу'
    model(monkeypatch, {**empty(), 'house': {'value': '12', 'quote': 'десять или двенадцать'}})
    evidence = asyncio.run(report_facts.extract(hedged, timeout=5))
    assert 'Дом' in check(hedged, CARD, None, evidence)['missing']


def test_live_check_asks_model_once_and_evidence_is_bound_to_text(monkeypatch):
    calls = []

    async def complete(messages, **kwargs):
        calls.append(1)
        return json.dumps({**empty(), 'street': {'value': 'Маршала Жукова', 'quote': 'жукова маршала'}}, ensure_ascii=False)
    monkeypatch.setattr(llm, 'phone_configuration', lambda: {'provider': 'ollama', 'configured': True, 'model': 'm'})
    monkeypatch.setattr(llm, 'complete', complete)
    spoken = 'москва жукова маршала дом двенадцать прорвало трубу'
    report = asyncio.run(check_live(spoken, {**CARD, 'street': 'Маршала Жукова'}))
    assert report['field_evidence']['fingerprint'] == report_facts.fingerprint(spoken)
    assert next(item for item in report['checks'] if item['id'] == 'street')['granted_by'] == 'model'
    # Для нового текста прежнее прочтение недействительно.
    assert not report_facts.valid(report['field_evidence'], spoken + ' уточняю дом тринадцать')


DEPOT = {'city': 'Москва', 'object': 'Депо около станции Москва-Пассажирская Киевская', 'incident_type': 'пожар: мусор',
         '_brief_required_fields': ['city', 'object', 'incident_type']}


@pytest.mark.parametrize('text,named', [
    # Распознанная речь искажает слова длинного ориентира, но объект опознаётся.
    ('возгорание москва дпо около станции пассажирско киетская', True),
    ('пожар москва дп около станции москва пассажирско киевская длинное помещение', True),
    ('пожар в депо у станции москва пассажирская киевская', True),
    # Общее слово или одно название объект не опознают.
    ('пожар на станции в москве', False),
    ('пожар депо на киевской москва', False),
])
def test_long_object_name_survives_speech_recognition(text, named):
    assert ('Объект' not in check(text, DEPOT)['missing']) is named


STT = 'по поводу возгорания москва дпо около станции пассажирско киетская длинное помещение'


def verdict(match, quote='дпо около станции пассажирско киетская'):
    return {**empty(), 'object': {'value': 'депо около станции Москва-Пассажирская Киевская',
                                  'quote': quote, 'match': match}}


def test_without_model_object_is_credited_approximately():
    item = next(i for i in check(STT, DEPOT)['checks'] if i['id'] == 'object')
    assert item['passed'] and item['granted_by'] == 'approximate'


def test_model_decides_whether_distorted_object_matches(monkeypatch):
    seen = []

    async def complete(messages, **kwargs):
        seen.append(json.loads(messages[-1]['content']))
        return json.dumps(verdict('yes'), ensure_ascii=False)
    monkeypatch.setattr(llm, 'phone_configuration', lambda: {'provider': 'ollama', 'configured': True, 'model': 'm'})
    monkeypatch.setattr(llm, 'complete', complete)
    report = asyncio.run(check_live(STT, DEPOT))
    item = next(i for i in report['checks'] if i['id'] == 'object')
    assert item['passed'] and item['granted_by'] == 'model' and 'киетская' in item['quote']
    # Модель сравнивает с карточкой, которую ей показали.
    assert seen[0]['карточка']['object'] == [DEPOT['object']]
    # Оценка повторяет решение по сохранённому прочтению, без модели.
    assert 'Объект' not in check(STT, DEPOT, None, report['field_evidence'])['missing']


@pytest.mark.parametrize('reading', [
    verdict('partial', 'около станции'),
    verdict('no'),
    # «Да» без опознавательного слова в цитате не засчитывается.
    verdict('yes', 'около станции'),
    # Цитаты нет в речи.
    verdict('yes', 'депо станции москва пассажирская киевская'),
])
def test_model_verdict_is_final_and_guarded(monkeypatch, reading):
    model(monkeypatch, reading)
    report = asyncio.run(check_live(STT, DEPOT))
    # Правило 2/3 засчитало бы, но при работающей модели решает она.
    assert 'Объект' in report['missing']
    assert 'Объект' in check(STT, DEPOT, None, report['field_evidence'])['missing']


def test_model_failure_falls_back_to_approximate_rule(monkeypatch):
    monkeypatch.setattr(llm, 'phone_configuration', lambda: {'provider': 'ollama', 'configured': True, 'model': 'm'})

    async def broken(messages, **kwargs):
        raise TimeoutError
    monkeypatch.setattr(llm, 'complete', broken)
    item = next(i for i in asyncio.run(check_live(STT, DEPOT))['checks'] if i['id'] == 'object')
    assert item['passed'] and item['granted_by'] == 'approximate'
