"""Measured text -> streamed local LLM -> sentence Silero -> WAV/MP3 experiment.

Not a SIP call or STT benchmark. No audio is played on the user's speakers.
The recording retains real synthesis/inference gaps, not an edited approximation.
"""
from __future__ import annotations

import argparse
from array import array
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time
import wave
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'qwen3:4b-instruct-2507-q4_K_M'
QUESTION = 'Бригада, доложите, что обнаружили на месте и когда закончите работы?'
SYSTEM = ('Ты старший учебной бригады водоканала, разговариваешь с диспетчером ДДС. '
          'Ответь по-русски двумя короткими предложениями, всего не более 30 слов: '
          'сначала обстановка и действия, затем срок. Не повторяй приветствие и название бригады. '
          'Не придумывай сроки, пострадавших или завершение работ. '
          'Факты: бригада на месте, в подвале вода, место протечки ограждено, '
          'сейчас обследуем трубу, пострадавших нет, точное время завершения пока неизвестно. '
          'Верни только JSON с одной строкой reply.')
SCHEMA = {'type': 'object', 'properties': {'reply': {'type': 'string'}},
          'required': ['reply'], 'additionalProperties': False}


def context_preparation_payload(options, system=SYSTEM, raw_text=False):
    return {'model': MODEL, 'messages': [{'role': 'system', 'content': system}],
            'stream': False, 'think': False, **({} if raw_text else {'format': SCHEMA}), 'keep_alive': -1,
            'options': {**options, 'num_predict': 1}}


def reply_prefix(raw: str) -> tuple[str, bool]:
    """Decode only complete JSON string characters; never speak JSON or escapes."""
    start = re.match(r'^\s*\{\s*"reply"\s*:\s*"', raw)
    if start is None:
        if len(raw) > 40:
            raise ValueError('Unexpected streamed JSON envelope')
        return '', False
    body, result, index = raw[start.end():], [], 0
    while index < len(body):
        char = body[index]
        if char == '"':
            return ''.join(result), True
        if ord(char) < 32:
            raise ValueError('Unescaped control character in reply')
        if char != '\\':
            result.append(char)
            index += 1
            continue
        if index + 1 >= len(body):
            break
        size = 6 if body[index + 1] == 'u' else 2
        if index + size > len(body):
            break
        escaped = body[index:index + size]
        decoded = json.loads('"' + escaped + '"')
        if decoded and 0xD800 <= ord(decoded[0]) <= 0xDBFF:
            if index + 12 > len(body):
                break
            escaped = body[index:index + 12]
            decoded = json.loads('"' + escaped + '"')
            size = 12
        if any(0xD800 <= ord(c) <= 0xDFFF for c in decoded):
            raise ValueError('Unpaired Unicode surrogate')
        result.append(decoded)
        index += size
    return ''.join(result), False


class Sentences:
    def __init__(self):
        self.pending = ''

    def feed(self, delta: str, *, final=False) -> list[str]:
        self.pending += delta
        result = []
        while match := re.search(r'[.!?…](?=\s)', self.pending):
            end = match.end()
            last = self.pending[:end].split()[-1].casefold()
            if last in {'ул.', 'д.', 'г.', 'стр.', 'корп.', 'т.д.', 'т.п.'}:
                # Do not split street/house abbreviations; look for the next end.
                next_match = re.search(r'[.!?…](?=\s)', self.pending[end:])
                if next_match is None:
                    break
                end += next_match.end()
            sentence, self.pending = self.pending[:end].strip(), self.pending[end:].lstrip()
            if sentence:
                result.append(sentence)
        if final and self.pending.strip():
            result.append(self.pending.strip())
            self.pending = ''
        return result


class Chunks:
    """Experimental fragmentation, not a production semantic safety gate."""
    def __init__(self, mode):
        self.mode, self.pending = mode, ''
        self.sentences = Sentences()

    def feed(self, delta, *, final=False):
        if self.mode == 'sentence':
            return self.sentences.feed(delta, final=final)
        self.pending += delta
        result = []
        if self.mode in {'word', 'words3'}:
            count = 1 if self.mode == 'word' else 3
            while match := re.match(r'^\s*(?:\S+\s+){' + str(count) + '}', self.pending):
                result.append(match.group().strip())
                self.pending = self.pending[match.end():]
        elif self.mode == 'clause':
            while match := re.search(r'[.,;:!?…](?=\s)', self.pending):
                piece = self.pending[:match.end()]
                if piece.split()[-1].casefold() in {'ул.', 'д.', 'г.', 'стр.', 'корп.'}:
                    break
                result.append(piece.strip())
                self.pending = self.pending[match.end():].lstrip()
        if final and self.pending.strip():
            result.append(self.pending.strip())
            self.pending = ''
        return result


def voice_onset(pcm, rate):
    """First two consecutive 20ms RMS windows above -44 dBFS, not acoustic latency."""
    samples = array('h', pcm)
    window = max(1, round(rate * .02))
    previous = False
    for start in range(0, len(samples) - window + 1, window):
        active = sum(value * value for value in samples[start:start + window]) / window > 200 ** 2
        if active and previous:
            return max(0, start - window) / rate
        previous = active
    return None


def timeline(question: bytes, packets: list[tuple[float, bytes]], rate: int):
    """Packets carry availability since question end; playback cannot precede it."""
    samples = len(question) // 2
    parts = [question]
    schedule = []
    origin = samples
    for available, pcm in packets:
        if len(pcm) % 2:
            raise ValueError('Unaligned PCM')
        target = max(samples, origin + round(available * rate))
        if target > samples:
            parts.append(b'\0\0' * (target - samples))
        parts.append(pcm)
        schedule.append((target - origin) / rate)
        samples = target + len(pcm) // 2
    return b''.join(parts), schedule


async def run(args):
    import httpx
    import lameenc
    voice_root = Path(args.voice_root).resolve()
    sys.path.insert(0, str(voice_root))
    # Voice workers start a fresh interpreter with `-m app...`; sys.path changes
    # in this process alone are not inherited by those subprocesses.
    os.environ['PYTHONPATH'] = str(voice_root) + os.pathsep + os.environ.get('PYTHONPATH', '')
    from app.audio.tts import SileroTTS, shutdown_tts
    from app.audio.speech_text import for_speech
    from app.audio.formats import SAMPLE_RATE
    from app.domain.messages import VoiceStyle

    question_text = args.question
    system = SYSTEM if not args.raw_text else SYSTEM.replace(
        'Верни только JSON с одной строкой reply.', 'Говори только ответ, без JSON и разметки.')
    if args.context_tag:
        system = ('Independent test ' + args.context_tag + '. Do not mention this code.\n' + system)
    model_path = Path(args.tts_model).resolve()
    if not model_path.is_file():
        raise ValueError('Silero model file is missing')
    endpoint = urlsplit(args.url)
    if (endpoint.scheme != 'http' or endpoint.hostname not in {'127.0.0.1', 'host.docker.internal', '172.17.0.1'}
            or endpoint.port is None or endpoint.username or endpoint.password
            or endpoint.path not in {'', '/'} or endpoint.query or endpoint.fragment):
        raise ValueError('Use an explicit local or Docker-host Ollama URL')
    output = Path(args.out).resolve()
    if output.exists():
        raise ValueError('Choose a new output directory; previous recordings are preserved')
    output.mkdir(parents=True)
    options = {'num_ctx': 4096, 'num_thread': 6, 'num_gpu': 0,
               'num_predict': 120, 'temperature': 0, 'seed': 42}
    trace = {'experiment': 'text_streaming_not_sip', 'model': MODEL,
             'question': question_text, 'cpu_only': True, 'stt': False,
             'audio_clock': 'local enqueue availability, not acoustic playback', 'segments': []}
    trace.update(chunk_mode=args.chunk_mode, raw_text=args.raw_text,
                 reset_context=args.reset_context, prepared_context=args.prepare_context)
    trace['context_tag'] = args.context_tag
    operator = SileroTTS(str(model_path), 'baya', workers=1, threads=2)
    crew = SileroTTS(str(model_path), 'eugene', workers=1, threads=2)
    raw, decoded, packets, started = '', '', [], 0.0
    queue = asyncio.Queue(maxsize=8)

    async def collect(provider, text, speaker):
        return b''.join([piece async for piece in provider.synthesize_stream(
            for_speech(text), VoiceStyle(speaker=speaker))])

    async def synthesize():
        while (sentence := await queue.get()) is not None:
            segment = {'text': sentence, 'tts_requested_s': round(time.perf_counter() - started, 4)}
            async for piece in crew.synthesize_stream(for_speech(sentence), VoiceStyle(speaker='eugene')):
                available = time.perf_counter() - started
                if 'first_audio_ready_s' not in trace:
                    trace['first_audio_ready_s'] = round(available, 4)
                segment.setdefault('first_audio_ready_s', round(available, 4))
                packets.append((available, piece))
            trace['segments'].append(segment)

    try:
        async with httpx.AsyncClient(base_url=args.url, timeout=120, trust_env=False) as client:
            tags = (await client.get('/api/tags')).raise_for_status().json()
            if MODEL not in {item['name'] for item in tags['models']}:
                raise ValueError('Phone model is not installed; run ollama pull ' + MODEL)
            warm_start = time.perf_counter()
            (await client.post('/api/chat', json={'model': MODEL, 'messages': [], 'stream': False,
                'think': False, 'keep_alive': -1, 'options': options})).raise_for_status()
            active = (await client.get('/api/ps')).raise_for_status().json()['models']
            selected = next(item for item in active if item['name'] == MODEL)
            if selected['size_vram'] != 0:
                raise ValueError('CPU-only inference was not confirmed')
            trace['model_digest'] = selected['digest']
            trace['warmup_s'] = round(time.perf_counter() - warm_start, 4)
            if args.reset_context:
                # Evict this runner's last prompt prefix without unloading weights.
                # No question, answer or real user data is used in this preparation.
                reset_at = time.perf_counter()
                (await client.post('/api/chat', json={'model': MODEL,
                    'messages': [{'role': 'system', 'content':
                        'Connection diagnostic. Output one character only.'}],
                    'stream': False, 'think': False, 'keep_alive': -1,
                    'options': {**options, 'num_predict': 1}})).raise_for_status()
                trace['context_reset_s'] = round(time.perf_counter() - reset_at, 4)
            if args.prepare_context:
                # Only already-known scenario facts, never the operator's future
                # question or an expected answer. A real call can do this while
                # ringing; the actual phone protocol is not changed by this demo.
                prepared_at = time.perf_counter()
                prepared = (await client.post('/api/chat', json=
                    context_preparation_payload(options, system, args.raw_text))).raise_for_status().json()
                trace['context_preparation_s'] = round(time.perf_counter() - prepared_at, 4)
                trace['context_preparation'] = 'scenario_system_only_no_operator_question'
                trace['prepared_prompt_tokens'] = prepared.get('prompt_eval_count')
            await operator._ensure()
            question = await collect(operator, question_text, 'baya')
            # Warm the reply speaker outside the recorded response interval, as
            # gateway startup preloads synthesis. This phrase is never played.
            await collect(crew, 'Проверка готовности голоса.', 'eugene')
            # Each comparison gets warm TTS workers but no phrase-cache shortcut.
            pool = crew.pool()
            pool.cache.clear()
            pool.cache_bytes = 0
            trace['question_duration_s'] = len(question) / (2 * SAMPLE_RATE)
            started = time.perf_counter()  # request submitted at the end of the question
            worker = asyncio.create_task(synthesize())
            splitter = Chunks(args.chunk_mode)
            try:
                async with client.stream('POST', '/api/chat', json={
                    'model': MODEL, 'messages': [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': question_text}], 'stream': True, 'think': False,
                    **({} if args.raw_text else {'format': SCHEMA}), 'keep_alive': -1, 'options': options}) as response:
                    response.raise_for_status()
                    done = False
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        item = json.loads(line)
                        if item.get('error'):
                            raise ValueError('Ollama stream failed')
                        delta = item.get('message', {}).get('content', '')
                        if delta:
                            trace.setdefault('first_token_s', round(time.perf_counter() - started, 4))
                            raw += delta
                            if len(raw) > 6000:
                                raise ValueError('Reply exceeded the experiment limit')
                            prefix = raw if args.raw_text else reply_prefix(raw)[0]
                            if not prefix.startswith(decoded):
                                raise ValueError('Streamed reply changed its prefix')
                            for sentence in splitter.feed(prefix[len(decoded):]):
                                await queue.put(sentence)
                            decoded = prefix
                        if item.get('done'):
                            done = True
                            trace['llm_finished_s'] = round(time.perf_counter() - started, 4)
                            trace['output_tokens'] = item.get('eval_count')
                            trace['model_timings_s'] = {
                                key: round(item.get(key, 0) / 1e9, 4) for key in
                                ('load_duration', 'prompt_eval_duration', 'eval_duration')}
                    if not done:
                        raise ValueError('Truncated provider stream')
                value = {'reply': raw} if args.raw_text else json.loads(raw)
                if set(value) != {'reply'} or value['reply'] != decoded or not decoded.strip():
                    raise ValueError('Invalid final reply envelope')
                for sentence in splitter.feed('', final=True):
                    await queue.put(sentence)
                await queue.put(None)
                await worker
            finally:
                if not worker.done():
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
            if not packets:
                raise ValueError('No synthesized answer audio')
            pcm, schedule = timeline(question, packets, SAMPLE_RATE)
            trace['reply'] = decoded
            trace['first_audio_before_llm_finished'] = trace['first_audio_ready_s'] < trace['llm_finished_s']
            trace['first_answer_playback_s'] = round(schedule[0], 4)
            trace['recording_duration_s'] = len(pcm) / (2 * SAMPLE_RATE)
            trace['first_speech_s'] = voice_onset(pcm[len(question):], SAMPLE_RATE)
            with wave.open(str(output / 'conversation.wav'), 'wb') as target:
                target.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
                target.writeframes(pcm)
            encoder = lameenc.Encoder()
            encoder.set_bit_rate(64)
            encoder.set_in_sample_rate(SAMPLE_RATE)
            encoder.set_channels(1)
            encoder.set_quality(2)
            (output / 'conversation.mp3').write_bytes(encoder.encode(pcm) + encoder.flush())
            (output / 'timings.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(trace, ensure_ascii=False, indent=2), flush=True)
    finally:
        await shutdown_tts()


def parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:11434')
    parser.add_argument('--voice-root', default=str(ROOT / 'voice'))
    parser.add_argument('--question', default=QUESTION)
    parser.add_argument('--prepare-context', action='store_true',
                        help='Prepare known scenario facts before the question, not the question itself')
    parser.add_argument('--tts-model', default=str(ROOT / 'deploy/models/v5_5_ru.pt'))
    parser.add_argument('--out', required=True)
    parser.add_argument('--chunk-mode', choices=['full', 'sentence', 'clause', 'words3', 'word'], default='sentence')
    parser.add_argument('--raw-text', action='store_true')
    parser.add_argument('--reset-context', action='store_true')
    parser.add_argument('--context-tag', default='', help='Unique synthetic prefix key to prevent cross-test prompt reuse')
    return parser


if __name__ == '__main__':
    asyncio.run(run(parser().parse_args()))
