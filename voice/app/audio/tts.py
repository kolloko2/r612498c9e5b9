import asyncio
import json
import logging
import struct
from collections.abc import AsyncIterator
from typing import Protocol

import numpy as np

from app.domain.messages import VoiceStyle
from .formats import FRAME_SAMPLES, SAMPLE_RATE, pcm16
from .providers import custom_provider, spawn_worker, stop_process
from .resampler import PCMResampler


class TTSProvider(Protocol):
    def synthesize_stream(self, text: str, voice_style: VoiceStyle) -> AsyncIterator[bytes]: ...
    async def close(self): ...


class MockTTS:
    """Audible tone, deliberately not presented as synthesized speech."""

    async def synthesize_stream(self, text, voice_style):
        frames = max(5, min(150, round(len(text) * 2 / voice_style.rate)))
        for index in range(frames):
            t = (np.arange(FRAME_SAMPLES) + index * FRAME_SAMPLES) / SAMPLE_RATE
            yield pcm16(0.12 * np.sin(2 * np.pi * 440 * t))
            await asyncio.sleep(0)

    async def close(self):
        pass


class PiperTTS:
    def __init__(self, model):
        self.model, self.process = model, None

    async def synthesize_stream(self, text, voice_style):
        if not self.model:
            raise ValueError("Piper requires TTS_VOICE .onnx path")
        self.process = await spawn_worker("tts", self.model)
        try:
            self.process.stdin.write((json.dumps({"text": text, "rate": voice_style.rate}) + "\n").encode())
            await self.process.stdin.drain()
            self.process.stdin.close()
            resampler = None
            rate = None
            while True:
                header = await self.process.stdout.readexactly(8)
                sample_rate, size = struct.unpack("<II", header)
                if size == 0:
                    break
                if size > 32 * 1024 * 1024 or size % 2:
                    raise ValueError("Invalid Piper worker audio block")
                if rate is not None and sample_rate != rate:
                    raise ValueError("Piper changed sample rate mid-stream")
                rate = sample_rate
                resampler = resampler or PCMResampler(rate)
                pcm = await self.process.stdout.readexactly(size)
                converted = await asyncio.to_thread(resampler.feed, pcm)
                if converted:
                    yield converted
            if resampler:
                tail = resampler.feed(b"", final=True)
                if tail:
                    yield tail
            if await self.process.wait() != 0:
                raise RuntimeError("Piper worker failed")
        finally:
            await self.close()

    async def close(self):
        await stop_process(self.process)
        self.process = None


class SileroTTS:
    """Shared persistent Silero worker with Russian auto-stress."""
    _process = None
    _lock = None

    def __init__(self, model, speaker):
        self.model, self.speaker = model, speaker

    async def _ensure(self):
        if type(self)._process and type(self)._process.returncode is None:
            return
        type(self)._process = await spawn_worker("silero-tts", self.model)
        if await type(self)._process.stdout.readline() != b"READY\n":
            raise RuntimeError("Silero model failed to load")

    async def synthesize_stream(self, text, voice_style):
        if not self.model:
            raise ValueError("Silero requires TTS_VOICE .pt path")
        if type(self)._lock is None:
            type(self)._lock = asyncio.Lock()
        async with type(self)._lock:
            await self._ensure()
            process = type(self)._process
            try:
                request = {"text": text, "speaker": self.speaker, "rate": voice_style.rate}
                process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
                await process.stdin.drain()
                resampler = None
                while True:
                    header = await process.stdout.readexactly(8)
                    sample_rate, size = struct.unpack("<II", header)
                    if size == 0:
                        break
                    if size > 32 * 1024 * 1024 or size % 2:
                        raise ValueError("Invalid Silero worker audio block")
                    resampler = resampler or PCMResampler(sample_rate)
                    converted = await asyncio.to_thread(resampler.feed,
                                                         await process.stdout.readexactly(size))
                    if converted:
                        yield converted
                if resampler and (tail := resampler.feed(b"", final=True)):
                    yield tail
            except BaseException:
                await stop_process(process)
                type(self)._process = None
                raise

    async def close(self):
        # The shared worker stays warm between replies and calls. systemd closes
        # the service cgroup, including this child, on gateway shutdown.
        pass


async def preload_tts(settings):
    if settings.tts_provider == "silero":
        try:
            await SileroTTS(settings.tts_voice, settings.tts_speaker)._ensure()
        except Exception as exc:
            logging.warning("Silero preload failed; configured fallback remains available: %s",
                            type(exc).__name__)


def make_tts(settings, name=None):
    name = name or settings.tts_provider
    if name == "mock":
        return MockTTS()
    if name == "piper":
        return PiperTTS(settings.tts_fallback_voice or settings.tts_voice)
    if name == "silero":
        return SileroTTS(settings.tts_voice, settings.tts_speaker)
    if name.startswith("python:"):
        return custom_provider(name, settings)
    raise ValueError(f"Unknown TTS_PROVIDER: {name}")
