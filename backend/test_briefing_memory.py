"""Regression of the reported Учебная, 12 conversation, without live models."""
import json
from pathlib import Path

import pytest
import llm
from briefing import check, duty_reply, reference_card
from text_facts import incident_asserted


def practice():
    scenario = json.loads((Path(__file__).parents[1] / 'tools/data/dds_guided_practice.json').read_text(encoding='utf-8'))
    return reference_card({'card': scenario['prefilled_card'], 'dds_expectation': scenario['dds_expectation']})


@pytest.mark.asyncio
async def test_user_report_accepts_water_leak_and_city_without_repeated_questions(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Normal report turns must not call the model')
    monkeypatch.setattr(llm, 'reply', forbidden)
    card = practice()
    first = 'во дворе дома учебная дом двенадцать вода вытекает из трубы пострадавших нет'
    history = [{'role': 'user', 'content': first}]
    report = check(first, card)
    assert report['missing'] == ['Город']
    answer = await duty_reply(history, card, 'ЖКХ', report['missing'], transcript=first, report=report)
    assert 'город' in answer
    history += [{'role': 'assistant', 'content': answer}, {'role': 'user', 'content': 'москва'}]
    spoken = first + '. москва'
    report = check(spoken, card)
    assert report['complete']
    assert await duty_reply(history, card, 'ЖКХ', [], transcript=spoken, report=report) == 'Информация принята.'


@pytest.mark.asyncio
async def test_house_conflict_and_explicit_correction():
    card = practice()
    spoken = 'Москва улица Учебная дом двенадцать повреждение трубы водоснабжения. Дом тринадцать'
    report = check(spoken, card)
    assert report['missing'] == ['Дом']
    answer = await duty_reply([], card, 'ЖКХ', report['missing'], transcript=spoken, report=report)
    assert '12, 13' in answer and 'район' not in answer
    assert not check(spoken + '. Уточняю, дом 13', card)['complete']
    assert check(spoken + '. Уточняю, дом 12', card)['complete']
    assert not check(spoken + '. Уточняю, дом 12 или 13', card)['complete']


@pytest.mark.asyncio
async def test_full_fact_memory_and_model_cannot_reopen_city_or_demand_district(monkeypatch):
    captured = []
    async def bad_model(messages, **kwargs):
        captured.extend(messages)
        return 'В каком районе Москвы произошло происшествие?'
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider': 'ollama', 'configured': True})
    monkeypatch.setattr(llm, 'reply', bad_model)
    card = practice()
    spoken = 'Москва Учебная дом 12 повреждение трубы водоснабжения'
    report = check(spoken, card)
    history = [{'role': 'user', 'content': 'Можете уточнить доступ во двор?'}]
    answer = await duty_reply(history, card, 'ЖКХ', [], transcript=spoken, report=report)
    assert answer == 'Информация принята.'
    context = json.loads(captured[1]['content'])
    assert {'поле': 'Город', 'значение': 'Москва'} in context['уже_переданные_сведения']


@pytest.mark.parametrize('text', ['вода не вытекает из трубы', 'вода течёт из крана', 'повреждение газовой трубы'])
def test_water_paraphrase_does_not_credit_other_or_negated_incidents(text):
    assert not incident_asserted(text, 'Повреждение трубы водоснабжения')


def test_denial_in_previous_turn_does_not_negate_following_facts():
    assert check('Пострадавших нет. Москва. Учебная дом 12. Повреждение трубы водоснабжения', practice())['complete']


@pytest.mark.asyncio
async def test_voice_engine_uses_accumulated_report_and_corrects_house(monkeypatch):
    from uuid import uuid4
    from server import Engine, Store
    from test_dialogue import event
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider': 'mock', 'configured': False})
    store = Store(':memory:')
    sid = str(uuid4())
    store.save(sid, {'step': 0, 'seq': 0, 'messages': [], 'replies': {}, 'ended': False,
                     'duty': {'service': 'ЖКХ', 'greeting': 'Старший бригады. Слушаю вас.', 'card': practice()}})
    engine = Engine(store)
    await engine.handle(sid, event(sid, 'call.connected'))
    first = 'во дворе дома учебная дом двенадцать вода вытекает из трубы пострадавших нет'
    reply = await engine.handle(sid, event(sid, 'operator.utterance', first))
    assert 'город' in reply['payload']['text']
    reply = await engine.handle(sid, event(sid, 'operator.utterance', 'Москва'))
    assert 'Информация принята' in reply['payload']['text']
    assert store.load(sid)['duty_report']['complete']
    reply = await engine.handle(sid, event(sid, 'operator.utterance', 'улица Учебная дом тринадцать'))
    assert '12, 13' in reply['payload']['text']
    assert not store.load(sid)['duty_report']['complete']
    reply = await engine.handle(sid, event(sid, 'operator.utterance', 'Уточняю, дом двенадцать'))
    assert store.load(sid)['duty_report']['complete']
