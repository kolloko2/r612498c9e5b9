import asyncio
import json

import pytest

import llm


@pytest.mark.asyncio
async def test_phone_model_separate_from_authoring_and_cpu(monkeypatch):
    monkeypatch.setenv('LLM_PROFILE', 'standard')
    monkeypatch.setenv('PHONE_LLM_MODEL', 'test-phone')
    monkeypatch.setenv('OLLAMA_URL', 'http://local-model:11434')
    monkeypatch.setenv('LLM_DEVICE', 'cpu')
    calls = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'message': {'content': json.dumps({'reply': 'Принято.'})}}
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            calls.append((url, kwargs['json']))
            return Response()
    monkeypatch.setattr(llm.httpx, 'AsyncClient', Client)
    await llm.warm_phone_model()
    assert await llm.reply([{'role':'user','content':'Что видно?'}], max_tokens=80) == 'Принято.'
    await llm.complete([{'role':'user','content':'Сценарий'}], max_tokens=1000)
    warm, phone, author = [payload for _, payload in calls]
    assert all(url == 'http://local-model:11434/api/chat' for url, _ in calls)
    assert warm['model'] == phone['model'] == 'test-phone'
    assert author['model'] == 'qwen3:8b'
    assert phone['options']['num_predict'] == 80
    assert phone['options']['num_ctx'] == warm['options']['num_ctx'] == 4096
    assert all(payload['options']['num_gpu'] == 0 for payload in (warm, phone, author))
    assert phone['keep_alive'] == warm['keep_alive'] == -1
    assert warm['options']['num_thread'] == phone['options']['num_thread']


@pytest.mark.asyncio
async def test_preload_retries_startup_connection_race(monkeypatch):
    calls, waits = [], []
    async def warm():
        calls.append(1)
        if len(calls) < 3:
            raise llm.httpx.ConnectError('not ready')
    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(llm, 'warm_phone_model', warm)
    monkeypatch.setattr(llm.asyncio, 'sleep', sleep)
    assert await llm.warm_phone_model_with_retry() is True
    assert len(calls) == 3 and waits == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize('status, attempts', [(400, 1), (429, 6), (503, 6)])
async def test_preload_has_bounded_retries_and_rejects_permanent_errors(monkeypatch, status, attempts):
    calls = []
    async def warm():
        calls.append(1)
        request = llm.httpx.Request('POST', 'http://local/api/chat')
        raise llm.httpx.HTTPStatusError('failure', request=request,
            response=llm.httpx.Response(status, request=request))
    async def sleep(seconds):
        pass
    monkeypatch.setattr(llm, 'warm_phone_model', warm)
    monkeypatch.setattr(llm.asyncio, 'sleep', sleep)
    assert await llm.warm_phone_model_with_retry() is False
    assert len(calls) == attempts


@pytest.mark.asyncio
async def test_preload_cancellation_is_not_retried(monkeypatch):
    async def warm():
        raise asyncio.CancelledError()
    monkeypatch.setattr(llm, 'warm_phone_model', warm)
    with pytest.raises(asyncio.CancelledError):
        await llm.warm_phone_model_with_retry()


@pytest.mark.asyncio
async def test_preload_total_deadline(monkeypatch):
    monkeypatch.setattr(llm, 'PHONE_WARMUP_TIMEOUT_SECONDS', 0)
    async def warm():
        await asyncio.Event().wait()
    monkeypatch.setattr(llm, 'warm_phone_model', warm)
    assert await llm.warm_phone_model_with_retry() is False


@pytest.mark.asyncio
async def test_reply_preserves_all_system_instructions_and_rejects_truncation(monkeypatch):
    async def complete(messages, **kwargs):
        assert 'первая' in messages[0]['content'] and 'исправление роли' in messages[0]['content']
        assert kwargs['phone'] is True
        assert kwargs['max_tokens'] == 120
        return '{"reply":"Ответ."}'
    monkeypatch.setattr(llm, 'complete', complete)
    assert await llm.reply([{'role':'system','content':'первая'},
                            {'role':'user','content':'вопрос'},
                            {'role':'system','content':'исправление роли'}]) == 'Ответ.'
    with pytest.raises(ValueError, match='context'):
        await llm.reply([{'role':'system','content':'ф' * 10000}])


@pytest.mark.asyncio
async def test_mock_warmup_does_not_contact_ollama(monkeypatch):
    monkeypatch.setenv('LLM_PROFILE', 'mock')
    def forbidden(**kwargs):
        raise AssertionError('mock must stay offline')
    monkeypatch.setattr(llm.httpx, 'AsyncClient', forbidden)
    await llm.warm_phone_model()
    assert llm.phone_configuration()['provider'] == 'mock'


@pytest.mark.parametrize('value, gpu, threads', [
    ('cpu', 0, True), ('gpu', 999, False), ('auto', -1, True), ('bogus', -1, True)])
def test_device_switch(monkeypatch, value, gpu, threads):
    """auto передаёт -1: Ollama сама берёт видеокарту или процессор."""
    monkeypatch.setenv('LLM_DEVICE', value)
    options = llm.local_options({'context': 4096}, 80, phone=True)
    assert options.get('num_gpu') == gpu
    assert ('num_thread' in options) is threads


@pytest.mark.asyncio
async def test_full_authoring_budget_reaches_real_adapter(monkeypatch):
    monkeypatch.setenv('LLM_PROFILE', 'standard')
    calls = []
    timeouts = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'message': {'content': 'x' * 24000}}
    class Client:
        def __init__(self, **kwargs): timeouts.append(kwargs['timeout'])
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append(kwargs['json'])
            return Response()
    monkeypatch.setattr(llm.httpx, 'AsyncClient', Client)
    schema = {'type': 'object', 'properties': {'scenario': {'type': 'object'}}}
    assert len(await llm.complete([], max_tokens=4500, json_mode=schema)) == 24000
    assert calls[0]['options']['num_predict'] == 4500
    assert timeouts[0].read == llm.AUTHORING_TIMEOUT_SECONDS
    with pytest.raises(ValueError, match='budget'):
        await llm.complete([], max_tokens=4500)
    with pytest.raises(ValueError, match='budget'):
        await llm.complete([], max_tokens=4500, json_mode=schema, phone=True)


def test_long_request_keeps_background_authoring_paused(monkeypatch):
    import asyncio
    import llm
    marks = []
    monkeypatch.setattr(llm, 'phone_activity_hooks', [lambda: marks.append(1)])
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider': 'ollama', 'model': 'm', 'configured': True, 'context': 0})
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await real_sleep(0)
    monkeypatch.setattr(llm.asyncio, 'sleep', fast_sleep)

    async def slow_request(*args):
        for _ in range(5):
            await real_sleep(0)
        return 'ok'
    monkeypatch.setattr(llm, '_request', slow_request)
    assert asyncio.run(llm.complete([{'role': 'user', 'content': 'x'}])) == 'ok'
    assert len(marks) > 1


def test_phone_reply_is_one_clean_line():
    assert llm.clean_reply('Ирина Яковлева: Ключи у старшей по дому.\nЕщё строка') == 'Ключи у старшей по дому.'
    assert llm.clean_reply('«Щиток в подвале.»') == 'Щиток в подвале.'
    assert llm.clean_reply('Да, 12:30 подойдёт.') == 'Да, 12:30 подойдёт.'
    assert llm.clean_reply('{"reply":"Ответ."}') == 'Ответ.'
    # Слово из кириллицы и латиницы — испорченный ответ: вызывающий возьмёт заготовку.
    assert llm.clean_reply('Подождите Пёtra.') == ''


def llm_roles():
    from briefing import ROLE_REPLIES
    return set(ROLE_REPLIES.values())


@pytest.mark.asyncio
async def test_unanswered_question_gets_a_reply_in_the_role(monkeypatch):
    import asyncio
    from briefing import duty_reply

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider': 'ollama', 'configured': True})
    monkeypatch.setattr(llm, 'reply', slow)
    monkeypatch.setattr(llm, 'VOICE_REPLY_TIMEOUT_SECONDS', .01)
    crew = {'id': 'c', 'name': 'Николай Ершов', 'role': 'crew_leader'}
    history = [{'role': 'user', 'content': 'Сколько вам ехать?'}]
    assert await duty_reply(history, {}, 'ДДС', [], counterpart=crew) == 'Принял, выезжаем. Подробности доложу с места.'
    boss = {'id': 's', 'name': 'Сергей Киселёв', 'role': 'superior'}
    answer = await duty_reply(history, {}, 'ДДС', [], counterpart=boss, purpose='progress')
    assert answer == 'Принял. Доложите, когда работы будут завершены.'
    # Недостающие сведения по-прежнему запрашиваются, а не принимаются.
    asked = await duty_reply(history, {}, 'ДДС', ['Дом'], counterpart=boss)
    assert asked not in llm_roles() and 'дом' in asked.casefold()


def test_shared_runner_uses_small_batches_for_quick_cancellation(monkeypatch):
    monkeypatch.setenv('LLM_DEVICE', 'cpu')
    config = {'model': llm.DEFAULT_PHONE_MODEL, 'context': 8192}
    phone, authoring = llm.local_options(config, 120, phone=True), llm.local_options(config, 4000)
    # The same runner options for every request, otherwise Ollama reloads the model.
    assert phone['num_batch'] == authoring['num_batch'] == 128
    assert phone['num_thread'] == authoring['num_thread']


@pytest.mark.asyncio
async def test_invented_clock_time_is_replaced_by_a_role_reply(monkeypatch):
    from briefing import duty_reply

    async def model(*args, **kwargs):
        return 'Доложить в 14:30.'
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider': 'ollama', 'configured': True})
    monkeypatch.setattr(llm, 'reply', model)
    boss = {'id': 's', 'name': 'Сергей Киселёв', 'role': 'superior'}
    answer = await duty_reply([{'role': 'user', 'content': 'Какие будут указания?'}], {}, 'ДДС', [], counterpart=boss)
    assert answer == 'Доклад принял. Держите на контроле и докладывайте о ходе работ.'
