"""MP3-копия записи учебного звонка.

WAV остаётся основной записью без потерь; MP3 — компактная копия для
прослушивания и передачи (по ТЗ: MP3 для голосовых вызовов, WAV для
высококачественной записи). Кодирует LAME через пакет lameenc, моно 64 кбит/с:
речь 16 кГц остаётся разборчивой, а минута звонка занимает около 0,5 МБ.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

BITRATE_KBPS = 64


def available() -> bool:
    try:
        import lameenc  # noqa: F401
    except ImportError:
        return False
    return True


def encode(wav_path: Path, mp3_path: Path | None = None) -> Path:
    """Закодировать WAV в MP3 рядом с исходником; запись атомарна (через .tmp)."""
    import lameenc
    target = mp3_path or wav_path.with_suffix(".mp3")
    with wave.open(str(wav_path), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("Expected 16-bit PCM")
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(BITRATE_KBPS)
        encoder.set_in_sample_rate(source.getframerate())
        encoder.set_channels(source.getnchannels())
        encoder.set_quality(5)
        temp = target.with_suffix(".mp3.tmp")
        with open(temp, "wb") as out:
            while True:
                frames = source.readframes(source.getframerate())
                if not frames:
                    break
                out.write(encoder.encode(frames))
            out.write(encoder.flush())
            out.flush()
            os.fsync(out.fileno())
    os.replace(temp, target)
    return target
