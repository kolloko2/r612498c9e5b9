import os
from uuid import uuid4

os.environ["DIALOGUE_DB"] = ":memory:"
import pytest

from server import Engine, Scenario, Store, spoken_reply, people_answer


def test_people_answer_uses_explicit_facts_only():
    assert people_answer('Вы один или с вами кто-то есть?', {'known_facts': ['В квартире находятся Анна и её восьмилетний сын']}) == 'В квартире находятся Анна и её восьмилетний сын.'
    assert people_answer('С вами кто-нибудь есть?', {'known_facts': ['Боль в груди']}) is None


def test_spoken_reply_preserves_words_and_removes_markup():
    assert spoken_reply('Анна: **Помогите**, мне плохо') == 'Помогите, мне плохо.'


@pytest.mark.asyncio
async def test_role_reversal_is_repaired_before_playback():
    answers = iter(['Помогу, если сможете выйти.', 'Мне плохо, помогите мне, пожалуйста.'])
    async def model(messages):
        return next(answers)
    store = Store(':memory:')
    store.put_scenario(custom_scenario())
    engine = Engine(store, model)
    sid = str(uuid4())
    await engine.handle(sid, event(sid, 'call.connected', scenario_id='medical_case'))
    reply = await engine.handle(sid, event(sid, 'operator.utterance', 'Как я могу помочь?'))
    assert reply['payload']['text'] == 'Мне плохо, помогите мне, пожалуйста.'
    assert all('Помогу' not in m['content'] for m in store.load(sid)['messages'])


def event(sid, kind, text="", mode="auto", scenario_id=None):
    return {"session_id": sid, "event_id": str(uuid4()), "type": kind,
            "payload": {"text": text, "utterance_id": str(uuid4()), "mode": mode,
                        "scenario_id": scenario_id}}


@pytest.mark.asyncio
async def test_control_receipt_survives_engine_restart(tmp_path):
    path=tmp_path/'receipt.sqlite3';store=Store(str(path));sid=str(uuid4())
    control=event(sid,'call.ended');engine=Engine(store)
    assert await engine.handle(sid,control) is None
    assert control['event_id'] in store.load(sid)['replies']
    store.db.close();reopened=Store(str(path));replayed=Engine(reopened)
    assert await replayed.handle(sid,control) is None
    assert reopened.load(sid)['ended'] is True
    reopened.db.close()


@pytest.mark.asyncio
async def test_resume_preserves_transcript_and_ignores_superseded_end():
    store=Store(':memory:');engine=Engine(store);sid=str(uuid4());old=str(uuid4())
    ended=event(sid,'call.ended');ended['payload'].update(call_id=old,reason='media_disconnected')
    await engine.handle(sid,ended);resume=event(sid,'session.resume');resume['payload']['previous_call_id']=old
    await engine.handle(sid,resume);assert not store.load(sid)['ended']
    late=event(sid,'call.ended');late['payload']['call_id']=old
    await engine.handle(sid,late);assert not store.load(sid)['ended']
    await engine.handle(sid,event(sid,'call.ended'));assert store.load(sid)['ended']


def custom_scenario(scenario_id="medical_case", opening="Мне очень плохо."):
    return Scenario(id=scenario_id, title="Проблема со здоровьем", description="Проверка",
                    victim_name="Иван", incident="Сильная боль в груди", location="Учебная улица, дом 1",
                    known_facts=["Боль началась десять минут назад"], unknown_facts=["Диагноз"],
                    emotion="Испуган и говорит с трудом", behavior="Отвечает кратко", opening=opening)


@pytest.mark.asyncio
async def test_selected_scenario_is_snapshotted_and_replayed(tmp_path):
    calls = []

    async def model(messages):
        assert "пострадавшего" in messages[0]["content"]
        assert "Боль началась десять минут назад" in messages[0]["content"]
        calls.append(messages)
        return "Учебная улица, дом один."

    store = Store(str(tmp_path / "state.db"))
    store.put_scenario(custom_scenario())
    engine = Engine(store, model)
    sid = str(uuid4())
    opening = await engine.handle(sid, event(sid, "call.connected", scenario_id="medical_case"))
    assert opening["payload"]["text"] == "Мне очень плохо."
    store.put_scenario(custom_scenario(opening="Новая реплика"))
    operator = event(sid, "operator.utterance", "Назовите адрес")
    reply = await engine.handle(sid, operator)
    assert await engine.handle(sid, operator) == reply
    assert len(calls) == 1
    restarted = Engine(Store(str(tmp_path / "state.db")), model)
    await restarted.handle(sid, event(sid, "operator.utterance", "Повторите"))
    assert any(message["content"] == "Назовите адрес" for message in calls[-1])
    await restarted.handle(sid, event(sid, "call.ended"))
    assert await restarted.handle(sid, event(sid, "operator.utterance", "Адрес?")) is None


@pytest.mark.asyncio
async def test_manual_silence_failure_and_missing_scenario():
    async def fail(*args):
        raise ValueError("broken")

    store = Store(":memory:")
    engine = Engine(store, fail)
    sid = str(uuid4())
    assert await engine.handle(sid, event(sid, "call.connected", mode="manual")) is None
    assert not store.load(sid)["messages"]
    missing_sid = str(uuid4())
    with pytest.raises(ValueError, match="unavailable"):
        await engine.handle(missing_sid, event(missing_sid, "call.connected", scenario_id="missing"))
    with pytest.raises(ValueError, match="Session mismatch"):
        await engine.handle(sid, event(str(uuid4()), "call.connected"))


@pytest.mark.asyncio
async def test_transcribed_bot_playback_does_not_trigger_a_reply(tmp_path):
    calls = 0

    async def model(messages):
        nonlocal calls
        calls += 1
        return "Не должно прозвучать"

    store = Store(str(tmp_path / "echo.db"))
    store.put_scenario(custom_scenario(opening="Я у окна, вокруг сильный дым,"))
    engine = Engine(store, model)
    sid = str(uuid4())
    await engine.handle(sid, event(sid, "call.connected", scenario_id="medical_case"))
    echoed = event(sid, "operator.utterance", "Я у окна вокруг сильный дым")
    assert await engine.handle(sid, echoed) is None
    assert await engine.handle(sid, echoed) is None
    assert calls == 0
    assert store.load(sid)["echoes_ignored"] == 1


def test_scenario_crud_and_protects_last_enabled(tmp_path):
    store = Store(str(tmp_path / "scenarios.db"))
    created = store.put_scenario(custom_scenario())
    assert store.scenario("medical_case") == created
    assert len(store.list_scenarios()) == 2
    store.delete_scenario("medical_case")
    assert store.scenario("medical_case") is None


def test_model_profiles_select_provider_and_model(monkeypatch):
    """Профиль задаёт и провайдера, и модель; без профиля работают прежние ENV."""
    import llm

    monkeypatch.setenv('LLM_PROFILE', 'standard')
    config = llm.configuration()
    assert config['provider'] == 'ollama' and config['model'] == 'qwen3:4b'
    assert config['configured'] is True and config['context'] == 8192

    monkeypatch.setenv('LLM_PROFILE', 'mock')
    assert llm.configuration()['provider'] == 'mock'

    # Неизвестное значение не подменяет провайдера молча.
    monkeypatch.setenv('LLM_PROFILE', 'turbo')
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    fallback = llm.configuration()
    assert fallback['provider'] == 'mock' and fallback['profile'] == ''

    # Обратная совместимость: без профиля читаются прежние переменные.
    monkeypatch.delenv('LLM_PROFILE', raising=False)
    monkeypatch.setenv('LLM_PROVIDER', 'ollama')
    monkeypatch.setenv('DIALOGUE_MODEL', 'custom-model')
    assert llm.configuration()['model'] == 'custom-model'


def test_every_profile_is_described_for_the_administrator():
    import llm
    for name, profile in llm.PROFILES.items():
        assert profile['title'] and profile['hint'], name
        assert profile['model'], name
