import json

import pytest

import llm


@pytest.mark.asyncio
async def test_phone_model_separate_from_authoring_and_cpu(monkeypatch):
    monkeypatch.setenv('LLM_PROFILE', 'standard')
    monkeypatch.setenv('PHONE_LLM_MODEL', 'test-phone')
    monkeypatch.setenv('OLLAMA_URL', 'http://local-model:11434')
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
