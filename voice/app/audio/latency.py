import json
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("voice")


def log_event(event, **fields):
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False))


@dataclass
class TurnMetrics:
    utterance_id: str
    clock: object = time.monotonic
    times: dict = field(default_factory=dict)

    def mark(self, name):
        self.times[name] = round(self.clock() * 1000)

    def report(self, call_id, reply_id=None):
        values = dict(self.times)
        pairs = {
            "stt_latency_ms": ("vad_end_ms", "stt_final_ms"),
            "backend_latency_ms": ("backend_send_ms", "backend_reply_ms"),
            "tts_first_audio_latency_ms": ("tts_request_ms", "tts_first_audio_ms"),
            "total_turn_latency_ms": ("vad_end_ms", "playback_start_ms"),
        }
        for name, (start, end) in pairs.items():
            if start in values and end in values:
                values[name] = values[end] - values[start]
        log_event("turn.latency", call_id=call_id, reply_id=reply_id,
                  utterance_id=self.utterance_id, **values)
        return values
