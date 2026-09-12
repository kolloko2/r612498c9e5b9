import asyncio
import os
import time
import wave
from pathlib import Path

import numpy as np

from .formats import SAMPLE_RATE


class Recorder:
    """Bounded async queue; disk IO and final mixing run outside the event loop.

    Both tracks come from actual Asterisk snoops, not TTS enqueue operations.
    Arrival timestamps align tracks approximately; they are not RTP timestamps.
    """

    def __init__(self, root: Path, session_id, call_id):
        self.directory = root / str(session_id) / str(call_id)
        self.queue = asyncio.Queue(maxsize=500)
        self.origin = time.monotonic()
        self.files = {}
        self.ends = {"operator": 0, "caller": 0}
        self.task = None
        self.closed = False

    async def start(self):
        await asyncio.to_thread(self._open)
        self.task = asyncio.create_task(self._writer(), name="recording-writer")

    def _open(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.files = {name: open(self.directory / f"{name}.pcm.tmp", "w+b")
                      for name in self.ends}

    def put(self, track, pcm):
        if self.closed:
            return
        if self.task.done():
            self.task.result()
            raise RuntimeError("Recorder stopped unexpectedly")
        offset = round((time.monotonic() - self.origin) * SAMPLE_RATE)
        self.queue.put_nowait((track, offset, pcm))

    async def _writer(self):
        while True:
            item = await self.queue.get()
            try:
                if item is None:
                    return
                await asyncio.to_thread(self._write, *item)
            finally:
                self.queue.task_done()

    def _write(self, track, offset, pcm):
        stream = self.files[track]
        # Absorb <=100 ms arrival jitter; preserve longer genuine gaps as silence.
        end = self.ends[track]
        offset = end if abs(offset - end) < SAMPLE_RATE // 10 else max(end, offset)
        stream.seek(offset * 2)
        stream.write(pcm)
        self.ends[track] = offset + len(pcm) // 2

    async def finalize(self):
        if self.closed:
            return self.paths()
        self.closed = True
        if self.task and self.task.done():
            try:
                self.task.result()
            finally:
                await asyncio.to_thread(self._close_files)
        try:
            await asyncio.wait_for(self.queue.put(None), 10)
            await asyncio.wait_for(self.task, 30)
            return await asyncio.to_thread(self._finish)
        finally:
            await asyncio.to_thread(self._close_files)

    def _close_files(self):
        for stream in self.files.values():
            stream.close()

    def paths(self):
        return {name: str((self.directory / f"{name}.wav").resolve())
                for name in ("operator", "caller", "mixed")}

    def _finish(self):
        for stream in self.files.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
        length = max(self.ends.values())
        writers = {}
        try:
            for name in ("operator", "caller", "mixed"):
                writer = wave.open(str(self.directory / f"{name}.wav.tmp"), "wb")
                writer.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                writers[name] = writer
            for offset in range(0, length, SAMPLE_RATE):
                size = min(SAMPLE_RATE, length - offset)
                chunks = {name: stream.read(size * 2).ljust(size * 2, b"\0")
                          for name, stream in self.files.items()}
                mixed = sum(np.frombuffer(data, dtype="<i2").astype(np.int32)
                            for data in chunks.values())
                chunks["mixed"] = np.clip(mixed, -32768, 32767).astype("<i2").tobytes()
                for name, data in chunks.items():
                    writers[name].writeframesraw(data)
        finally:
            for writer in writers.values():
                writer.close()
        for name in writers:
            temp = self.directory / f"{name}.wav.tmp"
            with open(temp, "r+b") as file:
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.directory / f"{name}.wav")
            with wave.open(str(self.directory / f"{name}.wav"), "rb") as check:
                if check.getnframes() != length:
                    raise IOError("Recording length verification failed")
        if os.name != "nt":
            fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self._close_files()
        for name in self.files:
            (self.directory / f"{name}.pcm.tmp").unlink()
        return self.paths()
