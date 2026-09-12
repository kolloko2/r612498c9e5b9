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
