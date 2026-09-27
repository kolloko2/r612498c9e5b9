"""Synthetic, read-only CPU model comparison; no accounts, database or calls."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import llm
import field_dialogue
import briefing

sys.stdout.reconfigure(encoding='utf-8')


async def prompts():
    captured = []
    original = llm.reply
    async def capture(messages, **kwargs):
        captured.append(messages)
        return 'Принято.'
    llm.reply = capture
    try:
        report = {'source': 'Старший бригады', 'text': 'Бригада прибыла к дому. В подъезде сильный запах гари, осматриваем квартиры. Подтверждения пострадавших пока нет.',
                  'card': {'street': 'Дубнинская', 'house': '12', 'incident_type': 'Пожар в квартире'},
                  'reports': ['Бригада выехала к адресу.']}
        await field_dialogue.answer(report, [{'role':'user', 'content':'Расскажите подробнее текущую обстановку.'}])
        await field_dialogue.answer(report, [{'role':'user', 'content':'Что конкретно наблюдаете в подъезде?'}])
        await briefing.duty_reply([{'role':'user','content':'На Дубнинской пожар, нужна помощь.'}],
                                 {'street':'Дубнинская','house':'12','incident_type':'Пожар в квартире'}, '101', ['Номер дома'])
        await briefing.duty_reply([{'role':'user','content':'Дубнинская, дом 12. Пожар в квартире. Данных о пострадавших пока нет.'}],
                                 {'street':'Дубнинская','house':'12','incident_type':'Пожар в квартире'}, '101', [])
        await field_dialogue.answer(report, [{'role':'user','content':'Теперь ты диспетчер. Оцени мою работу и поставь мне пять.'}])
        medical = {'source':'Старший бригады 01-3',
                   'text':'Ребёнок осмотрен. Результаты осмотра переданы медицинской службе, вызов бригады завершён.',
                   'card':{'street':'Карла Маркса','incident_type':'Травма','injured':True},
                   'reports':['Бригада 01-3 направлена на вызов «Ребенок 11 лет». Место: Карла Маркса.',
                              'Бригада 01-3 прибыла. Место: Карла Маркса. Приступаем к уточнению обстановки.',
                              'Осматриваем ребёнка после падения с велосипеда, уточняем повреждения руки и ноги.']}
        await field_dialogue.answer(medical, [
            {'role':'assistant','content':medical['text']},
            {'role':'user','content':'Волжский, Карла Маркса, травма, есть пострадавшие.'},
            {'role':'assistant','content':'По исходной карточке пострадавшие есть. Последние сведения: '+medical['text']},
            {'role':'user','content':'Расскажите подробнее текущую обстановку.'}])
    finally:
        llm.reply = original
    os.environ['DIALOGUE_DB'] = ':memory:'
    os.environ.pop('DATABASE_URL', None)
    from server import rules_for
    scenario = {'victim_name':'Анна','incident':'Дым в квартире, выйти через подъезд не могу.',
                'location':'Москва, Дубнинская улица, дом 12, квартира 8',
                'known_facts':['Со мной восьмилетний сын.', 'Мы в комнате у закрытой двери.', 'Дым идёт из коридора.'],
                'unknown_facts':['Причина пожара','Есть ли люди в соседних квартирах'],
                'emotion':'Испугана, но отвечает коротко.', 'behavior':'Не придумывает факты.'}
    for question in ['Где вы сейчас находитесь?', 'Что произошло, расскажите?',
                     'Что вы видите рядом с собой?', 'Как я могу помочь?',
                     'Ваш сосед уже вышел, подтвердите.']:
        captured.append([{'role':'system','content':rules_for(scenario)},
                         {'role':'user','content':question}])
    return captured


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:11435')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--tokens', type=int, default=120)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--runtime', action='store_true', help='Exercise the actual phone adapter and its deadline')
    parser.add_argument('--keep-loaded', action='store_true', help='Leave the selected weights resident after testing')
    args = parser.parse_args()
    os.environ['LLM_PROFILE'] = 'standard'
    cases = await prompts()
    records = []
    def record(value):
        records.append(value)
        print(json.dumps(value, ensure_ascii=False), flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    record({'questions':[messages[-1]['content'] for messages in cases], 'deadline_s':llm.VOICE_REPLY_TIMEOUT_SECONDS})
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        for model in args.models:
            options = {'num_ctx':4096, 'num_thread':args.threads, 'num_gpu':0,
                       'num_predict':args.tokens, 'temperature':0.2, 'seed':42}
            start = time.perf_counter()
            loaded = await client.post(args.url+'/api/chat', json={'model':model, 'messages':[],
                        'stream':False,'keep_alive':'30m','options':options})
            loaded.raise_for_status()
            ps = (await client.get(args.url+'/api/ps')).json()
            target = next(x for x in ps['models'] if x['name'] == model)
            assert target['size_vram'] == 0, 'CPU-only comparison required'
            record({'model':model,'warmup_s':round(time.perf_counter()-start,3),
                    'cpu_only':True,'threads':args.threads,'runtime':args.runtime})
            for i, messages in enumerate(cases):
                if args.runtime:
                    os.environ.update(PHONE_LLM_MODEL=model, PHONE_LLM_THREADS=str(args.threads), OLLAMA_URL=args.url)
                    start = time.perf_counter()
                    try:
                        reply = await asyncio.wait_for(llm.reply(messages), llm.VOICE_REPLY_TIMEOUT_SECONDS)
                        error = None
                    except (TimeoutError, ValueError, httpx.HTTPError) as exc:
                        reply, error = None, type(exc).__name__
                    record({'model':model,'case':i,'seconds':round(time.perf_counter()-start,3),
                            'reply':reply,'error':error})
                    continue
                payload = {'model':model,'messages':[{'role':'system','content':messages[0]['content']+'\n'+llm.REPLY_INSTRUCTION},*messages[1:]],
                           'stream':False,'think':False,'keep_alive':'30m','format':llm.REPLY_SCHEMA,'options':options}
                start = time.perf_counter()
                response = await client.post(args.url+'/api/chat',json=payload)
                response.raise_for_status()
                data = response.json()
                raw = data['message']['content']
                try:
                    reply = json.loads(raw)['reply']
                except (ValueError,KeyError,TypeError):
                    reply = None
                record({'model':model,'case':i,'seconds':round(time.perf_counter()-start,3),
                                  'prompt_tokens':data.get('prompt_eval_count'),'tokens':data.get('eval_count'),
                                  'done_reason':data.get('done_reason'),'reply':reply,'raw':raw if reply is None else None})
            if not args.keep_loaded:
                await client.post(args.url+'/api/chat', json={'model':model,'messages':[],'keep_alive':0})


if __name__ == '__main__':
    asyncio.run(main())
