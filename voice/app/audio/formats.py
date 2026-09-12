import asyncio

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2
SILENCE = bytes(FRAME_BYTES)


class FramePacer:
    """Bound application lead without accumulating scheduler/IO drift.

    Asterisk remains responsible for phone media timing and framing.
    """

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.deadline = self.loop.time()

    async def tick(self):
        now = self.loop.time()
        self.deadline = max(self.deadline + FRAME_MS / 1000, now - 0.1)
        await asyncio.sleep(max(0, self.deadline - now))


def samples(pcm: bytes):
    if len(pcm) % 2:
        raise ValueError("PCM16 data must have an even byte length")
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def pcm16(values):
    return (np.clip(values, -1, 32767 / 32768) * 32768).astype("<i2").tobytes()


class Framer:
    def __init__(self, size=FRAME_BYTES):
        self.size, self.pending = size, bytearray()

    def feed(self, data):
        self.pending.extend(data)
        while len(self.pending) >= self.size:
            frame = bytes(self.pending[:self.size])
            del self.pending[:self.size]
            yield frame

    def finish(self):
        if len(self.pending) % 2:
            raise ValueError("Truncated PCM sample")
        if self.pending:
            frame = bytes(self.pending).ljust(self.size, b"\0")
            self.pending.clear()
            return frame
        return None
