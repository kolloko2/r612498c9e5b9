"""Read-only sequential Ollama timing probes with synthetic training facts.

Does not unload models, change the service, or place calls. Run on the server
beside streaming_phone_demo.py. Results separate model load, prompt evaluation,
generation and unaccounted scheduling/transport time.
"""
import json
import time
import urllib.request

from streaming_phone_demo import MODEL, QUESTION, SYSTEM, SCHEMA

URL = 'http://172.17.0.1:11434'
OPTIONS = {'num_ctx': 4096, 'num_thread': 6, 'num_gpu': 0,
           'num_predict': 120, 'temperature': 0, 'seed': 42}


def probe(label, *, stream=True, structured=True, system=SYSTEM):
    body = {'model': MODEL, 'messages': [{'role': 'system', 'content': system},
            {'role': 'user', 'content': QUESTION}], 'stream': stream,
            'think': False, 'keep_alive': -1, 'options': OPTIONS}
    if structured:
        body['format'] = SCHEMA
    started = time.perf_counter()
    request = urllib.request.Request(URL + '/api/chat',
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    first = None
    text = ''
    with urllib.request.urlopen(request, timeout=120) as response:
        if stream:
            for line in response:
                item = json.loads(line)
                delta = item.get('message', {}).get('content', '')
                if delta:
                    if first is None:
                        first = time.perf_counter() - started
                    text += delta
                if item.get('done'):
                    final = item
        else:
            final = json.load(response)
            text = final.get('message', {}).get('content', '')
    elapsed = time.perf_counter() - started
    result = {'label': label, 'stream': stream, 'structured': structured,
              'first_token_s': round(first, 4) if first is not None else None,
              'wall_s': round(elapsed, 4), 'reply': text}
    for name in ('total_duration', 'load_duration', 'prompt_eval_duration', 'eval_duration'):
        result[name.replace('_duration', '_s')] = round(final.get(name, 0) / 1e9, 4)
    for name in ('prompt_eval_count', 'eval_count', 'done_reason'):
        result[name] = final.get(name)
    result['unaccounted_s'] = round(result['total_s'] - result['load_s'] -
                                    result['prompt_eval_s'] - result['eval_s'], 4)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    probe('same_request_first')
    probe('same_request_nonstream', stream=False)
    probe('same_request_stream_again')
    probe('same_request_plain', structured=False)
    probe('same_request_plain_again', structured=False)
    short = ('Ты старший бригады. Ответь кратко по-русски, без JSON. '
             'Факты: в подвале вода, протечка ограждена, обследуем трубу. '
             'Пострадавших нет. Срок завершения неизвестен. Не выдумывай факты.')
    probe('short_prompt_first', structured=False, system=short)
    probe('short_prompt_repeat', structured=False, system=short)
