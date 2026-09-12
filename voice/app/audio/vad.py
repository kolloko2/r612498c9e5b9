from collections import deque
from dataclasses import dataclass

import numpy as np

from .formats import FRAME_MS, samples


@dataclass
class VADResult:
    started: bool = False
    ended: bool = False
    audio: bytes = b""


class EnergyVAD:
    """Simple configurable baseline; confirmation + hysteresis + pre-roll.

    Applied only to unmodified operator audio, never caller/environment output.
    For noisy microphones replace with a speech classifier after measuring.
    """

    def __init__(self, settings):
        self.threshold = settings.vad_threshold
        self.start_frames = max(1, settings.vad_start_ms // FRAME_MS)
        self.end_frames = max(1, settings.vad_end_ms // FRAME_MS)
        self.limit_frames = settings.utterance_limit_s * 1000 // FRAME_MS
        self.ring = deque(maxlen=settings.preroll_ms // FRAME_MS)
        self.speaking = False
        self.loud = self.quiet = self.length = 0

    def feed(self, frame):
        rms = float(np.sqrt(np.mean(samples(frame) ** 2)))
        voiced = rms >= self.threshold * (0.7 if self.speaking else 1.0)
        if not self.speaking:
            self.ring.append(frame)
            self.loud = self.loud + 1 if voiced else 0
            if self.loud >= self.start_frames:
                self.speaking, self.quiet, self.length = True, 0, 0
                data = b"".join(self.ring)
                self.ring.clear()
                return VADResult(started=True, audio=data)
            return VADResult()
        self.length += 1
        self.quiet = 0 if voiced else self.quiet + 1
        ended = self.quiet >= self.end_frames or self.length >= self.limit_frames
        if ended:
            self.speaking = False
            self.loud = self.quiet = self.length = 0
        return VADResult(ended=ended, audio=frame)
