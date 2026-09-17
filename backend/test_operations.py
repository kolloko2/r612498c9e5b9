import json
import sqlite3
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from accounts import Accounts
from operations import Operation, Operations, Settings, router


def test_bridge_freshness_allowlist_and_single_pending(tmp_path):
    ops = Operations(tmp_path)
    assert ops.snapshot()['stale']
    with pytest.raises(HTTPException):
        ops.submit(Operation(action='backup'), {'id': 'admin'})
    ops.write('status.json', {'sampled_at': time.time()})
    job = ops.submit(Operation(action='backup'), {'id': 'admin'})
    assert job['status'] == 'queued'
    assert json.loads((tmp_path/'requests'/f"{job['id']}.json").read_text())['actor_id'] == 'admin'
    with pytest.raises(HTTPException) as error:
        ops.submit(Operation(action='stop', service='voice'), {'id': 'admin'})
    assert error.value.status_code == 409
    for value in ({'action': 'exec'}, {'action': 'stop', 'service': 'postgres'},
                  {'action': 'backup', 'service': 'voice'}, {'action': 'stop'}):
        with pytest.raises(ValidationError):
            Operation(**value)
    with pytest.raises(ValidationError):
        Settings(backup_hour_utc=24)


def test_admin_only_and_settings_audit(tmp_path, monkeypatch):
    monkeypatch.setenv('OPERATIONS_DIR', str(tmp_path))
    store = type('Store', (), {'db': sqlite3.connect(':memory:', check_same_thread=False)})()
    accounts = Accounts(store)
    app = FastAPI()
    app.include_router(accounts.router(lambda: None))
    app.include_router(router(accounts, lambda: None))
    client = TestClient(app)
    admin = accounts.bootstrap('admin', 'a-long-test-password', 'Admin')
    headers = {'X-User-Session': admin['session_token']}
    accounts.create_user('student', 'a-long-test-password', 'Student', 'student')
    student = accounts.login('student', 'a-long-test-password')
    url = '/api/v1/admin/operations'
    assert client.get(url).status_code == 401
    assert client.get(url, headers={'X-User-Session': student['session_token']}).status_code == 403
    assert client.post(url+'/jobs', json={'action':'backup'}, headers={
        'X-User-Session': student['session_token']}).status_code == 403
    assert client.get(url, headers=headers).status_code == 200
    assert client.patch(url+'/settings', headers=headers,
                        json={'backup_enabled': True, 'backup_hour_utc': 3}).status_code == 200
    assert list((tmp_path/'settings-audit').glob('*.json'))


def test_container_backup_remains_available_without_host_status(tmp_path):
    ops = Operations(tmp_path)
    ops.write('backup-worker-state.json', {'heartbeat_at': time.time(), 'executor': 'container',
        'backups': [{'name': 'full-test.t112', 'status': 'complete'}],
        'last_scheduled_backup': {'status': 'completed'}})
    result = {'id': '00000000-0000-4000-8000-000000000001', 'action': 'backup',
              'service': None, 'status': 'completed', 'at': time.time(), 'error': None}
    ops.write('processed/' + result['id'] + '.result.json', result)
    snapshot = ops.snapshot()
    assert snapshot['stale'] and snapshot['backup_available']
    assert snapshot['status']['backups'][0]['name'] == 'full-test.t112'
    assert snapshot['jobs'] == [result]
    queued = ops.submit(Operation(action='backup'), {'id': 'admin'})
    assert queued['status'] == 'queued'
    claim = tmp_path/'backup-claims'/f"{queued['id']}.json"
    claim.parent.mkdir();(tmp_path/'requests'/f"{queued['id']}.json").replace(claim)
    with pytest.raises(HTTPException) as error:
        ops.submit(Operation(action='backup'), {'id': 'admin'})
    assert error.value.status_code == 409
