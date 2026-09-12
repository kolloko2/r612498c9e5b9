import numpy as np

from .formats import pcm16, samples


class PCMResampler:
    """Continuous linear interpolation with FIR anti-aliasing for downsampling.

    Carries phase, the last sample and FIR history across provider chunks.
    One-stream/one-instance. Output is mono little-endian PCM16.
    """

    def __init__(self, source_rate, target_rate=16000):
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Sample rates must be positive")
        self.source_rate, self.target_rate = source_rate, target_rate
        self.step = source_rate / target_rate
        self.position = 0.0
        self.buffer = np.empty(0, dtype=np.float32)
        self.kernel = None
        if source_rate > target_rate:
            n = np.arange(63) - 31
            cutoff = 0.45 * target_rate / source_rate
            kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(63)
            self.kernel = kernel / kernel.sum()
            self.history = np.zeros(62)

    def feed(self, data, final=False):
        if self.source_rate == self.target_rate:
            return data
        values = samples(data)
        if self.kernel is not None and len(values):
            joined = np.concatenate((self.history, values))
            values = np.convolve(joined, self.kernel, mode="valid")
            self.history = joined[-62:]
        self.buffer = np.concatenate((self.buffer, values))
        if final and len(self.buffer):
            self.buffer = np.append(self.buffer, self.buffer[-1])
        positions = np.arange(self.position, max(0, len(self.buffer) - 1), self.step)
        result = np.interp(positions, np.arange(len(self.buffer)), self.buffer) if len(positions) else []
        next_position = self.position + len(positions) * self.step
        drop = min(int(next_position), max(0, len(self.buffer) - 1))
        self.buffer = self.buffer[drop:]
        self.position = next_position - drop
        return pcm16(result)
