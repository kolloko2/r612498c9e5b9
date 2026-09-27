"""Optional native inference runs in killable child processes, never the API loop."""
import json
import struct
import sys


def stt(model_path):
    from vosk import KaldiRecognizer, Model

    recognizer = KaldiRecognizer(Model(model_path), 16000)
    parts = []
    print("READY", flush=True)
    while header := sys.stdin.buffer.read(4):
        size, = struct.unpack("<I", header)
        if size > 4 * 1024 * 1024:
            raise ValueError("Oversized STT block")
        if size:
            data = sys.stdin.buffer.read(size)
            if len(data) != size:
                raise EOFError("Truncated STT audio")
            if recognizer.AcceptWaveform(data):
                parts.append(json.loads(recognizer.Result()).get("text", ""))
            result = json.loads(recognizer.PartialResult())
        else:
            parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
            result = {"text": " ".join(p for p in parts if p)}
            recognizer.Reset()
            parts.clear()
        print(json.dumps(result), flush=True)


def sherpa_stt(model_path):
    from pathlib import Path
    import numpy as np
    import sherpa_onnx

    root = Path(model_path)
    if (root / "model.int8.onnx").is_file():
        recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(root / "model.int8.onnx"), tokens=str(root / "tokens.txt"),
            num_threads=4, sample_rate=16000, decoding_method="greedy_search")
    else:
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(root / "am" / "encoder.onnx"),
            decoder=str(root / "am" / "decoder.onnx"),
            joiner=str(root / "am" / "joiner.onnx"),
            tokens=str(root / "lang" / "tokens.txt"),
            num_threads=4, sample_rate=16000, decoding_method="greedy_search")
    audio = bytearray()
    print("READY", flush=True)
    while header := sys.stdin.buffer.read(4):
        size, = struct.unpack("<I", header)
        if size > 4 * 1024 * 1024:
            raise ValueError("Oversized STT block")
        if size:
            data = sys.stdin.buffer.read(size)
            if len(data) != size:
                raise EOFError("Truncated STT audio")
            audio.extend(data)
            result = {"partial": ""}
        else:
            samples = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, samples)
            recognizer.decode_stream(stream)
            result = {"text": stream.result.text}
            audio.clear()
        print(json.dumps(result, ensure_ascii=False), flush=True)


def tts(model_path):
    from piper import PiperVoice, SynthesisConfig

    request = json.loads(sys.stdin.buffer.readline())
    voice = PiperVoice.load(model_path)
    config = SynthesisConfig(length_scale=1 / request["rate"])
    for chunk in voice.synthesize(request["text"], syn_config=config):
        if chunk.sample_width != 2 or chunk.sample_channels != 1:
            raise ValueError("Piper must emit mono PCM16")
        pcm = chunk.audio_int16_bytes
        sys.stdout.buffer.write(struct.pack("<II", chunk.sample_rate, len(pcm)) + pcm)
        sys.stdout.buffer.flush()
    sys.stdout.buffer.write(struct.pack("<II", 16000, 0))
    sys.stdout.buffer.flush()


def silero_tts(model_path):
    import numpy as np
    import torch
    import os

    torch.set_num_threads(max(1, min(8, int(os.getenv('SILERO_CPU_THREADS', '2')))))
    # Python opens Unicode Windows paths correctly; PyTorch's native filename
    # loader can fail when the installation directory contains Cyrillic.
    with open(model_path, 'rb') as model_file:
        model = torch.package.PackageImporter(model_file).load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    print("READY", flush=True)
    for line in sys.stdin.buffer:
        request = json.loads(line)
        audio = model.apply_tts(
            text=request["text"], speaker=request.get("speaker", "baya"), sample_rate=24000,
            put_accent=True, put_stress_homo=True, put_yo=True, put_yo_homo=True)
        samples = np.asarray(audio, dtype=np.float32)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak > 0.95:
            samples *= 0.95 / peak
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        sys.stdout.buffer.write(struct.pack("<II", 24000, len(pcm)) + pcm)
        sys.stdout.buffer.write(struct.pack("<II", 24000, 0))
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    # Native speech libraries on Windows may not accept non-ASCII absolute
    # paths. Each worker is isolated: Python can enter the model directory and
    # pass its short local filename to those libraries without copying models.
    import os
    from pathlib import Path
    model = Path(sys.argv[2]).resolve()
    os.chdir(model.parent)
    {"stt": stt, "sherpa-stt": sherpa_stt, "tts": tts,
     "silero-tts": silero_tts}[sys.argv[1]](model.name)
