import time
from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings

def test_external_api_auth_replay_idempotency_and_roles(tmp_path):
    cfg = Settings(_env_file=None, pipeline_mode='conversation', backend_mode='websocket',
                   backend_url='ws://127.0.0.1:1', api_token='test-key',
                   recording_dir=tmp_path/'rec', outbox_dir=tmp_path/'out')
    prefix='/api/v1/integration'
    with TestClient(create_app(cfg)) as c:
        payload={'session_id':str(uuid4())}
        assert c.post(prefix+'/calls',json=payload).status_code==401
        c.headers['Authorization']='Bearer test-key'
        r=c.post(prefix+'/calls',json=payload);assert r.status_code==201
        cid=r.json()['call_id'];url=prefix+'/calls/'+cid
        deadline=time.monotonic()+5
        while c.get(url).json()['status']!='active':
            assert time.monotonic()<deadline
            time.sleep(.01)
        assert c.get(url).json()['messages']==[] # No old bot, even with unreachable configured backend.
        msg={'message_id':str(uuid4()),'text':'Help'}
        assert c.post(url+'/speak',json=msg).status_code==202
        assert c.post(url+'/speak',json=msg).json()['duplicate']
        assert c.post(url+'/speak',json={**msg,'text':'Changed'}).status_code==409
        deadline=time.monotonic()+5
        while c.get(url).json()['messages'][0]['status']!='played':
            assert time.monotonic()<deadline
            time.sleep(.02)
        assert c.get(url).json()['messages'][0]['role']=='victim'
        events=c.get(url+'/events?follow=false').json()['events']
        assert any(e['type']=='playback.played' for e in events)
        cursor=events[-1]['event_id']
        assert c.get(url+'/events?follow=false&after='+cursor).json()['events']==[]
        assert c.post(url+'/stop',json={}).status_code==200
        assert c.post(url+'/hangup',json={}).status_code==200
        assert c.get(url).json()['status']=='ended'
        assert c.post(url+'/speak',json=msg).status_code==409
        assert c.get(url+'/events?follow=false&after='+cursor).json()['events']
    with TestClient(create_app(cfg)) as c:
        c.headers['Authorization']='Bearer test-key'
        assert c.get(url).json()['messages'][0]['role']=='victim'
        assert c.get(url+'/events?follow=false').json()['events']

def test_restart_event_and_atomic_state(tmp_path):
    from app.chat import ChatStore
    from app.domain.call import CallContext
    from uuid import uuid4
    store=ChatStore(tmp_path/'chat.db');ctx=CallContext(uuid4(),'201')
    store.create(ctx,'external')
    cid=str(ctx.call_id)
    before=store.get(cid)
    original=store.event
    def failure(*args):raise RuntimeError('simulated event failure')
    store.event=failure
    import pytest
    with pytest.raises(RuntimeError):store.update(cid,status='active')
    assert store.get(cid)==before
    store.event=original
    store.update(cid,status='active')
    cursor=store.events(cid)[-1]['event_id']
    reopened=ChatStore(tmp_path/'chat.db')
    assert reopened.get(cid)['status']=='failed'
    assert reopened.events(cid,int(cursor))[-1]['payload']['reason']=='service_restart'

def test_manual_mode_does_not_require_dialogue_server(tmp_path):
    cfg=Settings(_env_file=None,pipeline_mode='conversation',backend_mode='websocket',
                 backend_url='ws://127.0.0.1:1',recording_dir=tmp_path/'rec',outbox_dir=tmp_path/'out')
    with TestClient(create_app(cfg)) as c:
        result=c.post('/api/v1/calls',json={'session_id':str(uuid4()),'mode':'manual'})
        cid=result.json()['call_id'];deadline=time.monotonic()+3
        while c.get('/api/v1/calls/'+cid).json()['status']!='active':
            assert time.monotonic()<deadline
            time.sleep(.01)
        assert c.get('/api/v1/chat/'+cid).json()['messages']==[]
        assert c.post('/api/v1/calls/'+cid+'/hangup').json()['status']=='ended'
        schema=c.get('/openapi.json').json()
        assert 'HTTPBearer' in schema['components']['securitySchemes']
