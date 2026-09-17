"""Измерение точности распознавания речи на воспроизводимом наборе фраз.

Набор синтезируется системным голосом Windows (Microsoft Irina, ru-RU), поэтому
эталонный текст известен точно и результат можно перепроверить на другой машине
без записи живых людей и без передачи чьего-либо голоса наружу.

Считается WER — доля слов, которые пришлось бы вставить, удалить или заменить,
чтобы получить эталон. Регистр, знаки препинания и ё/е не учитываются: оператор
записывает сведения в карточку, а не диктант.

Набор фраз намеренно состоит из того, что диспетчер произносит в занятии: адрес
с названием улицы, ФИО заявителя, тип происшествия, ответ дежурного службы.
Названия улиц — самая дорогая ошибка: расхождение в одну букву уводит силы по
другому адресу.

Запуск:
    python tools/stt_benchmark.py --synthesize     # один раз, создаёт эталон
    python tools/stt_benchmark.py --vosk deploy/models/vosk-model-small-ru-0.22
    python tools/stt_benchmark.py --sherpa deploy/models/sherpa-onnx-nemo-ctc-...
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "tmp" / "stt-benchmark"

PHRASES = [
    ("p1", "Горит балкон на девятом этаже, пострадавших не видно"),
    ("p2", "Улица Дубнинская дом двенадцать квартира пять"),
    ("p3", "Дежурный служба сто один слушаю вас"),
    ("p4", "Докладываю по карточке происшествия, пожар в квартире"),
    ("p5", "Заявитель Иванова Елена Сергеевна, телефон записан"),
    ("p6", "Адрес уточняю: Зеленоград, корпус девятьсот два, подъезд один"),
    ("p7", "Пострадавших нет, доступ на объект свободен"),
]


def words(text: str) -> list[str]:
    return re.sub(r"[^а-яё0-9 ]", "", text.lower().replace("ё", "е")).split()


def wer(reference: str, hypothesis: str) -> float:
    """Расстояние редактирования по словам, нормированное на длину эталона."""
    ref, hyp = words(reference), words(hypothesis)
    table = [[0] * (len(hyp) + 1) for _ in range(len(ref) + 1)]
    for i in range(len(ref) + 1):
        table[i][0] = i
    for j in range(len(hyp) + 1):
        table[0][j] = j
    for i in range(1, len(ref) + 1):
        for j in range(1, len(hyp) + 1):
            table[i][j] = min(table[i - 1][j] + 1, table[i][j - 1] + 1,
                              table[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    return table[-1][-1] / max(1, len(ref))


def synthesize(target: Path) -> None:
    """Создаёт эталонный набор системным синтезатором Windows."""
    target.mkdir(parents=True, exist_ok=True)
    script = ["Add-Type -AssemblyName System.Speech"]
    for key, text in PHRASES:
        raw = target / f"{key}.raw.wav"
        script.append(
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.SelectVoice('Microsoft Irina Desktop'); "
            f"$s.SetOutputToWaveFile('{raw}'); $s.Speak('{text}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", "; ".join(script)], check=True)
    for key, _ in PHRASES:
        subprocess.run(["ffmpeg", "-y", "-i", str(target / f"{key}.raw.wav"),
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                        str(target / f"{key}.wav")],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (target / f"{key}.raw.wav").unlink()
    (target / "reference.json").write_text(
        json.dumps(dict(PHRASES), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"эталонный набор создан: {target}")


def read_wave(path: Path):
    with wave.open(str(path), "rb") as stream:
        if stream.getframerate() != 16000 or stream.getnchannels() != 1:
            raise ValueError(f"{path.name}: требуется моно 16 кГц")
        return stream.readframes(stream.getnframes())


def decode_vosk(model_path: str, files: list[Path]) -> tuple[dict[str, str], float]:
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    model = Model(model_path)
    results, spent = {}, 0.0
    for path in files:
        pcm = read_wave(path)
        recognizer = KaldiRecognizer(model, 16000)
        started = time.perf_counter()
        for offset in range(0, len(pcm), 8000):
            recognizer.AcceptWaveform(pcm[offset:offset + 8000])
        results[path.stem] = json.loads(recognizer.FinalResult()).get("text", "")
        spent += time.perf_counter() - started
    return results, spent


def decode_sherpa(model_path: str, files: list[Path]) -> tuple[dict[str, str], float]:
    import numpy as np
    import sherpa_onnx
    root = Path(model_path)
    recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
        model=str(root / "model.int8.onnx"), tokens=str(root / "tokens.txt"),
        num_threads=4, sample_rate=16000, decoding_method="greedy_search")
    results, spent = {}, 0.0
    for path in files:
        samples = np.frombuffer(read_wave(path), dtype="<i2").astype(np.float32) / 32768.0
        started = time.perf_counter()
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, samples)
        recognizer.decode_stream(stream)
        spent += time.perf_counter() - started
        results[path.stem] = stream.result.text
    return results, spent


def report(reference: dict[str, str], results: dict[str, str], spent: float,
           audio_seconds: float) -> float:
    errors = total = 0
    for key, expected in reference.items():
        actual = results.get(key, "")
        rate = wer(expected, actual)
        errors += rate * len(words(expected))
        total += len(words(expected))
        flag = "  " if rate == 0 else "!!"
        print(f"{flag} {key} {rate * 100:5.1f}%  {actual}")
        if rate:
            print(f"        эталон: {expected}")
    overall = errors / max(1, total) * 100
    print(f"\nWER: {overall:.1f}%   время {spent:.1f} с на {audio_seconds:.1f} с аудио "
          f"(RTF {spent / max(0.001, audio_seconds):.2f})")
    return overall


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--synthesize", action="store_true")
    parser.add_argument("--vosk", help="каталог модели Vosk")
    parser.add_argument("--sherpa", help="каталог модели sherpa-onnx (NeMo CTC)")
    args = parser.parse_args()

    if args.synthesize:
        synthesize(args.dir)
        if not (args.vosk or args.sherpa):
            return 0

    reference_file = args.dir / "reference.json"
    if not reference_file.is_file():
        print("Эталонный набор отсутствует; запустите с --synthesize", file=sys.stderr)
        return 1
    reference = json.loads(reference_file.read_text(encoding="utf-8"))
    files = [args.dir / f"{key}.wav" for key in reference]
    audio_seconds = sum(len(read_wave(path)) / 2 / 16000 for path in files)

    if args.vosk:
        print("=== Vosk:", args.vosk)
        results, spent = decode_vosk(args.vosk, files)
        report(reference, results, spent, audio_seconds)
    if args.sherpa:
        print("\n=== sherpa-onnx:", args.sherpa)
        results, spent = decode_sherpa(args.sherpa, files)
        report(reference, results, spent, audio_seconds)
    if not (args.vosk or args.sherpa):
        print("Укажите --vosk и/или --sherpa", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
