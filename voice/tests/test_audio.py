import asyncio
from uuid import uuid4

import numpy as np

from app.asterisk.media_ws import MockPeer
from app.audio.environment import Environment
from app.audio.formats import SILENCE, pcm16
from app.audio.latency import TurnMetrics
from app.audio.playback import Playback
from app.audio.resampler import PCMResampler
from app.audio.vad import EnergyVAD
from app.config import Settings
from app.domain.messages import CallerReply
import pytest


def test_vad_preroll_preserves_first_unconfirmed_speech():
    vad = EnergyVAD(Settings(_env_file=None))
    for _ in range(10):
        assert not vad.feed(SILENCE).started
    speech = [pcm16(np.full(320, level)) for level in (0.1, 0.2, 0.3)]
    assert not vad.feed(speech[0]).started
    assert not vad.feed(speech[1]).started
    start = vad.feed(speech[2])
    assert start.started
    assert start.audio.endswith(b"".join(speech))
    assert len(start.audio) == 10 * 640
    for _ in range(19):
        assert not vad.feed(SILENCE).ended
    assert vad.feed(SILENCE).ended


def test_environment_synthetic_noise_and_dropouts():
    env = Environment(seed=42)
    assert env.process(SILENCE) == SILENCE
    env.update({"noise_type": "alarm", "noise_level": 1})
    assert env.process(SILENCE) != SILENCE
    env.update({"dropout_probability": 1})
    assert env.process(pcm16(np.ones(320))) == SILENCE


def test_stream_resampler_chunk_boundaries_match_single_block():
    rate = 22050
    source = pcm16(np.sin(2 * np.pi * 400 * np.arange(rate) / rate) * 0.2)
    whole = PCMResampler(rate)
    expected = whole.feed(source) + whole.feed(b"", final=True)
    split = PCMResampler(rate)
    actual = b"".join(split.feed(source[i:i + 1378]) for i in range(0, len(source), 1378))
    actual += split.feed(b"", final=True)
    assert abs(len(actual) - 32000) <= 2
    assert len(actual) == len(expected)
    assert np.max(np.abs(np.frombuffer(actual, "<i2").astype(int)
                         - np.frombuffer(expected, "<i2").astype(int))) <= 1


async def test_priority_reply_preempts_waiting_normal_reply():
    quiet = asyncio.Event()  # operator currently speaking
    peer = MockPeer(lambda pcm: None)
    errors = []

    async def on_error(*args, **kwargs):
        errors.append(args)

    playback = Playback(Settings(_env_file=None), peer, Environment(), quiet, on_error,
                        lambda *args: errors.append(args))
    try:
        normal = CallerReply(reply_id=uuid4(), text="normal")
        urgent = CallerReply(reply_id=uuid4(), text="urgent", should_interrupt=True)
        await playback.submit(normal, TurnMetrics(str(normal.reply_id)))
        await asyncio.sleep(0.02)
        assert "START_MEDIA_BUFFERING" not in peer.commands
        await playback.submit(urgent, TurnMetrics(str(urgent.reply_id)))
        await asyncio.sleep(0.06)
        assert "START_MEDIA_BUFFERING" in peer.commands
        assert "FLUSH_MEDIA" in peer.commands
        assert not quiet.is_set()
        await playback.interrupt()
        assert not playback.active
        assert not errors
    finally:
        await playback.close()
        await peer.close()


async def test_close_cancels_tasks_even_when_media_flush_fails():
    peer = MockPeer(lambda pcm: None)

    async def broken_flush():
        raise ConnectionError("Socket already gone")

    async def error(*args, **kwargs):
        pass

    playback = Playback(Settings(_env_file=None), peer, Environment(), asyncio.Event(), error, error)
    peer.flush = broken_flush
    with pytest.raises(ConnectionError):
        await playback.close()
    assert playback.worker.done() and playback.renderer.done()
    await peer.close()
