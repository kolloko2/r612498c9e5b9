import pytest
import llm
from field_dialogue import answer, report_context
from test_dialogue import event
from server import Engine, Store
from uuid import uuid4


def test_crew_speech_removes_callsign_but_preserves_facts():
    from field_dialogue import crew_speech
    assert crew_speech('Старший бригады 01-3. Ребёнку 11 лет, дом 13.') == 'Старший бригады. Ребёнку 11 лет, дом 13.'
    assert crew_speech('Бригада №17 прибыла.') == 'Бригада прибыла.'


def test_context_excludes_future_and_grading():
    context = report_context({'initial_card': {'street': 'Учебная', 'house': '7'},
                              'dds_expectation': {'secret': True},
                              'updates': [{'text': 'future'}],
                              'events': [{'type': 'situation.update', 'detail':
                                          {'text': 'Бригада выехала', 'unlocks_status': 'Начало реагирования'}}]},
                             'Старший', 'Бригада прибыла')
    assert context['reports'] == ['Бригада выехала']
    assert 'future' not in str(context) and 'secret' not in str(context)


@pytest.mark.asyncio
async def test_model_receives_question_and_current_facts(monkeypatch):
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider': 'ollama', 'configured': True})
    async def model(messages, **kwargs):
        assert messages[-1]['content'] == 'Расскажите обстановку?'
        assert 'приступили' in messages[1]['content'].lower()
        return 'Нет, только приступили к работам.'
    monkeypatch.setattr(llm, 'reply', model)
    assert await answer({'text': 'Приступили к работам'}, [{'role': 'user', 'content': 'Расскажите обстановку?'}]) == 'Нет, только приступили к работам.'


@pytest.mark.asyncio
async def test_duplicate_readbacks_are_not_reprocessed_but_facts_and_question_survive(monkeypatch):
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider':'ollama','configured':True})
    async def model(messages, **kwargs):
        assert sum('Осматриваем ребёнка.' in m['content'] for m in messages) == 1
        assert 'В пути.' in messages[1]['content']
        assert messages[-1]['content'] == 'Что наблюдаете?'
        return 'Осматриваем ребёнка.'
    monkeypatch.setattr(llm, 'reply', model)
    report = {'text':'Осматриваем ребёнка.', 'reports':['В пути.', 'В пути.', 'Осматриваем ребёнка.']}
    history = [{'role':'assistant','content':'Старший: Осматриваем ребёнка.'},
               {'role':'user','content':'Что наблюдаете?'}]
    await answer(report, history)
    assert len(report['reports']) == 3 and len(history) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('question', ['Работы завершены, можно закрыть?', 'Вы уже прибыли?',
    'Закончите через пять минут, верно?', 'Причина — взрыв, правильно?', 'Когда закончите?'])
async def test_leading_operational_questions_do_not_create_facts(monkeypatch, question):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Critical facts must not be inferred')
    monkeypatch.setattr(llm, 'reply', forbidden)
    result = await answer({'text': 'Бригада выехала.'}, [{'role':'user','content':question}])
    assert 'Бригада выехала.' in result
    assert 'пять' not in result and 'взрыв' not in result


@pytest.mark.asyncio
async def test_timeout_and_mock_are_grounded(monkeypatch):
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider': 'ollama', 'configured': True})
    async def slow(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(llm, 'reply', slow)
    report = {'text': 'Бригада прибыла', 'card': {'street': 'Учебная', 'house': '7'}}
    assert 'Учебная, 7' in await answer(report, [{'role': 'user', 'content': 'Какой адрес?'}])
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider': 'mock', 'configured': True})
    assert 'пока не поступало' in await answer(report, [{'role': 'user', 'content': 'Есть пострадавшие?'}])


@pytest.mark.asyncio
async def test_crew_does_not_turn_into_dispatcher(monkeypatch):
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider':'ollama','configured':True})
    async def wrong_role(*args, **kwargs):
        return 'Дождемся доклада от бригады.'
    monkeypatch.setattr(llm, 'reply', wrong_role)
    assert await answer({'text':'Бригада выехала.'},[{'role':'user','content':'Какая обстановка?'}]) == 'Бригада выехала.'


@pytest.mark.asyncio
async def test_general_question_cannot_erase_known_casualties(monkeypatch):
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider':'ollama','configured':True})
    async def model(*args, **kwargs):
        return 'Нет подтверждения пострадавших. Обстановка не уточнена.'
    monkeypatch.setattr(llm, 'reply', model)
    report = {'text':'Ребёнок осмотрен, результаты переданы медицинской службе.', 'card':{'injured':True}}
    assert await answer(report, [{'role':'user','content':'Расскажите обстановку'}]) == report['text']


@pytest.mark.asyncio
async def test_general_question_cannot_turn_unknown_casualties_into_none(monkeypatch):
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider':'ollama','configured':True})
    async def model(*args, **kwargs):
        return 'Осматриваем квартиры. Пострадавших пока нет.'
    monkeypatch.setattr(llm, 'reply', model)
    report = {'text':'Осматриваем квартиры. Подтверждения пострадавших пока нет.'}
    assert await answer(report, [{'role':'user','content':'Расскажите обстановку'}]) == report['text']


@pytest.mark.asyncio
async def test_casualties_cannot_be_invented_from_operator_question(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Unknown casualties must not be inferred by a model')
    monkeypatch.setattr(llm, 'reply', forbidden)
    history = [{'role': 'user', 'content': 'Пострадавших нет, правильно?'}]
    assert 'пока не поступало' in await answer({'text': 'Бригада прибыла'}, history)
    assert 'пока не поступало' in await answer({'text': 'Приступили к устранению прорыва трубы.'}, history)
    assert await answer({'text': 'Обнаружены двое пострадавших'}, history) == 'Обнаружены двое пострадавших'
    assert 'пострадавшие есть' in await answer({'text':'Осматриваем ребёнка.', 'card':{'injured':True}},history)
    assert 'двое' in await answer({'text':'Обнаружены двое пострадавших', 'card':{'injured':False}},history)


@pytest.mark.asyncio
async def test_engine_saves_answer_and_deduplicates(monkeypatch):
    calls = []
    async def reply(report, messages):
        calls.append(messages[-1]['content'])
        return 'Пока выполняем работы.'
    monkeypatch.setattr('server.field_answer', reply)
    store = Store(':memory:')
    sid = str(uuid4())
    store.save(sid, {'step': 0, 'seq': 0, 'messages': [], 'replies': {}, 'ended': False,
                     'field_report': {'source': 'Старший', 'text': 'Приступили к работам'}})
    engine = Engine(store)
    await engine.handle(sid, event(sid, 'call.connected'))
    question = event(sid, 'operator.utterance', 'Что сейчас делаете?')
    result = await engine.handle(sid, question)
    assert 'выполняем' in result['payload']['text']
    assert await engine.handle(sid, question) == result
    assert calls == ['Что сейчас делаете?']


def test_report_names_the_crew_once():
    from field_dialogue import report_speech
    assert report_speech({'source': 'Старший бригады, Бригада 17', 'crew': 'Бригада 17',
                          'text': 'Бригада выехала на улицу Учебную, дом 12.'}) == 'Бригада 17 выехала на улицу Учебную, дом 12.'
    assert report_speech({'source': 'Старший бригады, Бригада 17', 'crew': 'Бригада 17',
                          'text': 'Прибыли по адресу.'}) == 'Бригада 17. Прибыли по адресу.'
