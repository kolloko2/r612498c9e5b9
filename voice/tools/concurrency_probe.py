"""Measure concurrent local speech pipelines with synthetic audio, without SIP.

Phone transport and dialogue replies are mock; --speech uses real local Vosk /
optional GigaAM plus Silero. Results explicitly do NOT measure RTP/VoIP delay.
"""
import argparse
import asyncio
import json
import logging
import statistics
import time
import wave
from pathlib import Path
from uuid import uuid4

from app.asterisk.call_manager import CallManager
from app.audio.formats import FRAME_BYTES, SAMPLE_RATE, SILENCE
from app.audio.providers import stop_process
from app.audio.tts import SileroTTS, shutdown_tts
from app.config import Settings
from app.domain.messages import CreateCall, VoiceStyle


async def wait(predicate, seconds):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(.05)


def events(runtime):
    path = runtime.backend.journal
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


class Metrics(logging.Handler):
    def __init__(self):
        super().__init__();self.rows=[]
    def emit(self, record):
        try:
            value=json.loads(record.getMessage())
            if value.get('event')=='turn.latency':self.rows.append(value)
        except (ValueError,TypeError):
            pass


async def main(args):
    args.output.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[2]
    config=dict(_env_file=None, telephony_mode='mock', backend_mode='mock', pipeline_mode='conversation',
        allowed_extensions=','.join(str(201+i) for i in range(args.calls)),max_calls=args.calls,
        provider_timeout_s=120,recording_dir=args.output/'recordings',outbox_dir=args.output/'outbox')
    if args.speech:
        config.update(stt_provider='hybrid' if args.hybrid else 'vosk',
            stt_model=str(root/'deploy/models/vosk-model-small-ru-0.22'),
            stt_final_model=str(root/'deploy/models/sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19'),
            tts_provider='silero',tts_voice=str(root/'deploy/models/v5_5_ru.pt'))
    manager=CallManager(Settings(**config))
    collector=Metrics();logger=logging.getLogger('voice');logger.setLevel(logging.INFO);logger.addHandler(collector)
    result={'calls_requested':args.calls,'transport':'mock phone (no SIP/RTP)', 'backend':'mock dialogue (no LLM)',
            'stt':config.get('stt_provider','mock'),'tts':config.get('tts_provider','mock'),
            'voip_latency_ms':None,'calls':[]}
    runtimes=[]
    started=time.monotonic()
    try:
        if args.speech:
            tts=SileroTTS(config['tts_voice'],'baya');pcm=bytearray()
            async with asyncio.timeout(120):
                async for data in tts.synthesize_stream('Учебная проверка связи. На Лесной улице пожар.',VoiceStyle()):pcm.extend(data)
            await tts.close()
        else:
            import numpy as np
            t=np.arange(SAMPLE_RATE)/SAMPLE_RATE
            pcm=bytearray((.2*np.sin(2*np.pi*220*t)*32767).astype('<i2').tobytes())
        pcm.extend(bytes((-len(pcm))%FRAME_BYTES))
        await manager.start()
        created=await asyncio.gather(*(manager.create(CreateCall(session_id=uuid4(),extension=str(201+i))) for i in range(args.calls)))
        runtimes=[manager.calls[item['call_id']] for item in created]
        await wait(lambda:all(r.context.ready.is_set() or r.context.done.is_set() for r in runtimes),150)
        await wait(lambda:all(r.context.done.is_set() or any(e['type']=='caller.playback' and e['payload'].get('status')=='played' for e in events(r)) for r in runtimes),180)
        await asyncio.sleep(1.6) # configured echo guard, not a measurement shortcut
        result['active_overlap']=sum(not r.context.done.is_set() for r in runtimes)
        async def feed(runtime):
            if runtime.context.done.is_set():return
            for offset in range(0,len(pcm),FRAME_BYTES):
                await runtime.mock_input.put(bytes(pcm[offset:offset+FRAME_BYTES]))
            for _ in range(50):await runtime.mock_input.put(SILENCE)
        await asyncio.gather(*(feed(r) for r in runtimes))
        await wait(lambda:all(r.context.done.is_set() or sum(e['type']=='caller.playback' and e['payload'].get('status')=='played' for e in events(r))>=2 for r in runtimes),240)
        result['completed']=True
    except Exception as error:
        result['completed']=False;result['error']=type(error).__name__+': '+str(error)
    finally:
        for runtime in runtimes:
            snapshot=await manager.hangup(runtime.context.call_id)
            journal=events(runtime)
            row={'call_id':str(runtime.context.call_id),'status':snapshot['status'],
                 'utterances':[e['payload'].get('text') for e in journal if e['type']=='operator.utterance'],
                 'errors':[e['payload'] for e in journal if e['type']=='voice.error'],
                 'session_isolated':all(str(e['session_id'])==str(runtime.context.session_id) for e in journal)}
            row['latency']=[m for m in collector.rows if m.get('call_id')==row['call_id'] and 'total_turn_latency_ms' in m]
            result['calls'].append(row)
        await manager.close()
        await shutdown_tts()
        logger.removeHandler(collector)
        result['elapsed_seconds']=round(time.monotonic()-started,2)
        result['completed'] = bool(result.get('completed') and len(result['calls']) == args.calls
            and result.get('active_overlap') == args.calls
            and all(row['status']=='ended' and row['utterances'] and not row['errors']
                    and row['session_isolated'] and row['latency'] for row in result['calls']))
        values=[m['total_turn_latency_ms'] for r in result['calls'] for m in r['latency']]
        result['turn_latency_after_vad_ms']={'samples':len(values),'median':statistics.median(values) if values else None,'max':max(values) if values else None}
        result['vad_end_ms']=manager.settings.vad_end_ms
        (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='calls'},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calls',type=int,choices=range(1,21),default=2)
    parser.add_argument('--speech',action='store_true')
    parser.add_argument('--hybrid',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
