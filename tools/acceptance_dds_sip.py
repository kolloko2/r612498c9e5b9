"""Live, synthetic DDS acceptance using existing demo users and a TLS/SRTP phone.

Writes only a new lesson/group and normal student actions through application APIs.
Never updates database configuration or bypasses status/report gates.
"""
import argparse
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
import wave
from uuid import uuid4

from dotenv import dotenv_values
from seed_demo import Client

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('DDS_ACCEPTANCE_OUT', str(ROOT / 'artifacts' / 'dds-sip-acceptance-2026-09-26')))


def docker(*args):
    return subprocess.check_output(['docker', *args], stderr=subprocess.STDOUT, encoding='utf-8', errors='replace').strip()


def client(name):
    c = Client(os.environ.get('DDS_ACCEPTANCE_BASE_URL', 'https://127.0.0.1:3000'))
    c.context = ssl.create_default_context(cafile=str(ROOT/'deploy/tls/ca/ca.cert.pem'))
    c.login(name)
    return c


def collect(evidence, teacher, student):
    result=student.call('GET','/student/sessions/'+evidence['session_id'])[1]
    evidence['dds_review']=result.get('dds_review')
    evidence['teacher_report']=teacher.call('GET','/instructor/lessons/'+evidence['lesson_id']+'/report')[1]
    evidence['status']=result['status']
    ids=list(dict.fromkeys(evidence['calls']))
    code="""import os,json,ssl,urllib.request
ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE'])
headers={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}
def get(path):
 return json.load(urllib.request.urlopen(urllib.request.Request('https://voice:8001/api/v1/'+path,headers=headers),context=ctx,timeout=10))
results=[]
for cid in IDS:
 call=get('calls/'+cid); chat=get('chat/'+cid)
 results.append({'call_id':cid,'status':call['status'],'recordings':call.get('recordings'),
 'recognized':sum(m['status']=='recognized' for m in chat['messages']),
 'played':sum(m['status']=='played' for m in chat['messages']), 'messages':chat['messages']})
print(json.dumps(results))
""".replace('IDS',repr(ids))
    evidence['voice_evidence']=json.loads(docker('exec','trainer112-backend-1','python','-c',code))
    metrics=[]
    for line in docker('logs','--since','20m','trainer112-voice-1').splitlines():
        try:
            row=json.loads(line)
        except ValueError:
            continue
        if row.get('event')=='turn.latency' and row.get('call_id') in ids and 'total_turn_latency_ms' in row:
            metrics.append(row)
    evidence['turn_latency']=metrics
    (OUT/'result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    assert evidence['dds_review']['passed'], evidence['dds_review']
    assert len(ids)>=5 and all(c['played'] and c['status']=='ended' for c in evidence['voice_evidence'])
    print('PASS: five SIP calls, completed lesson, DDS assessment '+str(evidence['dds_review']['score_percent']),flush=True)


def main(args):
    OUT.mkdir(parents=True, exist_ok=True)
    teacher, student = client('prepod'), client('kursant1')
    if args.collect:
        collect(json.loads((OUT/'result.json').read_text(encoding='utf-8')),teacher,student)
        return
    call = lambda c, method, path, body=None: c.call(method, path, body)[1]
    me = call(student, 'GET', '/auth/me')
    item = call(teacher, 'GET', '/instructor/tickets/1')['calls'][2]
    scenario = item['scenario']; card = scenario['prefilled_card']
    spoken = ', '.join('Есть пострадавшие' if key == 'injured' else str(card[key])
        for key in scenario['dds_expectation']['brief_required_fields'] if card.get(key))
    text = spoken + ', требуется направить бригаду.'
    print('Generating synthetic operator speech', flush=True)
    print(docker('exec', 'trainer112-voice-1', 'python', '-m', 'tools.speech_smoke',
        '--tts-model', '/models/v5_5_ru.pt', '--stt-model', '/models/vosk-model-small-ru-0.22',
        '--text', text, '--wav', '/tmp/dds-acceptance-operator.wav'), flush=True)
    docker('cp', 'trainer112-voice-1:/tmp/dds-acceptance-operator.wav', str(OUT/'operator.wav'))
    followups = args.followups
    if followups:
        docker('exec', 'trainer112-voice-1', 'python', '-m', 'tools.speech_smoke',
            '--tts-model', '/models/v5_5_ru.pt', '--stt-model', '/models/vosk-model-small-ru-0.22',
            '--text', 'Расскажите подробнее текущую обстановку.', '--wav', '/tmp/dds-question.wav')
        docker('cp', 'trainer112-voice-1:/tmp/dds-question.wav', str(OUT/'question.wav'))
        with wave.open(str(OUT/'operator.wav'), 'rb') as audio:
            params, first = audio.getparams(), audio.readframes(audio.getnframes())
        with wave.open(str(OUT/'question.wav'), 'rb') as audio:
            question = audio.readframes(audio.getnframes())
        with wave.open(str(OUT/'operator.wav'), 'wb') as audio:
            audio.setparams(params)
            # This fixture tests sequential dialogue, not barge-in. Leave room
            # for the 15-second LLM budget, TTS and the playback echo guard.
            audio.writeframes(first + bytes(params.framerate * params.sampwidth * params.nchannels * 30) + question)
    env = dotenv_values(ROOT/'.env.docker')
    password = json.loads(env['SIP_ACCOUNTS_JSON'])['220']
    name = 'trainer112-dds-acceptance-' + uuid4().hex[:8]
    evidence = {'scenario_id': item['published_scenario_id'], 'calls': [], 'steps': []}
    with tempfile.TemporaryDirectory(prefix='trainer112-dds-') as temp:
        secret = Path(temp)/'password'; secret.write_text(password, encoding='utf-8')
        try:
            docker('run', '-d', '--name', name, '--network', 'container:trainer112-asterisk-1',
                '-e', 'SIP_PROBE_SECONDS=900', '-e', 'SIP_PROBE_SILENCE_SECONDS=80',
                '-e', 'SIP_PROBE_LEAD_SECONDS=20',
                '-v', f'{secret}:/run/secrets/sip220_password:ro',
                '-v', f'{ROOT / "deploy/tls/ca/ca.cert.pem"}:/ca/ca.cert.pem:ro',
                '-v', f'{OUT / "operator.wav"}:/audio/operator.wav:ro',
                '-v', f'{OUT}:/output', 'trainer112-sip220-probe:dev')
            time.sleep(3)
            if args.resume:
                evidence=json.loads((OUT/'result.json').read_text(encoding='utf-8'))
                lid=evidence['lesson_id']; sid=evidence['session_id']
                workspace=call(student,'GET','/student/sessions/'+sid)
            else:
                group = call(teacher, 'POST', '/instructor/groups', {'title':'SIP-приёмка 26.09 ' + uuid4().hex[:6]})
                call(teacher, 'POST', f'/instructor/groups/{group["id"]}/members', {'student_id':me['id']})
                lesson = call(teacher, 'POST', '/instructor/lessons', {
                    'title':'Полный цикл ДДС — проверка SIP', 'group_id':group['id'],
                    'mode':'actions', 'prefilled_scenario_ids':[item['published_scenario_id']],
                    'cards_per_student':1, 'transport':'sip', 'sip_extensions':{me['id']:'220'}})
                lid = lesson['id']; evidence['lesson_id'] = lid
                call(teacher, 'POST', f'/instructor/lessons/{lid}/start', {})
                workspace = call(student, 'POST', f'/student/lessons/{lid}/next', {})
            sid = workspace['id']; evidence['session_id'] = sid
            base = '/student/sessions/' + sid
            def status(value, comment=''):
                return call(student, 'POST', base+'/services', {'service':workspace['owner_service'],
                    'status':value,'comment':comment,'message_id':str(uuid4())})
            if not workspace.get('assigned_crew'):
                status('Принята', 'Принято в работу')
                workspace = call(student,'POST',base+'/crew',{'message_id':str(uuid4()),
                    'crew_id':workspace['crew_options'][0]['id'],'decision_by':'dispatcher'})
            crew = workspace['assigned_crew']
            evidence['steps'].append('accepted_and_crew_assigned')
            (OUT/'live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
            # Задача бригаде, затем доклад начальнику дежурной смены своей службы.
            superior_phone=workspace['card']['service_phones'][workspace['owner_service']]
            for crew_id,phone,destination in ((crew['id'],crew['phone'],crew['leader']),
                                             ('',superior_phone,'Начальник дежурной смены')):
                existing=call(student,'GET',base+'/briefings')
                brief = next((b for b in existing if b['state']=='accepted' and b.get('crew_id','')==crew_id),None) or call(student,'POST',base+'/briefings',{'message_id':str(uuid4()),
                    'service':workspace['owner_service'],'crew_id':crew_id,
                    'phone':phone,'destination':destination,'transport':'sip'})
                if brief['state']=='accepted':
                    continue
                evidence['calls'].append(brief['call_id'])
                deadline=time.monotonic()+80
                while time.monotonic()<deadline:
                    brief=next(b for b in call(student,'GET',base+'/briefings') if b['id']==brief['id'])
                    if brief['report']['complete'] and len([m for m in brief['messages'] if m['role']=='assistant'])>=2:
                        break
                    time.sleep(2)
                assert brief['report']['complete'], brief
                evidence.setdefault('briefings',[]).append(brief)
                # Do not hang up while the recipient is still speaking confirmation.
                wait_playback = """import json,os,ssl,time,urllib.request
ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE'])
h={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}
deadline=time.monotonic()+30
while time.monotonic()<deadline:
 messages=json.load(urllib.request.urlopen(urllib.request.Request('https://voice:8001/api/v1/chat/'+CID,headers=h),context=ctx,timeout=10))['messages']
 if sum(m.get('role')=='bot' and m.get('status')=='played' for m in messages)>=2:break
 time.sleep(.5)
else:raise RuntimeError('Recipient confirmation was not played')
""".replace('CID',repr(brief['call_id']))
                docker('exec','trainer112-backend-1','python','-c',wait_playback)
                call(student,'POST',base+'/briefings/'+brief['id']+'/finish',{
                    'message_id':str(uuid4()),'recipient':destination})
            print('Voice briefings to crew and superior accepted',flush=True)
            for update in scenario['updates']:
                if any(u['id']==update['id'] for u in workspace.get('situation_updates',[])):
                    continue
                deadline=time.monotonic()+170
                while time.monotonic()<deadline:
                    workspace=call(student,'POST',base+'/updates',{})
                    pending=next((p for p in workspace['pending_phone_reports'] if p['id']==update['id']),None)
                    if pending: break
                    time.sleep(2)
                assert pending, update['id']
                workspace=call(student,'POST',base+'/updates/'+update['id']+'/call',{})
                pending=next(p for p in workspace['pending_phone_reports'] if p['id']==update['id'])
                evidence['calls'].append(pending['call_id'])
                if followups:
                    check = """import json,os,ssl,time,urllib.request
ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE'])
h={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}
deadline=time.monotonic()+85
while time.monotonic()<deadline:
 chat=json.load(urllib.request.urlopen(urllib.request.Request('https://voice:8001/api/v1/chat/'+CID,headers=h),context=ctx,timeout=10))
 messages=chat['messages']
 asked=any(m.get('status')=='recognized' and 'обстанов' in m.get('text','').lower() for m in messages)
 if asked and any(m.get('status')=='played' and m.get('role')=='bot'
                  and m.get('time','') > max(q.get('time','') for q in messages
                    if q.get('status')=='recognized' and 'обстанов' in q.get('text','').lower())
                  for m in messages): break
 time.sleep(1)
else: raise RuntimeError('Follow-up was not recognized and answered: '+str(messages))
print('Follow-up STT and reply playback confirmed')
""".replace('CID', repr(pending['call_id']))
                    print(docker('exec','trainer112-backend-1','python','-c',check),flush=True)
                deadline=time.monotonic()+70
                while time.monotonic()<deadline:
                    code, result=student.call('POST',base+'/updates/'+update['id']+'/confirm',{},expect=(200,409))
                    if code==200: break
                    time.sleep(2)
                assert code==200, result
                status(update['unlocks_status'],update['text'])
                evidence['steps'].append(update['unlocks_status'])
                print(update['unlocks_status'],flush=True)
            call(student,'POST',base+'/processed',{})
            result=call(student,'POST',base+'/finish',{})
            call(teacher,'POST',f'/instructor/lessons/{lid}/finish',{'reason':'Приёмочный цикл завершён'})
            report=call(teacher,'GET',f'/instructor/lessons/{lid}/report')
            evidence.update(status=result['status'],action_report=result.get('action_report'),teacher_report=report)
            assert result['status']=='Завершена'
            collect(evidence,teacher,student)
            print(json.dumps({'status':result['status'],'action_report':result.get('action_report')},ensure_ascii=False),flush=True)
        finally:
            evidence['phone_log']=docker('logs',name)
            (OUT/'result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
            docker('rm','-f',name)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--followups', action='store_true', help='Ask follow-up questions in each field report call')
    parser.add_argument('--resume', action='store_true', help='Continue an existing acceptance lesson in DDS_ACCEPTANCE_OUT')
    parser.add_argument('--collect', action='store_true', help='Read back evidence for the existing acceptance lesson')
    main(parser.parse_args())
