import asyncio
import json
import logging
import struct
import weakref
from collections import OrderedDict
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


class SileroPool:
    """Bounded warm workers; a cancelled call invalidates only its own worker."""
    def __init__(self, model, workers, threads):
        self.model, self.threads = model, threads
        self.processes = [None] * workers
        self.available = asyncio.Queue()
        for index in range(workers):
            self.available.put_nowait(index)
        self.cache = OrderedDict()
        self.cache_bytes = 0

    async def ensure(self, index):
        process = self.processes[index]
        if process is None or process.returncode is not None:
            process = await spawn_worker('silero-tts', self.model,
                                         env={'SILERO_CPU_THREADS': str(self.threads)})
            self.processes[index] = process
            try:
                if (await asyncio.wait_for(process.stdout.readline(), 90)).strip() != b'READY':
                    raise RuntimeError('Silero model failed to load')
            except BaseException:
                await stop_process(process)
                self.processes[index] = None
                raise
        return process

    async def render(self, text, speaker, rate):
        key = (text, speaker, rate)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        index = await self.available.get()
        try:
            # Another worker may have populated this phrase while we waited.
            if key in self.cache:
                return self.cache[key]
            process = await self.ensure(index)
            process.stdin.write((json.dumps({'text': text, 'speaker': speaker, 'rate': rate},
                                            ensure_ascii=False) + '\n').encode())
            await process.stdin.drain()
            resampler, chunks, total, source_rate = None, [], 0, None
            while True:
                sample_rate, size = struct.unpack('<II', await process.stdout.readexactly(8))
                if size == 0:
                    break
                total += size
                if size % 2 or total > 32 * 1024 * 1024 or sample_rate not in (8000, 16000, 24000, 48000):
                    raise ValueError('Invalid Silero audio block')
                if source_rate is not None and sample_rate != source_rate:
                    raise ValueError('Silero changed sample rate')
                source_rate = sample_rate
                resampler = resampler or PCMResampler(sample_rate)
                chunks.append(await asyncio.to_thread(resampler.feed, await process.stdout.readexactly(size)))
            if resampler:
                chunks.append(resampler.feed(b'', final=True))
            audio = b''.join(chunks)
            if len(audio) <= 1024 * 1024:
                previous = self.cache.pop(key, b'')
                self.cache_bytes += len(audio) - len(previous)
                self.cache[key] = audio
                while self.cache_bytes > 8 * 1024 * 1024 or len(self.cache) > 128:
                    self.cache_bytes -= len(self.cache.popitem(last=False)[1])
            return audio
        except BaseException:
            await stop_process(self.processes[index])
            self.processes[index] = None
            raise
        finally:
            self.available.put_nowait(index)

    async def close(self):
        await asyncio.gather(*(stop_process(p) for p in self.processes))
        self.processes = [None] * len(self.processes)
        self.cache.clear()
        self.cache_bytes = 0


class SileroTTS:
    _pools = weakref.WeakKeyDictionary()

    def __init__(self, model, speaker, workers=2, threads=2):
        self.model, self.speaker, self.workers, self.threads = model, speaker, workers, threads

    def pool(self):
        pools = self._pools.setdefault(asyncio.get_running_loop(), {})
        key = (self.model, self.workers, self.threads)
        if key not in pools:
            pools[key] = SileroPool(*key)
        return pools[key]

    async def _ensure(self):
        pool = self.pool()
        results = await asyncio.gather(*(pool.ensure(i) for i in range(self.workers)), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def synthesize_stream(self, text, voice_style):
        if not self.model:
            raise ValueError('Silero requires TTS_VOICE .pt path')
        audio = await self.pool().render(text, self.speaker, voice_style.rate)
        # Worker is already free: slow playback on one call never blocks synthesis.
        for offset in range(0, len(audio), 16384):
            yield audio[offset:offset + 16384]

    async def close(self):
        pass  # Shared pool lifetime is owned by the gateway, not an individual call.


async def shutdown_tts():
    pools = SileroTTS._pools.pop(asyncio.get_running_loop(), {})
    await asyncio.gather(*(pool.close() for pool in pools.values()))


async def preload_tts(settings):
    if settings.tts_provider == "silero":
        try:
            provider = SileroTTS(settings.tts_voice, settings.tts_speaker,
                                 settings.tts_workers, settings.tts_threads_per_worker)
            async with asyncio.timeout(120):
                await provider._ensure()
                await asyncio.gather(*(provider.pool().render(f'Проверка связи, линия {i+1}.',
                    settings.tts_speaker, 1) for i in range(settings.tts_workers)))
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
        return SileroTTS(settings.tts_voice, settings.tts_speaker,
                         settings.tts_workers, settings.tts_threads_per_worker)
    if name.startswith("python:"):
        return custom_provider(name, settings)
    raise ValueError(f"Unknown TTS_PROVIDER: {name}")
