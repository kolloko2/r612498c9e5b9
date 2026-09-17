"""Create one explicitly synthetic linked learner; no password is printed."""
import json
from pathlib import Path
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
CODE = '''
import json,sys,secrets,os
import httpx
from server import accounts
data=json.load(sys.stdin)
with accounts._lock:
    row=accounts._user_row(username='ldap-demo')
    if row:
        user=accounts._public(row)
        link=accounts.db.execute('SELECT directory_username FROM directory_links WHERE user_id=?',(user['id'],)).fetchone()
        if user['role']!='student' or not link or link[0]!='trainer.test':
            raise SystemExit('Existing learner is not this fixture; left unchanged')
    else:
        user=accounts.create_user('ldap-demo',secrets.token_urlsafe(40),'Учебный пользователь LDAP','student')
        with accounts.db:
            accounts.db.execute('INSERT INTO directory_links VALUES (?,?)',(user['id'],'trainer.test'))
            accounts._audit('directory.demo.created',user['id'])
with httpx.Client(trust_env=False,timeout=30) as client:
    response=client.post('http://127.0.0.1:8000/api/v1/auth/directory-login',headers={'Authorization':'Bearer '+os.environ['DIALOGUE_TOKEN']},json={'username':'trainer.test','password':data['password']})
    if response.status_code!=200:
        raise SystemExit('Synthetic directory login failed; no secrets printed')
    result=response.json()
    print(json.dumps({'directory_username':'trainer.test','local_username':result['user']['username'],'role':result['user']['role'],'login':'successful'}))
    client.post('http://127.0.0.1:8000/api/v1/auth/logout',headers={'Authorization':'Bearer '+os.environ['DIALOGUE_TOKEN'],'X-User-Session':result['session_token']})
'''

if __name__ == '__main__':
    values=dotenv_values(ROOT/'deploy/directory/private/test.env')
    password=values.get('DIRECTORY_TEST_PASSWORD')
    if not password:
        raise SystemExit('Generate private directory test ENV first')
    command=['docker','compose','--env-file',str(ROOT/'.env.docker'),'-f',str(ROOT/'docker-compose.yml'),
             'exec','-T','backend','python','-c',CODE]
    result=subprocess.run(command,input=json.dumps({'password':password}),text=True,encoding='utf-8',
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,cwd=ROOT)
    if result.returncode:
        raise SystemExit('Synthetic directory setup/login failed; inspect service health (credentials not printed)')
    print(result.stdout.strip())
