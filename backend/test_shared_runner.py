"""Authoring and phone share one resident Ollama runner when they use the same weights."""
import llm

PHONE = 'qwen3:4b-instruct-2507-q4_K_M'


def test_same_weights_use_identical_runner_options(monkeypatch):
    monkeypatch.setenv('LLM_PROFILE', 'fast')
    monkeypatch.setenv('LLM_MODEL', PHONE)
    monkeypatch.setenv('PHONE_LLM_MODEL', PHONE)
    monkeypatch.setenv('PHONE_LLM_THREADS', '6')
    authoring, phone = llm.configuration(), llm.phone_configuration()
    assert authoring['model'] == phone['model'] == PHONE
    a = llm.local_options(authoring, 2500)
    p = llm.local_options(phone, 120, phone=True)
    # Only per-request values may differ; runner options must be equal.
    for key in ('num_ctx', 'num_thread', 'num_gpu'):
        assert a[key] == p[key], key


def test_separate_phone_weights_keep_small_phone_context(monkeypatch):
    monkeypatch.setenv('LLM_PROFILE', 'standard')
    monkeypatch.delenv('LLM_MODEL', raising=False)
    monkeypatch.delenv('LLM_SHARED_RUNNER', raising=False)
    monkeypatch.setenv('PHONE_LLM_MODEL', PHONE)
    assert llm.configuration()['model'] == 'qwen3:8b'
    assert llm.phone_configuration()['context'] == 4096
    assert 'num_thread' not in llm.local_options(llm.configuration(), 2500)
