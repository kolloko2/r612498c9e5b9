import asyncio
import json
import wave
from uuid import uuid4

import numpy as np
import pytest

from app.asterisk.call_manager import CallManager
from app.audio.formats import SILENCE, pcm16
from app.config import Settings
from app.domain.messages import CreateCall, EventEnvelope


def config(tmp_path, **kwargs):
    return Settings(_env_file=None, pipeline_mode="conversation",
                    recording_dir=tmp_path / "recordings", outbox_dir=tmp_path / "outbox", **kwargs)


async def wait_for(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def read_events(runtime):
    if not runtime.backend.journal.exists():
        return []
    return [json.loads(line) for line in runtime.backend.journal.read_text(encoding="utf-8").splitlines()]


async def start_call(tmp_path, **kwargs):
    manager = CallManager(config(tmp_path, **kwargs))
    await manager.start()
    request = CreateCall(session_id=uuid4())
    created = await manager.create(request)
    runtime = manager.calls[created["call_id"]]
    await asyncio.wait_for(runtime.context.ready.wait(), 3)
    return manager, runtime, request


async def test_operator_speech_interrupts_bot(tmp_path):
    manager, runtime, _ = await start_call(tmp_path, echo_guard_ms=200)
    try:
        await wait_for(lambda: runtime.playback.active)
        # Речь оператора дольше порога перебивания останавливает ответ бота
        # и распознаётся как обычная реплика.
        for frame in [pcm16(np.full(320, 0.2))] * 40 + [SILENCE] * 40:
            runtime.mock_input.put_nowait(frame)
        await wait_for(lambda: any(e["type"] == "operator.utterance" for e in read_events(runtime)))
        chat = runtime.manager.chat.get(runtime.context.call_id)
        assert any(m['status'] == 'interrupted' for m in chat['messages'] if m['role'] != 'me')
        assert not runtime.barge_open
    finally:
        await manager.close()


async def test_bot_playback_cannot_create_automatic_dialogue(tmp_path):
    manager, runtime, _ = await start_call(tmp_path, echo_guard_ms=200)
    try:
        await wait_for(lambda: runtime.playback.active)
        tone = pcm16(np.full(320, 0.2))
        # Loud microphone input during bot playback is ignored, so speaker echo
        # cannot interrupt the bot or create another automatic response.
        for frame in [tone] * 12 + [SILENCE] * 25:
            runtime.mock_input.put_nowait(frame)
        await wait_for(lambda: not runtime.playback.active)
        await asyncio.sleep(0.25)
        assert not any(e["type"] in ("operator.utterance", "operator.barge_in")
                       for e in read_events(runtime))
        # Speech works normally after playback and the echo guard have ended.
        for frame in [tone] * 12 + [SILENCE] * 25:
            runtime.mock_input.put_nowait(frame)
        await wait_for(lambda: any(e["type"] == "operator.utterance" for e in read_events(runtime)))
        events = read_events(runtime)
        assert events[0]["type"] == "call.connected"
        assert not any(e["type"] == "operator.barge_in" for e in events)
        utterances = [e for e in events if e["type"] == "operator.utterance"]
        assert len(utterances) == 1
        assert utterances[0]["payload"]["text"] == "Учебная реплика оператора"
        assert utterances[0]["payload"]["is_final"] is True
        assert all(e["type"] != "operator.partial" for e in events)
        snapshot = await manager.hangup(runtime.context.call_id)
        assert snapshot["status"] == "ended"
        assert not manager.calls and not manager.sessions
        events = read_events(runtime)
        assert [e["type"] for e in events][-2:] == ["call.ended", "recording.ready"]
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        assert len({e["event_id"] for e in events}) == len(events)
        assert runtime.recorder.task.done()
        assert runtime.playback.worker.done() and runtime.playback.renderer.done()
        assert all(t.done() for t in runtime.tasks)
    finally:
        await manager.close()


async def test_sentence_pause_reaches_backend_as_one_utterance(tmp_path):
    manager, runtime, _ = await start_call(tmp_path, echo_guard_ms=200)
    try:
        # Приветствие доиграло, и эхо-защита после него закончилась.
        await wait_for(lambda: any(m["status"] == "played" for m in
                                   runtime.manager.chat.get(runtime.context.call_id)["messages"]))
        await asyncio.sleep(0.4)
        tone = pcm16(np.full(320, 0.2))
        # Пауза между предложениями длиннее конца реплики VAD, но оператор
        # сразу продолжает: собеседник получает одну реплику, а не два обрывка.
        for frame in [tone] * 12 + [SILENCE] * 38 + [tone] * 12 + [SILENCE] * 40:
            runtime.mock_input.put_nowait(frame)
        await wait_for(lambda: any(e["type"] == "operator.utterance" for e in read_events(runtime)))
        await asyncio.sleep(0.6)
        utterances = [e for e in read_events(runtime) if e["type"] == "operator.utterance"]
        assert [u["payload"]["text"] for u in utterances] == ["Учебная реплика оператора Учебная реплика оператора"]
        chat = runtime.manager.chat.get(runtime.context.call_id)
        assert len([m for m in chat["messages"] if m["role"] == "me"]) == 2
        # Отдельная фраза после ответа собеседника остаётся отдельной репликой.
        await wait_for(lambda: len([m for m in runtime.manager.chat.get(runtime.context.call_id)["messages"]
                                    if m["status"] == "played"]) == 2)
        await asyncio.sleep(0.4)
        for frame in [tone] * 12 + [SILENCE] * 40:
            runtime.mock_input.put_nowait(frame)
        await wait_for(lambda: len([e for e in read_events(runtime) if e["type"] == "operator.utterance"]) == 2)
    finally:
        await manager.close()


async def test_duplicate_session_does_not_originate_twice(tmp_path):
    manager, runtime, request = await start_call(tmp_path)
    try:
        response = await manager.create(request)
        assert response["call_id"] == str(runtime.context.call_id)
        assert len(manager.calls) == 1
    finally:
        await manager.close()


async def test_duplicate_reply_and_environment_patch(tmp_path):
    manager, runtime, _ = await start_call(tmp_path)
    try:
        event = EventEnvelope(seq=8, session_id=runtime.context.session_id, elapsed_ms=1,
                              type="caller.reply", payload={"reply_id": str(uuid4()), "text": "test"})
        before = len(runtime.context.seen_replies)
        await runtime.on_message(event)
        await runtime.on_message(event)
        assert len(runtime.context.seen_replies) == before + 1
        env = event.model_copy(update={"type": "environment.update", "payload": {"noise_type": "wind"}})
        await runtime.on_message(env)
        await runtime.on_message(env.model_copy(update={"payload": {"noise_level": 0.5}}))
        assert runtime.environment.settings.noise_type == "wind"
        assert runtime.environment.settings.noise_level == 0.5
        with pytest.raises(ValueError):
            await runtime.on_message(event.model_copy(update={"session_id": uuid4()}))
    finally:
        await manager.close()


async def test_stt_failure_sends_error_and_finishes(tmp_path):
    manager, runtime, _ = await start_call(tmp_path, stt_provider="does-not-exist")
    try:
        await asyncio.wait_for(runtime.context.done.wait(), 5)
        assert manager.get(runtime.context.call_id)["status"] == "failed"
        assert any(e["type"] == "voice.error" for e in read_events(runtime))
        assert not manager.calls
    finally:
        await manager.close()


async def test_stt_configured_fallback_keeps_call_active(tmp_path):
    manager, runtime, _ = await start_call(tmp_path, stt_provider="does-not-exist",
                                         stt_fallback_provider="mock")
    try:
        await wait_for(lambda: any(e["type"] == "voice.error" for e in read_events(runtime)))
        await asyncio.sleep(0.05)
        assert runtime.context.status == "active"
    finally:
        await manager.close()


async def test_tts_provider_failure_is_cleaned_up(tmp_path):
    manager, runtime, _ = await start_call(tmp_path, tts_provider="does-not-exist")
    try:
        await asyncio.wait_for(runtime.context.done.wait(), 5)
        assert manager.get(runtime.context.call_id)["status"] == "failed"
        assert runtime.playback.renderer.done()
    finally:
        await manager.close()


async def test_hangup_during_setup_and_repeated_hangup(tmp_path):
    manager = CallManager(config(tmp_path))
    response = await manager.create(CreateCall(session_id=uuid4()))
    result = await manager.hangup(response["call_id"])
    assert result["status"] == "ended"
    again = await manager.hangup(response["call_id"])
    assert result == again
    assert not manager.calls


def test_real_conversation_is_gated_on_media_spike(tmp_path):
    with pytest.raises(ValueError, match="spike"):
        config(tmp_path, telephony_mode="asterisk", ari_password="test-only",
               media_password="test-only", api_token="test-only")


async def test_mock_call_recording_has_tts_only_on_caller_track(tmp_path):
    manager, runtime, _ = await start_call(tmp_path)
    try:
        await wait_for(lambda: runtime.playback.active)
        await asyncio.sleep(0.15)
        await manager.hangup(runtime.context.call_id)
        tracks = {}
        for name in ("operator", "caller"):
            with wave.open(runtime.recording_paths[name], "rb") as file:
                tracks[name] = np.frombuffer(file.readframes(file.getnframes()), "<i2")
        assert len(tracks["operator"]) > 0
        assert np.max(np.abs(tracks["operator"])) == 0
        assert np.max(np.abs(tracks["caller"])) > 1000
    finally:
        await manager.close()
