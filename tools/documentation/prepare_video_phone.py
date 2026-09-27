"""Isolated recording phone; never connects to Windows sound devices."""
import json,sys,subprocess,wave,ssl
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from seed_demo import Client
root=Path(__file__).resolve().parents[2]
out=root/'artifacts/video-2026-09-28';out.mkdir(parents=True,exist_ok=True)
def docker(*args):
 return subprocess.check_output(['docker',*args],stderr=subprocess.STDOUT,encoding='utf8',errors='replace').strip()
teacher=Client('https://127.0.0.1:3000')
teacher.context=ssl.create_default_context(cafile=str(root/'deploy/tls/ca/ca.cert.pem'))
teacher.login('prepod')
item=teacher.call('GET','/instructor/tickets/1')[1]['calls'][2]
scenario=item['scenario'];card=scenario['prefilled_card']
spoken=', '.join('Есть пострадавшие' if k=='injured' else str(card[k]) for k in scenario['dds_expectation']['brief_required_fields'] if card.get(k))
for name,text in [('brief',spoken+', требуется направить бригаду.'),('question','Доложите, пожалуйста, текущую обстановку. Есть ли опасность для людей?')]:
 docker('exec','trainer112-voice-1','python','-m','tools.speech_smoke','--tts-model','/models/v5_5_ru.pt','--stt-model','/models/vosk-model-small-ru-0.22','--text',text,'--wav','/tmp/video-'+name+'.wav')
 docker('cp','trainer112-voice-1:/tmp/video-'+name+'.wav',str(out/(name+'.wav')))
 with wave.open(str(out/(name+'.wav')),'rb') as w: params=w.getparams();data=w.readframes(w.getnframes())
 with wave.open(str(out/(name+'-padded.wav')),'wb') as w:
  w.setparams(params);w.writeframes(bytes(params.framerate*params.sampwidth*params.nchannels*20)+data+bytes(params.framerate*params.sampwidth*params.nchannels*90))
(out/'scenario.json').write_text(json.dumps({'id':item['published_scenario_id'],'scenario':scenario},ensure_ascii=False,indent=2),encoding='utf8')
line=next(x.partition('=')[2].strip().strip("'\"") for x in (root/'.env.docker').read_text(encoding='utf8').splitlines() if x.startswith('SIP_ACCOUNTS_JSON='))
(out/'sip.secret').write_text(json.loads(line)['220'],encoding='utf8')
print('Prepared SIP 220 audio and scenario; no Windows audio access.')
