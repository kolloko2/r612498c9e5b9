import json
import struct
import asyncio
from typing import Protocol

from .providers import custom_provider, spawn_worker, stop_process


class STTProvider(Protocol):
    async def open_stream(self, call_id: str, sample_rate: int): ...
    async def push_audio(self, pcm: bytes): ...
    async def get_partial(self) -> str: ...
    async def get_final(self) -> str: ...
    async def close(self): ...


class MockSTT:
    def __init__(self):
        self.bytes_seen = 0
        self.closed = False

    async def open_stream(self, call_id, sample_rate):
        if sample_rate != 16000:
            raise ValueError("Expected 16kHz")

    async def push_audio(self, pcm):
        self.bytes_seen += len(pcm)

    async def get_partial(self):
        return "Учебная реплика" if self.bytes_seen else ""

    async def get_final(self):
        text = "Учебная реплика оператора" if self.bytes_seen else ""
        self.bytes_seen = 0
        return text

    async def close(self):
        self.closed = True


class VoskSTT:
    def __init__(self, model):
        self.model, self.process, self.partial = model, None, ""

    async def open_stream(self, call_id, sample_rate):
        if sample_rate != 16000 or not self.model:
            raise ValueError("Vosk requires 16kHz and STT_MODEL directory")
        self.process = await spawn_worker("stt", self.model)
        # Windows печатает READY с CRLF: сравниваем содержимое строки,
        # иначе Voice не стартует вне Linux-контейнера.
        if (await self.process.stdout.readline()).strip() != b"READY":
            raise RuntimeError(
                "Vosk model failed to load. Check the model path and that it contains only "
                "ASCII characters: the Kaldi runtime cannot open a non-ASCII path on Windows. "
                "Inside the container /models/... is ASCII, so this affects native Windows runs.")

    async def _request(self, pcm):
        self.process.stdin.write(struct.pack("<I", len(pcm)) + pcm)
        await self.process.stdin.drain()
        line = await self.process.stdout.readline()
        if not line:
            raise RuntimeError("Vosk worker exited")
        return json.loads(line)

    async def push_audio(self, pcm):
        result = await self._request(pcm)
        self.partial = result.get("partial", "")

    async def get_partial(self):
        return self.partial

    async def get_final(self):
        result = await self._request(b"")
        self.partial = ""
        return result.get("text", "")

    async def close(self):
        await stop_process(self.process)


class SherpaSTT(VoskSTT):
    """Accurate utterance-final recognition with a Zipformer ONNX model."""

    async def open_stream(self, call_id, sample_rate):
        if sample_rate != 16000 or not self.model:
            raise ValueError("Sherpa requires 16kHz and STT_FINAL_MODEL directory")
        self.process = await spawn_worker("sherpa-stt", self.model)
        # Windows печатает READY с CRLF: сравниваем содержимое строки,
        # иначе Voice не стартует вне Linux-контейнера.
        if (await self.process.stdout.readline()).strip() != b"READY":
            raise RuntimeError("Sherpa model failed to load")


class HybridSTT:
    """Fast Vosk partials followed by a more accurate Zipformer final result."""

    def __init__(self, partial_model, final_model):
        self.partial = VoskSTT(partial_model)
        self.final = SherpaSTT(final_model)

    async def open_stream(self, call_id, sample_rate):
        try:
            await asyncio.gather(self.partial.open_stream(call_id, sample_rate),
                                 self.final.open_stream(call_id, sample_rate))
        except Exception:
            await self.close()
            raise

    async def push_audio(self, pcm):
        await asyncio.gather(self.partial.push_audio(pcm), self.final.push_audio(pcm))

    async def get_partial(self):
        return await self.partial.get_partial()

    async def get_final(self):
        quick, accurate = await asyncio.gather(self.partial.get_final(), self.final.get_final())
        return accurate.strip() or quick.strip()

    async def close(self):
        await asyncio.gather(self.partial.close(), self.final.close(), return_exceptions=True)


def make_stt(settings, name=None):
    name = name or settings.stt_provider
    if name == "mock":
        return MockSTT()
    if name == "vosk":
        return VoskSTT(settings.stt_model)
    if name == "sherpa":
        # Финальное распознавание без промежуточного текста: Vosk не требуется.
        return SherpaSTT(settings.stt_final_model or settings.stt_model)
    if name == "hybrid":
        return HybridSTT(settings.stt_model, settings.stt_final_model)
    if name.startswith("python:"):
        return custom_provider(name, settings)
    raise ValueError(f"Unknown STT_PROVIDER: {name}")
