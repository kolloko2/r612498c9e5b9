"""Offline neural TTS -> STT smoke test; never creates a SIP call."""
import argparse
import asyncio
import json
import wave
from pathlib import Path
from uuid import uuid4

from app.audio.formats import FRAME_BYTES, SAMPLE_RATE
from app.audio.stt import VoskSTT
from app.audio.tts import SileroTTS
from app.audio.providers import stop_process
from app.domain.messages import VoiceStyle


async def main(args):
    tts = SileroTTS(str(args.tts_model), args.speaker)
    stt = VoskSTT(str(args.stt_model))
    pcm = bytearray()
    try:
        async for chunk in tts.synthesize_stream(args.text, VoiceStyle()):
            pcm.extend(chunk)
        await stt.open_stream(str(uuid4()), SAMPLE_RATE)
        for offset in range(0, len(pcm), 3200):
            await stt.push_audio(bytes(pcm[offset:offset + 3200]))
        transcript = await stt.get_final()
    finally:
        await stt.close()
        await tts.close()
        await stop_process(SileroTTS._process)
        SileroTTS._process = None
    if args.wav:
        args.wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(args.wav), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(SAMPLE_RATE)
            output.writeframes(pcm)
    if args.raw:
        args.raw.parent.mkdir(parents=True, exist_ok=True)
        padding = (-len(pcm)) % FRAME_BYTES
        args.raw.write_bytes(pcm + bytes(padding))
    result = {"input_text": args.text, "transcript": transcript,
              "pcm_bytes": len(pcm), "sample_rate": SAMPLE_RATE}
    print(json.dumps(result, ensure_ascii=False))
    if not pcm or not transcript.strip():
        raise RuntimeError("Speech smoke did not produce audio and a non-empty transcript")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stt-model", type=Path, required=True)
    parser.add_argument("--tts-model", type=Path, required=True)
    parser.add_argument("--speaker", default="baya")
    parser.add_argument("--text", default="Проверка связи. Раз, два, три.")
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--raw", type=Path)
    asyncio.run(main(parser.parse_args()))
