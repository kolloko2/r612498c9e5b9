import json,sys,ssl,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[2];out=root/'artifacts/video-2026-09-28'
sys.path.insert(0,str(root/'tools'))
from seed_demo import Client
c=Client('https://127.0.0.1:3000');c.context=ssl.create_default_context(cafile=str(root/'deploy/tls/ca/ca.cert.pem'));c.login('kursant1')
sid=json.loads((out/'session.json').read_text())['session_id']
session=c.call('GET','/student/sessions/'+sid)[1]
briefs=c.call('GET','/student/sessions/'+sid+'/briefings')[1]
ids=[b['call_id'] for b in briefs if b.get('call_id')]
reports=session.get('field_report_calls',{})
for item in (reports.values() if isinstance(reports,dict) else reports):
 if isinstance(item,dict) and item.get('call_id'):ids.append(item['call_id'])
ids=list(dict.fromkeys(ids))
def docker(*args):return subprocess.check_output(['docker',*args],encoding='utf8',errors='replace').strip()
code='''import json,ssl,os,urllib.request
ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE'])
h={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}
def get(p):return json.load(urllib.request.urlopen(urllib.request.Request('https://voice:8001/api/v1/'+p,headers=h),context=ctx,timeout=20))
print(json.dumps([{'call':get('calls/'+cid),'chat':get('chat/'+cid)} for cid in IDS]))
'''.replace('IDS',repr(ids))
calls=json.loads(docker('exec','trainer112-backend-1','python','-c',code))
(out/'evidence.json').write_text(json.dumps({'session':session,'briefings':briefs,'calls':calls},ensure_ascii=False,indent=2),encoding='utf8')
for i,row in enumerate(calls):
 call=row['call']
 if call['status']!='ended':continue
 for key,path in (call.get('recordings') or {}).items():
  if path:docker('cp','trainer112-voice-1:'+path,str(out/f'call-{i}-{key}.wav'))
print(json.dumps({'status':session['status'],'review':session.get('dds_review'),'calls':[{k:r['call'].get(k) for k in ['call_id','status','created_at','started_at','recordings']} for r in calls]},ensure_ascii=False))
