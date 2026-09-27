import json
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_worker import OperationsWorker, SingleInstanceLock  # noqa: E402


@pytest.mark.skipif(os.name != 'posix' or getattr(os, 'geteuid', lambda: 1)() != 0,
                    reason='requires a Linux root worker')
def test_root_snapshot_keeps_shared_directory_owner(tmp_path):
    from ops_worker import _atomic_json
    folder = tmp_path / 'shared'
    folder.mkdir()
    os.chown(folder, 10001, 10001)
    target = folder / 'status.json'
    _atomic_json(target, {'sampled_at': 1})
    assert target.stat().st_uid == 10001
    assert target.stat().st_gid == 10001
    assert target.stat().st_mode & 0o777 == 0o600


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "deploy" / "operations" / "requests").mkdir(parents=True)
    (tmp_path / "deploy" / "backups").mkdir(parents=True)
    (tmp_path / ".env.docker").write_text(
        "SIP_BIND_ADDRESS=10.0.0.2\nARI_PASSWORD=do-not-publish\nPIPELINE_MODE=spike\n",
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return tmp_path


def mock_ops(root: Path) -> Path:
    return root / "deploy/operations/mock"


def test_cluster_status_does_not_hide_failed_replica(tmp_path):
    root = make_root(tmp_path)
    (root / 'deploy/operations/deployment-profile.json').write_text(
        json.dumps({'directory': True, 'cluster': True}), encoding='utf-8')
    rows = [{'Service': 'backend', 'State': 'running', 'Health': 'healthy'},
            {'Service': 'backend', 'State': 'running', 'Health': 'unhealthy'}]
    worker = OperationsWorker(root=root, runner=lambda cmd, **kw:
        subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(rows)))
    services = {item['name']: item for item in worker._services()}
    assert services['backend']['health'] == 'unhealthy'
    assert services['backend']['healthy_replicas'] == 1
    assert services['backend']['expected_replicas'] == 2
    assert 'directory' in services and 'backend-lb' in services
    rows.pop()
    assert worker._services()[1]['state'] == 'degraded'


def test_mock_status_is_explicit_and_does_not_publish_secrets(tmp_path):
    root = make_root(tmp_path)
    worker = OperationsWorker(root=root, mock=True, now=lambda: 100.0)
    status = worker.run_once()

    assert status["mock"] is True
    assert all(item["state"] == "mock" for item in status["services"])
    serialized = (mock_ops(root) / "status.json").read_text(encoding="utf-8")
    assert "do-not-publish" not in serialized
    assert status["configuration"]["sip_bind_address"] == "10.0.0.2"


def test_allowlisted_request_is_simulated_once(tmp_path):
    root = make_root(tmp_path)
    request_id = str(uuid4())
    request = {"id": request_id, "action": "restart", "service": "voice", "created_at": 90}
    worker = OperationsWorker(root=root, mock=True, now=lambda: 100.0)
    (mock_ops(root) / "requests/job.json").write_text(json.dumps(request), encoding="utf-8")

    worker.run_once()
    worker.run_once()
    completed = json.loads((mock_ops(root) / "completed.json").read_text(encoding="utf-8"))

    assert completed == [{"id": request_id, "action": "restart", "service": "voice", "status": "simulated", "at": 100, "error": None}]


@pytest.mark.parametrize("service", ["backend", "postgres", "frontend", "VOICE", "../voice"])
def test_service_control_is_strictly_allowlisted(tmp_path, service):
    root = make_root(tmp_path)
    request_id = str(uuid4())
    request = {"id": request_id, "action": "stop", "service": service, "created_at": 100}
    worker = OperationsWorker(root=root, mock=True, now=lambda: 100.0)
    (mock_ops(root) / "requests/job.json").write_text(json.dumps(request), encoding="utf-8")

    worker.run_once()
    completed = json.loads((mock_ops(root) / "completed.json").read_text(encoding="utf-8"))
    assert completed[0]["status"] == "rejected"
    assert completed[0]["error"] == "service is not allowed"


def test_expired_request_is_persistently_rejected(tmp_path):
    root = make_root(tmp_path)
    request_id = str(uuid4())
    request = {"id": request_id, "action": "stop", "service": "asterisk", "created_at": 1}
    worker = OperationsWorker(root=root, mock=True, now=lambda: 200.0)
    (mock_ops(root) / "requests/job.json").write_text(json.dumps(request), encoding="utf-8")
    worker.run_once()
    completed = json.loads((mock_ops(root) / "completed.json").read_text(encoding="utf-8"))
    assert completed[0]["status"] == "rejected"
    assert completed[0]["error"] == "request expired"


def test_real_action_uses_exact_compose_command_without_shell(tmp_path):
    root = make_root(tmp_path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[-3:] == ["ps", "--format", "json"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    request_id = str(uuid4())
    request = {"id": request_id, "action": "restart", "service": "asterisk", "created_at": 100}
    (root / "deploy/operations/requests/job.json").write_text(json.dumps(request), encoding="utf-8")
    OperationsWorker(root=root, runner=runner, now=lambda: 100.0).run_once()

    action_call = next(call for call in calls if call[0][-2:] == ["restart", "asterisk"])
    assert action_call[1]["shell"] is False
    assert action_call[0][:2] == ["docker", "compose"]
    assert "do-not-publish" not in " ".join(action_call[0])


def test_scheduled_mock_backup_runs_once_per_utc_date(tmp_path):
    root = make_root(tmp_path)
    worker = OperationsWorker(root=root, mock=True, now=lambda: 0)
    (mock_ops(root) / "settings.json").write_text(
        json.dumps({"backup_enabled": True, "backup_hour_utc": 0}), encoding="utf-8"
    )
    now = datetime(2026, 9, 15, 2, tzinfo=timezone.utc).timestamp()
    worker = OperationsWorker(root=root, mock=True, now=lambda: now)
    worker.run_once()
    worker.run_once()

    events = (mock_ops(root) / "events.jsonl").read_text(encoding="utf-8")
    assert events.count("scheduled backup simulated") == 1


def test_container_executor_owns_backup_requests_and_host_schedule(tmp_path):
    root = make_root(tmp_path)
    worker = OperationsWorker(root=root, mock=False, now=lambda: 100.0,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout='[]', stderr=''))
    (root/'deploy/operations/backup-executor.json').write_text(
        json.dumps({'mode':'container','version':1}),encoding='utf-8')
    request_id=str(uuid4())
    request_path=root/'deploy/operations/requests'/f'{request_id}.json'
    request_path.write_text(json.dumps({'id':request_id,'action':'backup','created_at':100}),encoding='utf-8')
    worker.run_once()
    assert request_path.is_file()
    assert not any(item.get('id')==request_id for item in worker.completed)
    assert 'last_scheduled_backup_date' not in worker.state


def test_stale_lock_is_recovered_and_live_lock_is_respected(tmp_path, monkeypatch):
    path = tmp_path / "worker.lock"
    path.write_text('{"pid":99999999}', encoding="utf-8")
    monkeypatch.setattr(SingleInstanceLock, "_alive", staticmethod(lambda pid: False))
    lock = SingleInstanceLock(path)
    assert lock.acquire()
    lock.release()
    assert not path.exists()

    path.write_text('{"pid":123}', encoding="utf-8")
    monkeypatch.setattr(SingleInstanceLock, "_alive", staticmethod(lambda pid: True))
    assert not SingleInstanceLock(path).acquire()


def test_recent_incomplete_lock_is_not_removed(tmp_path):
    path = tmp_path / "worker.lock"
    path.write_text("", encoding="utf-8")
    assert not SingleInstanceLock(path).acquire()
    assert path.exists()


def test_current_process_is_detected_as_alive():
    assert SingleInstanceLock._alive(os.getpid())
