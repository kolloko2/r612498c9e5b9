import gzip
import json
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from security_audit import AuditLog, AuditMiddleware
from accounts import Accounts


def test_metadata_only_and_denied_request(tmp_path):
    audit=AuditLog(tmp_path)
    app=FastAPI()
    app.add_middleware(AuditMiddleware,audit=audit)
    @app.post('/items/{item_id}')
    def denied(item_id: str):
        raise HTTPException(403)
    with TestClient(app) as client:
        assert client.post('/items/secret-path?password=secret-query',json={'password':'secret-body'},headers={'Authorization':'Bearer secret-header'}).status_code==403
    data=audit.list()
    encoded=json.dumps(data)
    assert 'secret-' not in encoded
    assert data['events'][-1]['route']=='/items/{item_id}'
    assert data['events'][-1]['status']==403
    assert len(data['events'])==2
    assert data['minimum_retention_days']>=183


def test_archive_preserves_original(tmp_path):
    original=tmp_path/'2020-01-01.jsonl'
    original.write_text('{"phase":"finished"}\n',encoding='utf-8')
    audit=AuditLog(tmp_path)
    audit.archive()
    audit.archive()
    assert original.exists()
    with gzip.open(tmp_path/'2020-01-01.jsonl.gz','rt') as archive:
        assert archive.read()==original.read_text()


def test_fail_closed_before_mutation(tmp_path,monkeypatch):
    audit=AuditLog(tmp_path)
    def fail(_):raise OSError('disk unavailable')
    monkeypatch.setattr(audit,'append',fail)
    app=FastAPI()
    app.add_middleware(AuditMiddleware,audit=audit)
    touched=[]
    @app.post('/mutate')
    def mutate():touched.append(True)
    assert TestClient(app).post('/mutate').status_code==503
    assert not touched


def test_identity_from_legacy_current_in_threadpool(tmp_path):
    accounts=Accounts(type('Store',(),{'db':sqlite3.connect(':memory:',check_same_thread=False)})())
    admin=accounts.bootstrap('admin','long-test-password','Admin')
    audit=AuditLog(tmp_path)
    app=FastAPI()
    app.add_middleware(AuditMiddleware,audit=audit)
    @app.get('/legacy')
    def legacy():
        return accounts.current(admin['session_token'])
    assert TestClient(app).get('/legacy').status_code==200
    assert audit.list()['events'][-1]['actor_id']==admin['user']['id']


def test_websocket_metadata_not_payload(tmp_path):
    from fastapi import WebSocket
    audit=AuditLog(tmp_path);app=FastAPI();app.add_middleware(AuditMiddleware,audit=audit)
    @app.websocket('/ws/{sid}')
    async def websocket(ws:WebSocket,sid:str):
        ws.scope.setdefault('state',{})['audit_actor']={'id':'service:voice','role':'service'}
        await ws.accept();await ws.receive_text();await ws.send_text('sensitive response');await ws.close()
    with TestClient(app).websocket_connect('/ws/private-id?token=private-token') as ws:
        ws.send_text('sensitive request');assert ws.receive_text()=='sensitive response'
    records=audit.list()['events'];last=records[-1]
    assert last['method']=='WS' and last['route']=='/ws/{sid}'
    assert last['actor_id']=='service:voice' and last['received_messages']==1
    assert last['sent_messages']==1 and 'sensitive' not in str(records)
    assert 'private-token' not in str(records)


def test_group_commit_keeps_every_concurrent_record(tmp_path):
    """One fsync may serve several writers, but no record may be dropped."""
    import threading
    audit = AuditLog(tmp_path)
    writers, per_writer = 12, 25

    def write(index):
        for number in range(per_writer):
            audit.append({'request_id': f'{index}-{number}', 'phase': 'started'})

    threads = [threading.Thread(target=write, args=(index,)) for index in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    audit.close()

    lines = [json.loads(line) for line in
             next(tmp_path.glob('*.jsonl')).read_text(encoding='utf-8').splitlines()]
    assert len(lines) == writers * per_writer
    assert len({line['request_id'] for line in lines}) == writers * per_writer
    assert audit.failed is False


def test_group_commit_fails_every_writer_in_a_broken_group(tmp_path, monkeypatch):
    """A failed fsync must not be reported as a durable record to any caller."""
    import pytest
    audit = AuditLog(tmp_path)
    audit.append({'request_id': 'first', 'phase': 'started'})

    def broken(_batch):
        raise OSError('disk unavailable')

    monkeypatch.setattr(audit, '_write', broken)
    with pytest.raises(OSError):
        audit.append({'request_id': 'second', 'phase': 'started'})
    assert audit.failed is True

    monkeypatch.undo()
    # A later record still succeeds and clears the failed flag.
    audit.append({'request_id': 'third', 'phase': 'started'})
    audit.close()
    assert audit.failed is False
    identifiers = [json.loads(line)['request_id'] for line in
                   next(tmp_path.glob('*.jsonl')).read_text(encoding='utf-8').splitlines()]
    assert identifiers == ['first', 'third']
