"""Two real TLS/SRTP phones, separate students and local speech/LLM providers."""
import json
from pathlib import Path
import tempfile
import time
from uuid import uuid4
from dotenv import dotenv_values
from acceptance_dds_sip import ROOT, client, docker

OUT = ROOT / 'artifacts' / ('parallel-sip-' + time.strftime('%Y%m%d-%H%M%S'))


def voice_snapshot(ids):
    code = '''import json,os,ssl,urllib.request
ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE'])
h={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}
def get(path):
 return json.load(urllib.request.urlopen(urllib.request.Request('https://voice:8001/api/v1/'+path,headers=h),context=ctx,timeout=10))
print(json.dumps([{'call':get('calls/'+cid),'chat':get('chat/'+cid)} for cid in IDS]))
'''.replace('IDS', repr(ids))
    return json.loads(docker('exec','trainer112-backend-1','python','-c',code))


def main():
    OUT.mkdir(parents=True)
    teacher = client('prepod')
    students = [client('kursant1'), client('kursant2')]
    api = lambda c,m,p,b=None: c.call(m,p,b)[1]
    users = [api(s,'GET','/auth/me') for s in students]
    extensions = ['218','219']
    contacts = docker('exec','trainer112-asterisk-1','asterisk','-rx','pjsip show contacts')
    assert not any(ext+'/' in contacts for ext in extensions), 'Probe extensions are already in use'
    accounts = json.loads(dotenv_values(ROOT/'.env.docker')['SIP_ACCOUNTS_JSON'])
    names, evidence, lid = [], {'transport':'real TLS/SRTP', 'students':2, 'calls':[]}, None
    with tempfile.TemporaryDirectory(prefix='dds-parallel-') as temp:
        try:
            for ext in extensions:
                secret=Path(temp)/ext; secret.write_text(accounts[ext],encoding='utf-8')
                output=OUT/ext; output.mkdir()
                name='trainer112-parallel-'+uuid4().hex[:8]; names.append(name)
                docker('run','-d','--name',name,'--network','container:trainer112-asterisk-1',
                       '-e','SIP_PROBE_EXTENSION='+ext,'-e','SIP_PROBE_PORT='+str(5300+int(ext)*2),
                       '-e','SIP_PROBE_SECONDS=240','-e','SIP_PROBE_SILENCE_SECONDS=100',
                       '-e','SIP_PROBE_LEAD_SECONDS=25',
                       '-v',f'{secret}:/run/secrets/sip220_password:ro',
                       '-v',f'{ROOT / "deploy/tls/ca/ca.cert.pem"}:/ca/ca.cert.pem:ro',
                       '-v',f'{ROOT / "artifacts/dds-followups-2026-09-27/question.wav"}:/audio/operator.wav:ro',
                       '-v',f'{output}:/output','trainer112-sip220-probe:dev')
            time.sleep(3)
            group=api(teacher,'POST','/instructor/groups',{'title':'Параллельная SIP-проверка '+uuid4().hex[:6]})
            for user in users:
                api(teacher,'POST',f'/instructor/groups/{group["id"]}/members',{'student_id':user['id']})
            scenario=api(teacher,'GET','/instructor/tickets/1')['calls'][2]['published_scenario_id']
            lesson=api(teacher,'POST','/instructor/lessons',{'title':'Две параллельные голосовые линии',
                'group_id':group['id'],'mode':'actions','prefilled_scenario_ids':[scenario],
                'cards_per_student':1,'transport':'sip','sip_extensions':dict(zip([u['id'] for u in users],extensions))})
            lid=lesson['id']; evidence['lesson_id']=lid
            api(teacher,'POST',f'/instructor/lessons/{lid}/start',{})
            work=[]
            for student in students:
                w=api(student,'POST',f'/student/lessons/{lid}/next',{})
                base='/student/sessions/'+w['id']
                api(student,'POST',base+'/services',{'service':w['owner_service'],'status':'Принята','comment':'Принято в работу','message_id':str(uuid4())})
                api(student,'POST',base+'/crew',{'crew_id':w['crew_options'][0]['id'],'decision_by':'dispatcher','message_id':str(uuid4())})
                work.append(base)
            deadline=time.monotonic()+100
            while time.monotonic()<deadline:
                pending=[api(s,'POST',base+'/updates',{})['pending_phone_reports'] for s,base in zip(students,work)]
                if all(pending):break
                time.sleep(1)
            assert all(pending)
            started=time.monotonic()
            for s,base,reports in zip(students,work,pending):
                response=api(s,'POST',base+'/updates/'+reports[0]['id']+'/call',{})
                evidence['calls'].append(response['pending_phone_reports'][0]['call_id'])
            overlap=False
            while time.monotonic()-started<110:
                snapshots=voice_snapshot(evidence['calls'])
                overlap |= all(row['call']['status']=='active' for row in snapshots)
                if all(sum(m.get('status')=='played' for m in row['chat']['messages'])>=2
                       and any(m.get('status')=='recognized' and 'обстанов' in m.get('text','').lower()
                               for m in row['chat']['messages']) for row in snapshots):break
                time.sleep(2)
            else:raise RuntimeError('Parallel dialogue did not complete')
            evidence.update(overlap=overlap,elapsed_seconds=round(time.monotonic()-started,2),snapshots=snapshots)
            assert overlap
            evidence['passed']=True
            print('PASS: two simultaneous real SIP dialogues, '+str(evidence['elapsed_seconds'])+' seconds',flush=True)
        finally:
            if lid:
                try:
                    api(teacher,'POST',f'/instructor/lessons/{lid}/finish',{'reason':'Проверка параллельных линий завершена, это не оценочная попытка'})
                except Exception as error:
                    evidence['cleanup_error']=type(error).__name__
            for name in names:
                evidence.setdefault('phone_logs',[]).append(docker('logs',name))
                docker('rm','-f',name)
            (OUT/'result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
