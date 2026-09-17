"""Admin-only file bridge. No Docker socket, shell or credentials in the web app."""
import json
import os
import threading
import time
from pathlib import Path
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from technical_config import DEFAULTS, validate, parse_xml, export_xml


class Operation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['backup', 'start', 'stop', 'restart', 'configure', 'update']
    service: Literal['voice', 'asterisk'] | None = None
    configuration: dict | None = None

    @model_validator(mode='after')
    def target(self):
        if (self.action in ('backup','configure','update')) != (self.service is None):
            raise ValueError('Backup has no service; service actions require a target')
        if self.action=='configure':
            self.configuration=validate(self.configuration)
            if not self.configuration:raise ValueError('Нет изменений')
        elif self.configuration is not None:
            raise ValueError('Configuration is allowed only for configure')
        return self


class XmlPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    xml: str = Field(max_length=32768)


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    backup_enabled: bool = True
    backup_hour_utc: int = Field(default=0, ge=0, le=23)


class Operations:
    def __init__(self, folder=None):
        value = folder or os.getenv('OPERATIONS_DIR')
        self.folder = Path(value) if value else None
        self.lock = threading.Lock()

    def read(self, name, default):
        if not self.folder:
            return default
        path = self.folder / name
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                return default
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def write(self, name, value):
        if not self.folder:
            raise HTTPException(503, 'Техническое управление не подключено')
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.' + uuid4().hex + '.tmp')
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)

    def snapshot(self):
        status = self.read('status.json', {})
        backup_worker = self.read('backup-worker-state.json', {})
        backup_sampled = backup_worker.get('heartbeat_at', 0) if isinstance(backup_worker, dict) else 0
        backup_fresh = isinstance(backup_sampled, (int, float)) and 0 <= time.time() - backup_sampled < 90
        events = []
        if self.folder:
            try:
                # Tail is bounded even after months of worker activity.
                with (self.folder / 'events.jsonl').open('rb') as source:
                    source.seek(0, 2)
                    size = source.tell()
                    source.seek(max(0, size - 128 * 1024))
                    if size > 128 * 1024:
                        source.readline()
                    for line in source.readlines()[-100:]:
                        try:
                            events.append(json.loads(line))
                        except ValueError:
                            pass
            except OSError:
                pass
        sampled = status.get('sampled_at', 0)
        stale = not isinstance(sampled, (int, float)) or not 0 <= time.time() - sampled < 90
        jobs = self.read('completed.json', [])
        by_id = {item.get('id'): item for item in jobs if isinstance(item, dict) and item.get('id')}
        if self.folder:
            for path in (self.folder / 'processed').glob('*.result.json'):
                item = self.read(str(path.relative_to(self.folder)), None)
                if isinstance(item, dict) and item.get('id'):
                    by_id[item['id']] = item
        if backup_fresh:
            status = {**status, 'backups': backup_worker.get('backups', status.get('backups', [])),
                      'last_scheduled_backup': backup_worker.get('last_scheduled_backup')}
        return {'enabled': self.folder is not None, 'stale': stale, 'backup_available': backup_fresh,
                'backup_worker': backup_worker if backup_fresh else {}, 'status': status,
                'settings': self.read('settings.json', Settings().model_dump()),
                'events': events, 'jobs': sorted(by_id.values(), key=lambda item:item.get('at',0))[-200:]}

    def submit(self, body, user):
        with self.lock:
            snapshot = self.snapshot()
            if not snapshot['enabled'] or (snapshot['stale'] and not (body.action == 'backup' and snapshot['backup_available'])):
                raise HTTPException(503, 'Управляющий процесс не подключён или данные устарели')
            completed = self.read('completed.json', [])
            finished = {job.get('id') for job in completed if isinstance(job, dict)
                        and job.get('status') not in ('queued', 'in_progress', 'running')}
            pending = [path for folder in ('requests','backup-claims') for path in (self.folder / folder).glob('*.json')
                       if path.stem not in finished]
            if pending:
                raise HTTPException(409, 'Дождитесь выполнения предыдущей операции')
            job = {'id': str(uuid4()), **body.model_dump(), 'created_at': time.time(),
                   'actor_id': user['id']}
            self.write('requests/' + job['id'] + '.json', job)
            return {**job, 'status': 'queued'}


def router(accounts, authorize):
    operations = Operations()
    api = APIRouter(prefix='/api/v1/admin/operations',
                    dependencies=[Depends(authorize), Depends(accounts.require('admin'))])

    @api.get('')
    def status():
        result = operations.snapshot()
        with accounts._lock:
            rows = accounts.db.execute(
                'SELECT id,at,event,user_id FROM account_audit ORDER BY at DESC,id DESC LIMIT 100'
            ).fetchall()
        audit = [{'id': row[0], 'at': row[1], 'type': row[2],
                  'detail': row[3] or '', 'severity': 'warning' if row[2] == 'login.failed' else 'info'}
                 for row in rows]
        result['events'] = sorted(result['events'] + audit, key=lambda item: item.get('at', 0))[-100:]
        return result

    @api.post('/jobs', status_code=202)
    def job(body: Operation, user=Depends(accounts.require('admin'))):
        result = operations.submit(body, user)
        with accounts._lock, accounts.db:
            accounts._audit('operations.queued.' + body.action, user['id'])
        return result

    @api.patch('/settings')
    def settings(body: Settings, user=Depends(accounts.require('admin'))):
        with operations.lock:
            operations.write('settings.json', body.model_dump())
            operations.write('settings-audit/' + uuid4().hex + '.json', {
                'at': time.time(), 'actor_id': user['id'], 'settings': body.model_dump()})
        with accounts._lock, accounts.db:
            accounts._audit('operations.settings.updated', user['id'])
        return body.model_dump()

    @api.get('/configuration')
    def configuration():
        values=operations.read('runtime-configuration.json',DEFAULTS)
        return {'values':values,'xml':export_xml(values),
                'note':'Применение перезапускает Backend, PostgreSQL, Voice и SIP. Выполняйте вне занятий.'}

    @api.get('/logs')
    def logs():
        return operations.read('service-logs.json',{'sampled_at':None,'lines':[], 'available':False})

    @api.post('/configuration/preview')
    def preview(body: XmlPreview):
        try: values=parse_xml(body.xml)
        except (ValueError,TypeError):raise HTTPException(422,'XML содержит недопустимые параметры') from None
        current=operations.read('runtime-configuration.json',DEFAULTS)
        return {'configuration':values,'changes':[{'key':key,'before':current.get(key),'after':value}
                    for key,value in values.items() if current.get(key)!=value],
                'requires_restart':True}

    return api
