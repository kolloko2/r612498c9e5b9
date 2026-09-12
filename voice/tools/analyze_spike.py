"""Quantify the 997Hz probe. A real phone listening check remains mandatory."""
import argparse
import json
import wave
from pathlib import Path

import numpy as np


def read(path):
    with wave.open(str(path), "rb") as file:
        if (file.getnchannels(), file.getsampwidth(), file.getframerate()) != (1, 2, 16000):
            raise ValueError("Expected mono 16kHz PCM16 WAV")
        return np.frombuffer(file.readframes(file.getnframes()), "<i2").astype(float) / 32768


def analyze(operator, caller, threshold_db=-35):
    size = min(len(operator), len(caller))
    windows = []
    reference = np.exp(-2j * np.pi * 997 * np.arange(16000) / 16000)
    for offset in range(0, size - 16000 + 1, 8000):
        op, ca = operator[offset:offset + 16000], caller[offset:offset + 16000]
        tone_caller = abs(np.dot(ca, reference)) / 8000
        tone_operator = abs(np.dot(op, reference)) / 8000
        windows.append((tone_caller, tone_operator, float(np.sqrt(np.mean(op ** 2))), offset))
    if not windows:
        raise ValueError("Need at least one second of both recordings")
    caller_amp, operator_amp, operator_rms, offset = max(windows)
    leakage = 20 * np.log10(max(operator_amp, 1e-10) / max(caller_amp, 1e-10))
    return {"window_start_s": offset / 16000, "caller_probe_amplitude": float(caller_amp),
            "operator_rms": operator_rms, "probe_leakage_db": round(float(leakage), 2),
            "probe_present": bool(caller_amp > 0.03), "input_energy_present": bool(operator_rms > 0.005),
            "suspected_leak": bool(leakage > threshold_db),
            "diagnostic_threshold_db": threshold_db, "result": "requires_real_phone_listening",
            "note": "Energy is not proof of speech. Acoustic echo and speech harmonics can affect this test."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, help="Directory containing operator.wav and caller.wav")
    parser.add_argument("--threshold-db", type=float, default=-35)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = analyze(read(args.directory / "operator.wav"), read(args.directory / "caller.wav"), args.threshold_db)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
