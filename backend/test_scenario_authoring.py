import os
os.environ.setdefault('DIALOGUE_DB', ':memory:')
from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
import server


@pytest.fixture
def authoring(monkeypatch):
    store=server.Store(':memory:')
    monkeypatch.setattr(server,'store',store)
    monkeypatch.setattr(server,'TOKEN','synthetic-service')
    def current(token):
        if token not in ('teacher1','teacher2','student'):
            raise HTTPException(401)
        return {'id':token,'active':True,'role':'student' if token=='student' else 'teacher'}
    monkeypatch.setattr(server.accounts,'current',current)
    monkeypatch.setenv('LLM_PROVIDER','mock')
    with TestClient(server.app,headers={'Authorization':'Bearer synthetic-service','X-User-Session':'teacher1'}) as client:
        yield client,store
    store.db.close()


def test_template_copy_edit_and_cross_teacher_isolation(authoring):
    client,store=authoring
    template=client.get('/api/v1/scenarios').json()[0]
    assert template['editable'] is False
    original=store.scenario(template['id'])
    assert client.put('/api/v1/scenarios/'+template['id'],json={**original,'version':template['version']}).status_code==403
    body={**original,'id':'teacher_copy','title':'Учебная копия'}
    assert client.post('/api/v1/scenarios/validate',json=body).json()['valid']
    assert store.scenario(body['id']) is None
    assert client.post('/api/v1/scenarios',json=body).status_code==201
    assert client.get('/api/v1/scenarios/teacher_copy').json()['editable'] is True
    assert client.post('/api/v1/scenarios',json=body).status_code==409
    body['enabled']=False
    body['version']=client.get('/api/v1/scenarios/teacher_copy').json()['version']
    assert client.put('/api/v1/scenarios/teacher_copy',json=body).status_code==200
    assert store.scenario('teacher_copy')['enabled'] is False
    client.headers['X-User-Session']='teacher2'
    assert 'teacher_copy' not in [s['id'] for s in client.get('/api/v1/scenarios').json()]
    assert client.get('/api/v1/scenarios/teacher_copy').status_code==404
    assert client.put('/api/v1/scenarios/teacher_copy',json=body).status_code==403
    assert store.scenario(template['id'])==original


def test_stale_editor_cannot_overwrite_saved_changes(authoring):
    client,store=authoring
    body={**store.first_enabled(),'id':'versioned_copy'}
    created=client.post('/api/v1/scenarios',json=body).json()
    assert len(created['version'])==64
    first={**body,'title':'Правки первого окна','version':created['version']}
    saved=client.put('/api/v1/scenarios/versioned_copy',json=first)
    assert saved.status_code==200 and saved.json()['version']!=created['version']
    assert client.put('/api/v1/scenarios/versioned_copy',json={**first,'title':'Устаревшие правки'}).status_code==409
    assert store.scenario('versioned_copy')['title']=='Правки первого окна'
    assert client.put('/api/v1/scenarios/versioned_copy',json=body).status_code==422
    assert client.put('/api/v1/scenarios/versioned_copy',json={**first,'version':saved.json()['version'],'title':'После перечитывания'}).status_code==200


def test_student_cannot_read_hidden_scenario_or_author(authoring):
    client,store=authoring
    body=store.first_enabled()
    client.headers['X-User-Session']='student'
    assert client.get('/api/v1/scenarios').status_code==403
    assert client.get('/api/v1/scenarios/'+body['id']).status_code==403
    assert client.post('/api/v1/scenarios/validate',json=body).status_code==403
    assert client.post('/api/v1/scenarios',json={**body,'id':'forbidden_copy'}).status_code==403
