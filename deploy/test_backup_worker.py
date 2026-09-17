import json
from datetime import datetime,timezone
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parent))
from backup_worker import BackupWorker


def test_schedule_and_manual_claim_are_durable(tmp_path,monkeypatch):
    operations=tmp_path/'operations';backups=tmp_path/'backups'
    now=datetime(2026,9,15,2,tzinfo=timezone.utc).timestamp()
    worker=BackupWorker(operations,backups,now=lambda:now)
    calls=[]
    def fake_backup():
        calls.append(1);target=backups/f'full-{len(calls)}.t112';target.write_bytes(b'encrypted')
        return target
    monkeypatch.setattr(worker,'backup',fake_backup)
    worker.run_once();worker.run_once()
    assert len(calls)==1
    assert json.loads((operations/'backup-worker-state.json').read_text())['last_scheduled_backup_date']=='2026-09-15'

    request_id=str(uuid4())
    (operations/'requests'/f'{request_id}.json').write_text(json.dumps(
        {'id':request_id,'action':'backup','service':None,'created_at':now}),encoding='utf-8')
    worker.run_once()
    result=json.loads((operations/'processed'/f'{request_id}.result.json').read_text())
    assert result['status']=='completed' and len(calls)==2
    assert (operations/'processed'/f'{request_id}.request.json').is_file()


def test_pg_dump_uses_tls_environment_and_no_secret_argument(tmp_path,monkeypatch):
    operations=tmp_path/'operations';backups=tmp_path/'backups';captured={}
    monkeypatch.setenv('POSTGRES_PASSWORD','synthetic-password')
    monkeypatch.setenv('BACKUP_ENCRYPTION_KEY','11'*32)
    monkeypatch.setenv('PGSSLMODE','verify-full')
    def runner(command,**kwargs):
        captured.update(command=command,env=kwargs['env'])
        Path(command[-1]).write_bytes(b'dump')
        return subprocess.CompletedProcess(command,0)
    worker=BackupWorker(operations,backups,now=lambda:1,runner=runner)
    monkeypatch.setattr('backup_worker.create_container',lambda *args: backups/'full-test.t112')
    worker.backup()
    assert 'synthetic-password' not in ' '.join(captured['command'])
    assert captured['env']['PGSSLMODE']=='verify-full'
    assert captured['env']['PGPASSWORD']=='synthetic-password'


def test_interrupted_claim_is_failed_not_replayed(tmp_path):
    operations=tmp_path/'operations';claims=operations/'backup-claims';claims.mkdir(parents=True)
    backups=tmp_path/'backups';backups.mkdir()
    request_id=str(uuid4())
    (claims/f'{request_id}.json').write_text(json.dumps(
        {'id':request_id,'action':'backup','created_at':1}),encoding='utf-8')
    BackupWorker(operations,backups,now=lambda:2)
    result=json.loads((operations/'processed'/f'{request_id}.result.json').read_text())
    assert result['status']=='failed' and result['error']=='worker interrupted'
    assert not (claims/f'{request_id}.json').exists()


def test_mock_mode_needs_no_database_key_or_archive(tmp_path,monkeypatch):
    monkeypatch.setenv('BACKUP_MODE','mock')
    monkeypatch.delenv('BACKUP_ENCRYPTION_KEY',raising=False)
    monkeypatch.delenv('POSTGRES_PASSWORD',raising=False)
    worker=BackupWorker(tmp_path/'operations',tmp_path/'backups',now=lambda:1)
    assert worker.backup() is None
    assert not list((tmp_path/'backups').iterdir())


def test_heartbeat_is_refreshed_while_backup_runs(tmp_path,monkeypatch):
    operations=tmp_path/'operations';backups=tmp_path/'backups'
    worker=BackupWorker(operations,backups,now=time.time,heartbeat_seconds=.01)
    monkeypatch.setattr(worker,'backup',lambda: time.sleep(.04))
    before=time.time()
    worker._backup_with_heartbeat()
    state=json.loads((operations/'backup-worker-state.json').read_text())
    assert state['heartbeat_at']>=int(before)
