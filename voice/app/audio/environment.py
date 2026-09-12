import numpy as np

from app.domain.messages import EnvironmentUpdate
from .formats import FRAME_SAMPLES, SAMPLE_RATE, pcm16, samples


class Environment:
    """Synthetic backgrounds only. Changes caller output, not operator/STT input."""

    def __init__(self, seed=None):
        self.settings = EnvironmentUpdate()
        self.rng = np.random.default_rng(seed)
        self.position = 0
        self.low_state = 0.0

    def update(self, payload):
        # Updates are patches: omitted fields preserve the current scene.
        self.settings = EnvironmentUpdate.model_validate(
            self.settings.model_dump() | payload)

    def process(self, pcm):
        cfg = self.settings
        values = samples(pcm)
        size = len(values)
        t = (np.arange(size) + self.position) / SAMPLE_RATE
        self.position += size
        noise = np.zeros(size)
        if cfg.noise_type != "none" and cfg.noise_level:
            white = self.rng.normal(0, 0.15, size)
            if cfg.noise_type == "alarm":
                noise = 0.15 * np.sin(2 * np.pi * (700 * t + 50 * np.sin(2 * np.pi * t)))
            elif cfg.noise_type == "crowd":
                noise = white * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t) ** 2)
            else:
                cutoff = {"road": 0.08, "wind": 0.025, "indoor": 0.3}[cfg.noise_type]
                low = self.low_state
                for i, value in enumerate(white):
                    low += cutoff * (value - low)
                    noise[i] = low
                self.low_state = low
            values = values + noise * cfg.noise_level
        drop, gain, smooth = {"good": (0, 1, 1), "medium": (0.02, 0.85, 3),
                              "bad": (0.1, 0.65, 7)}[cfg.connection_quality]
        if smooth > 1:
            values = np.convolve(values, np.ones(smooth) / smooth, mode="same")
        values *= gain * cfg.volume_multiplier
        probability = cfg.dropout_probability if cfg.dropout_probability is not None else drop
        if self.rng.random() < probability:
            values = np.zeros(size)
        return pcm16(values)
