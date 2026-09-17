"""Docker-native encrypted backup scheduler; deliberately has no Docker socket."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import Any
from uuid import UUID

from full_backup import create_container

OPERATIONS=Path(os.getenv('OPERATIONS_DIR','/data/operations'))
BACKUPS=Path(os.getenv('BACKUP_DIR','/data/backups'))
POLL_SECONDS=15
MAX_REQUEST_AGE=120


def atomic_json(path:Path,value:Any):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,separators=(',',':'));stream.write('\n')
            stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    except BaseException:
        try:os.unlink(name)
        except OSError:pass
        raise


def read_json(path:Path,default):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,UnicodeError,ValueError):return default


class BackupWorker:
    def __init__(self,operations=OPERATIONS,backups=BACKUPS,now=time.time,runner=subprocess.run,
                 heartbeat_seconds=30):
        self.operations=Path(operations);self.backups=Path(backups);self.now=now;self.runner=runner
        self.heartbeat_seconds=heartbeat_seconds
        self.requests=self.operations/'requests';self.claims=self.operations/'backup-claims'
        self.processed=self.operations/'processed';self.state_path=self.operations/'backup-worker-state.json'
        for path in (self.operations,self.backups,self.requests,self.claims,self.processed):path.mkdir(parents=True,exist_ok=True)
        state=read_json(self.state_path,{})
        self.state=state if isinstance(state,dict) else {}
        atomic_json(self.operations/'backup-executor.json',{'mode':'container','version':1})
        for claim in self.claims.glob('*.json'):
            request=read_json(claim,None)
            if isinstance(request,dict) and isinstance(request.get('id'),str):
                self._result(request,'failed','worker interrupted')
                destination=self.processed/f"{request['id']}.request.json"
                if not destination.exists():os.replace(claim,destination)

    def settings(self):
        value=read_json(self.operations/'settings.json',{})
        enabled=value.get('backup_enabled',True) if isinstance(value,dict) else True
        hour=value.get('backup_hour_utc',0) if isinstance(value,dict) else 0
        if not isinstance(enabled,bool) or isinstance(hour,bool) or not isinstance(hour,int) or not 0<=hour<=23:
            return {'backup_enabled':True,'backup_hour_utc':0}
        return {'backup_enabled':enabled,'backup_hour_utc':hour}

    def _key(self):
        value=os.getenv('BACKUP_ENCRYPTION_KEY','')
        try:key=bytes.fromhex(value)
        except ValueError:key=b''
        if len(key)!=32:raise RuntimeError('BACKUP_ENCRYPTION_KEY must contain 32-byte hex')
        return key

    def backup(self):
        mode=os.getenv('BACKUP_MODE','live')
        if mode=='mock':return None
        if mode!='live':raise RuntimeError('BACKUP_MODE must be live or mock')
        stamp=datetime.fromtimestamp(self.now(),timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        with tempfile.TemporaryDirectory(prefix='trainer-pg-') as folder:
            dump=Path(folder)/'postgres.dump'
            env={**os.environ,'PGPASSWORD':os.environ['POSTGRES_PASSWORD']}
            result=self.runner(['pg_dump','--host','postgres','--port','5432','--username','trainer',
                '--dbname','trainer','--format=custom','--file',str(dump)],env=env,stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=300,check=False)
            if result.returncode:raise RuntimeError('database dump failed')
            return create_container(dump,stamp,self.backups,self._key(),[
                ('/source/recordings','voice/recordings'),('/source/outbox','voice/outbox'),
                ('/source/compose.yml','configuration/docker-compose.yml'),
                ('/source/tls-certs','configuration/tls/certs'),
                ('/source/tls-ca/ca.cert.pem','configuration/tls/ca.cert.pem'),
                (self.operations/'settings.json','configuration/settings.json'),
                (self.operations/'runtime-configuration.json','configuration/runtime-configuration.json'),
                (self.operations/'deployment-profile.json','configuration/deployment-profile.json'),
                (self.operations/'security-audit','audit/backend'),(self.operations/'voice-audit','audit/voice'),
                (self.operations/'events.jsonl','audit/operations-events.jsonl')])

    def _backup_with_heartbeat(self):
        stop=threading.Event()
        def pulse():
            while not stop.wait(self.heartbeat_seconds):
                snapshot={**self.state,'heartbeat_at':int(self.now()),'executor':'container',
                          'settings':self.settings(),'backups':self.manifest()}
                atomic_json(self.state_path,snapshot)
        thread=threading.Thread(target=pulse,name='backup-heartbeat',daemon=True)
        thread.start()
        try:return self.backup()
        finally:
            stop.set();thread.join(timeout=max(1,self.heartbeat_seconds))

    @staticmethod
    def _success_status():
        return 'simulated' if os.getenv('BACKUP_MODE','live')=='mock' else 'completed'

    def _result(self,request,status,error=None):
        value={'id':request['id'],'action':'backup','service':None,'status':status,
               'at':int(self.now()),'error':error}
        atomic_json(self.processed/f"{request['id']}.result.json",value)

    def consume(self):
        for path in sorted(self.requests.glob('*.json')):
            raw=read_json(path,None)
            if not isinstance(raw,dict) or raw.get('action')!='backup':continue
            try:identifier=str(UUID(raw.get('id','')))
            except ValueError:continue
            if identifier!=raw.get('id'):continue
            claim=self.claims/f'{identifier}.json'
            try:os.replace(path,claim)
            except FileNotFoundError:continue
            except OSError:continue
            created=raw.get('created_at')
            if not isinstance(created,(int,float)) or isinstance(created,bool) or not 0<=self.now()-created<=MAX_REQUEST_AGE:
                self._result(raw,'rejected','request expired')
            else:
                self._result(raw,'in_progress')
                try:self._backup_with_heartbeat();self._result(raw,self._success_status())
                except (OSError,RuntimeError,subprocess.SubprocessError):self._result(raw,'failed','backup failed')
            destination=self.processed/f'{identifier}.request.json'
            if not destination.exists():os.replace(claim,destination)

    def scheduled(self,settings):
        if not settings['backup_enabled']:return
        current=datetime.fromtimestamp(self.now(),timezone.utc);today=current.date().isoformat()
        last=self.state.get('last_scheduled_backup',{})
        retry_wait=isinstance(last,dict) and last.get('status')=='failed' and self.now()-float(last.get('at',0))<900
        if current.hour<settings['backup_hour_utc'] or self.state.get('last_scheduled_backup_date')==today or retry_wait:return
        try:self._backup_with_heartbeat();status=self._success_status();error=None;self.state['last_scheduled_backup_date']=today
        except (OSError,RuntimeError,subprocess.SubprocessError):status='failed';error='backup failed'
        self.state['last_scheduled_backup']={'at':int(self.now()),'status':status,'error':error}

    def manifest(self):
        rows=[]
        for path in sorted(self.backups.glob('full-*.t112'),reverse=True)[:200]:
            try:stat=path.stat()
            except OSError:continue
            rows.append({'name':path.name,'size_bytes':stat.st_size,
                         'created_at':int(stat.st_mtime),'status':'complete'})
        return rows

    def run_once(self):
        settings=self.settings()
        self.state.update({'heartbeat_at':int(self.now()),'executor':'container','settings':settings})
        atomic_json(self.state_path,self.state)
        self.consume();self.scheduled(settings)
        self.state.update({'heartbeat_at':int(self.now()),'executor':'container','settings':settings,
                           'backups':self.manifest()})
        atomic_json(self.state_path,self.state);return self.state


def main():
    worker=BackupWorker()
    while True:
        worker.run_once();time.sleep(POLL_SECONDS)


if __name__=='__main__':main()
