"""Eight real CPU audio experiments. Does not change the live phone protocol."""
import argparse
import asyncio
import json
import secrets
from pathlib import Path

import streaming_phone_demo as demo

VARIANTS = [
    ('01_full_new', 'full', False, False),
    ('02_sentence_new', 'sentence', False, False),
    ('03_sentence_prepared', 'sentence', True, False),
    ('04_clause_prepared', 'clause', True, False),
    ('05_words3_prepared', 'words3', True, False),
    ('06_word_prepared', 'word', True, False),
    ('07_raw_clause_prepared', 'clause', True, True),
    ('08_raw_word_prepared', 'word', True, True),
]


async def main(args):
    root = Path(args.out).resolve()
    if root.exists():
        raise ValueError('Use a new output directory to preserve previous comparisons')
    root.mkdir(parents=True)
    summary = []
    tag_base = str(secrets.randbelow(90000) + 10000)
    for index, (name, mode, prepare, raw) in enumerate(VARIANTS):
        print('START ' + name, flush=True)
        argv = ['--url', args.url, '--voice-root', args.voice_root,
                '--tts-model', args.tts_model, '--out', str(root / name),
                '--chunk-mode', mode, '--reset-context', '--context-tag', tag_base + str(index)]
        if prepare:
            argv.append('--prepare-context')
        if raw:
            argv.append('--raw-text')
        try:
            await demo.run(demo.parser().parse_args(argv))
            trace = json.loads((root / name / 'timings.json').read_text())
            summary.append({'variant': name, **{key: trace.get(key) for key in (
                'first_token_s', 'first_audio_ready_s', 'first_speech_s', 'llm_finished_s',
                'context_preparation_s', 'model_timings_s', 'reply', 'recording_duration_s')}})
        except Exception as error:
            summary.append({'variant': name, 'error': type(error).__name__ + ': ' + str(error)[:300]})
        (root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print('FINISH ' + json.dumps(summary[-1], ensure_ascii=False), flush=True)
    if any('error' in row for row in summary):
        raise RuntimeError('Some variants failed; partial results were preserved')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--url', default='http://172.17.0.1:11434')
    parser.add_argument('--voice-root', default='/srv/voice')
    parser.add_argument('--tts-model', default='/models/v5_5_ru.pt')
    asyncio.run(main(parser.parse_args()))
