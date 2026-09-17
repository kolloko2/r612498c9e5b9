import json
import sqlite3
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
import certificates


def test_export_requires_owned_completed_passing_attempt(monkeypatch):
    db=sqlite3.connect(':memory:',check_same_thread=False)
    db.execute('CREATE TABLE workspace(id TEXT PRIMARY KEY, body TEXT)')
    store=type('Store',(),{'db':db})()
    user={'id':'student','role':'student'}
    accounts=type('Accounts',(),{'require':lambda self,role:lambda:user,
                               'get_user':lambda self,uid:{'display_name':'Учебный пользователь'}})()
    learning=type('Learning',(),{'owns_session':lambda self,u,v:u['id']==v.get('student_id')})()
    monkeypatch.setattr(certificates,'expert_history',lambda *args:{'current':None})
    monkeypatch.setattr(certificates,'certificate_pdf',lambda *args:b'%PDF-1.4\nsynthetic')
    sid=str(uuid4());value={'student_id':'student','status':'В работе','policy_result':{'passed':True}}
    def save():
        db.execute('INSERT OR REPLACE INTO workspace VALUES (?,?)',(sid,json.dumps(value)));db.commit()
    app=FastAPI();app.include_router(certificates.router(store,accounts,learning,lambda:None));client=TestClient(app)
    path='/api/v1/student/sessions/'+sid+'/certificate'
    save();assert client.get(path).status_code==409
    value['status']='Завершена';value['policy_result']['passed']=False;save();assert client.get(path).status_code==409
    value['policy_result']['passed']=True;save();response=client.get(path);assert response.status_code==200
    assert response.json()['file_base64'].startswith('JVBER')
    value['student_id']='another';save();assert client.get(path).status_code==404
