"""Persistent request metadata audit; never collect payloads, query strings or secrets."""
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import JSONResponse

DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')
AUDIT_ACTOR = ContextVar('security_audit_actor', default=None)


class AuditLog:
    minimum_retention_days = 183

    def __init__(self, folder=None):
        value = folder or os.getenv('SECURITY_AUDIT_DIR')
        self.folder = Path(value) if value else None
        self.lock = threading.RLock()
        self.archived_day = None
        self.failed = False
        # Group commit state. One fsync can make several concurrent records
        # durable, but `append` still returns only after the caller's own record
        # is on disk, so the fail-closed guarantee is unchanged.
        self.condition = threading.Condition(self.lock)
        self.pending = []
        self.sequence = 0
        self.committed = 0
        self.rejected = 0
        self.writing = False
        self.handle = None
        self.handle_day = None

    def _stream(self, day):
        """Reuse one appender; reopening per record costs more than the fsync."""
        if self.handle is None or self.handle_day != day:
            self.close()
            self.folder.mkdir(parents=True, exist_ok=True)
            self.handle = (self.folder / (day + '.jsonl')).open('a', encoding='utf-8')
            self.handle_day = day
        return self.handle

    def close(self):
        if self.handle is not None:
            try:
                self.handle.close()
            except OSError:
                pass
            self.handle, self.handle_day = None, None

    def _write(self, batch):
        """Write one group and fsync it once. Only one thread runs this."""
        for day, records in batch:
            stream = self._stream(day)
            stream.write(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records))
            stream.flush()
            os.fsync(stream.fileno())

    def append(self, event):
        if self.folder is None:
            return
        day = datetime.now(timezone.utc).date().isoformat()
        with self.condition:
            self.sequence += 1
            mine = self.sequence
            self.pending.append((day, event))
            while True:
                if mine <= self.rejected:
                    raise OSError('security audit write failed')
                if mine <= self.committed:
                    return
                if self.writing:
                    self.condition.wait()
                    continue
                self.writing = True
                group, self.pending = self.pending, []
                upto = self.sequence
                break

        # Grouped outside the lock so that concurrent requests keep queueing
        # their records while this fsync is in flight.
        batch, error = [], None
        for record_day, record in group:
            if batch and batch[-1][0] == record_day:
                batch[-1][1].append(record)
            else:
                batch.append((record_day, [record]))
        try:
            self._write(batch)
        except OSError as failure:
            error = failure
            self.close()

        with self.condition:
            self.writing = False
            if error is None:
                self.committed = upto
                self.failed = False
            else:
                # The whole group is unwritten; every waiter in it must fail too.
                self.rejected = upto
                self.committed = upto
                self.failed = True
            self.condition.notify_all()
        if error is not None:
            raise error

    def archive(self):
        """Keep originals AND gzip copies. No automatic retention deletion."""
        if self.folder is None or not self.folder.exists():
            return
        with self.lock:
            today = datetime.now(timezone.utc).date().isoformat()
            for source in self.folder.glob('*.jsonl'):
                if not DAY.fullmatch(source.stem) or source.stem >= today:
                    continue
                target = source.with_suffix('.jsonl.gz')
                if target.exists():
                    continue
                temporary = target.with_name(target.name + '.tmp-' + uuid4().hex)
                with source.open('rb') as original, temporary.open('xb') as raw:
                    with gzip.GzipFile(fileobj=raw, mode='wb') as compressed:
                        for chunk in iter(lambda: original.read(65536), b''):
                            compressed.write(chunk)
                    raw.flush()
                    os.fsync(raw.fileno())
                temporary.replace(target)

    def list(self, day=None, limit=100):
        if self.folder is None:
            return {'enabled': False, 'failed': False, 'minimum_retention_days': 183,
                    'automatic_deletion': False, 'days': [], 'events': []}
        selected = day or datetime.now(timezone.utc).date().isoformat()
        if not DAY.fullmatch(selected):
            raise HTTPException(422, 'Дата должна быть YYYY-MM-DD')
        try:
            datetime.strptime(selected, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(422, 'Некорректная дата') from None
        days = sorted([p.stem for p in self.folder.glob('*.jsonl') if DAY.fullmatch(p.stem)], reverse=True)
        events = []
        path = self.folder / (selected + '.jsonl')
        if path.exists():
            with self.lock, path.open('rb') as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - 1024 * 1024))
                if size > 1024 * 1024:
                    stream.readline()
                for line in stream.readlines()[-limit:]:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
        return {'enabled': True, 'failed': self.failed, 'minimum_retention_days': 183,
                'automatic_deletion': False, 'days': days, 'day': selected, 'events': events}


class AuditMiddleware:
    def __init__(self, app, audit):
        self.app, self.audit = app, audit

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            return await self.websocket(scope, receive, send)
        if scope['type'] != 'http' or scope.get('path') == '/api/v1/health':
            return await self.app(scope, receive, send)
        identifier, started = str(uuid4()), time.monotonic()
        method = scope.get('method', '')
        method = method if method in ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS') else 'OTHER'
        base = {'request_id': identifier, 'method': method}
        try:
            await asyncio.to_thread(self.audit.append, {**base, 'at': time.time(), 'phase': 'started'})
        except OSError:
            return await JSONResponse({'detail': 'Журнал безопасности недоступен'}, status_code=503)(scope, receive, send)
        status = 500
        actor_state = scope.setdefault('state', {}).setdefault('audit_actor', {})
        actor_token = AUDIT_ACTOR.set(actor_state)

        async def observed_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            AUDIT_ACTOR.reset(actor_token)
            route = scope.get('route')
            actor = scope.get('state', {}).get('audit_actor', {})
            event = {**base, 'at': time.time(), 'phase': 'finished',
                     'route': getattr(route, 'path', '[unmatched]'), 'status': status,
                     'actor_id': actor.get('id'), 'role': actor.get('role'),
                     'elapsed_ms': round((time.monotonic() - started) * 1000)}
            try:
                await asyncio.to_thread(self.audit.append, event)
            except OSError:
                # Started record remains evidence of an incomplete audit operation.
                # Never replay an application mutation after a logging failure.
                print('SECURITY_AUDIT_WRITE_FAILED', flush=True)

    async def websocket(self, scope, receive, send):
        identifier, started = str(uuid4()), time.monotonic()
        received, sent, status = 0, 0, 1006
        try:
            await asyncio.to_thread(self.audit.append, {'request_id':identifier,'at':time.time(),
                'method':'WS','phase':'started'})
        except OSError:
            await send({'type':'websocket.close','code':1011})
            return
        async def audit_receive():
            nonlocal received,status
            message=await receive()
            if message['type']=='websocket.receive':received+=1
            if message['type']=='websocket.disconnect':status=message.get('code',1006)
            return message
        async def audit_send(message):
            nonlocal sent,status
            if message['type']=='websocket.send':sent+=1
            if message['type']=='websocket.accept':status=101
            if message['type']=='websocket.close':status=message.get('code',1000)
            await send(message)
        try:
            await self.app(scope,audit_receive,audit_send)
        finally:
            actor=scope.get('state',{}).get('audit_actor',{})
            try:
                await asyncio.to_thread(self.audit.append,{'request_id':identifier,'at':time.time(),
                    'method':'WS','phase':'finished','route':getattr(scope.get('route'),'path','[unmatched]'),
                    'actor_id':actor.get('id'),'role':actor.get('role'),'status':status,
                    'received_messages':received,'sent_messages':sent,
                    'elapsed_ms':round((time.monotonic()-started)*1000)})
            except OSError:
                print('SECURITY_AUDIT_WRITE_FAILED',flush=True)


def install(app, accounts, authorize):
    audit = AuditLog()
    voice_audit=AuditLog()
    operations_folder=os.getenv('OPERATIONS_DIR')
    voice_audit.folder=Path(operations_folder)/'voice-audit' if operations_folder else None
    app.state.security_audit = audit
    app.add_middleware(AuditMiddleware, audit=audit)
    api = APIRouter(prefix='/api/v1/admin/audit', dependencies=[Depends(authorize), Depends(accounts.require('admin'))])

    @api.get('')
    def records(day: str | None = Query(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$'),
                limit: int = Query(default=100, ge=1, le=200),
                source: str = Query(default='backend',pattern='^(backend|voice)$')):
        return {**(voice_audit if source=='voice' else audit).list(day, limit),'source':source}

    app.include_router(api)

    async def archive_loop():
        while True:
            try:
                await asyncio.to_thread(audit.archive)
            except OSError:
                print('SECURITY_AUDIT_ARCHIVE_FAILED', flush=True)
            await asyncio.sleep(3600)

    @app.on_event('startup')
    async def start_archive():
        app.state.audit_archive_task = asyncio.create_task(archive_loop())

    @app.on_event('shutdown')
    async def stop_archive():
        app.state.audit_archive_task.cancel()
        try:
            await app.state.audit_archive_task
        except asyncio.CancelledError:
            pass
