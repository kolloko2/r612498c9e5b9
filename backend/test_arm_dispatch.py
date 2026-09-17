import json
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server import Store
from accounts import Accounts
from arm_dispatch import router


def test_teacher_recipient_config_delivery_and_isolation():
    store=Store(':memory:');store.db.execute('CREATE TABLE workspace(id TEXT PRIMARY KEY,body TEXT)')
    accounts=Accounts(store)
    users={}
    for name,role in [('student','student'),('teacher','teacher'),('other','teacher')]:
        users[name]=accounts.create_user(name,'synthetic-test-password',name,role)
    app=FastAPI();app.include_router(router(store,accounts,lambda:None));client=TestClient(app)
    headers={name:{'X-User-Session':accounts.login(name,'synthetic-test-password')['session_token']} for name in users}
    sid=str(uuid4());value={'student_id':users['student']['id'],'teacher_id':users['teacher']['id'],
        'assignment_id':'assignment','group_id':'group','registered_at':'2026-09-15','revision':1,
        'dds_profile':'fire','card':{'services':['Служба 101'],'description':'Учебное событие'}}
    store.db.execute('INSERT INTO workspace VALUES (?,?)',(sid,json.dumps(value)));store.db.commit()
    url='/api/v1/instructor/dds/profiles/fire/recipients'
    assert client.put(url,headers=headers['teacher'],json={'revision':0,'recipients':['Учебный информационный центр']}).status_code==200
    assert client.put(url,headers=headers['teacher'],json={'revision':0,'recipients':[]}).status_code==409
    path=f'/api/v1/student/sessions/{sid}/vis-deliveries';body={'message_id':str(uuid4()),'informational_recipients':[]}
    delivered=client.post(path,headers=headers['student'],json=body);assert delivered.status_code==201
    data=delivered.json();assert data['informational_recipient_count']==1 and 'informational_recipients' not in data
    assert client.post(path,headers=headers['student'],json=body).json()['id']==data['id']
    detail=f"/api/v1/instructor/dds/incoming/{data['id']}?profile=fire"
    assert client.get(detail,headers=headers['other']).status_code==404
    assert client.get(detail,headers=headers['teacher']).json()['informational_recipients']==['Учебный информационный центр']
