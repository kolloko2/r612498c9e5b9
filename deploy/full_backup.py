"""Encrypted online backups. Recovery keys never enter the archive."""
import io
import json
import os
from pathlib import Path
import secrets
import tarfile
import tempfile

MAGIC=b'T112BACKUP1\n'
HOST_CONFIGURATION_PATHS=(
    'docker-compose.yml','deploy/tls/certs','deploy/tls/ca/ca.cert.pem',
    'deploy/tls/docker-compose.tls.yml','deploy/directory/compose.yaml',
    'deploy/operations/settings.json','deploy/operations/runtime-configuration.json',
    'deploy/operations/deployment-profile.json','deploy/operations/security-audit',
    'deploy/operations/voice-audit','deploy/operations/events.jsonl')


def ensure_key(root):
    target=root/'deploy/private/backup.env'
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w',encoding='ascii') as stream:
            stream.write('BACKUP_ENCRYPTION_KEY='+secrets.token_hex(32)+'\n')
    return target


def read_key(root):
    value=os.getenv('BACKUP_ENCRYPTION_KEY')
    if not value:
        lines=(root/'deploy/private/backup.env').read_text(encoding='ascii').splitlines()
        value=next((line.split('=',1)[1] for line in lines if line.startswith('BACKUP_ENCRYPTION_KEY=')),'')
    key=bytes.fromhex(value)
    if len(key)!=32:raise ValueError('Invalid backup key')
    return key


def encrypt(source,target,key):
    from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
    nonce=os.urandom(12)
    encryptor=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    with source.open('rb') as original,target.open('xb') as output:
        output.write(MAGIC+nonce)
        for block in iter(lambda:original.read(1024*1024),b''):
            output.write(encryptor.update(block))
        output.write(encryptor.finalize());output.write(encryptor.tag)
        output.flush();os.fsync(output.fileno())


def decrypt(source,target,key):
    from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
    if target.exists():raise FileExistsError(target)
    with source.open('rb') as original:
        if original.read(len(MAGIC))!=MAGIC:raise ValueError('Invalid backup')
        nonce=original.read(12)
        original.seek(-16,2);end=original.tell();tag=original.read(16)
        original.seek(len(MAGIC)+12)
        decryptor=Cipher(algorithms.AES(key),modes.GCM(nonce,tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        fd,name=tempfile.mkstemp(dir=target.parent,prefix='.recovery-')
        try:
            with os.fdopen(fd,'wb') as output:
                while original.tell()<end:
                    output.write(decryptor.update(original.read(min(1024*1024,end-original.tell()))))
                output.write(decryptor.finalize());output.flush();os.fsync(output.fileno())
            os.link(name,target)
        finally:
            Path(name).unlink(missing_ok=True)


def create(worker,dump,stamp):
    key=read_key(worker.root)
    final=worker.backup_dir/f'full-{stamp}.t112'
    with tempfile.TemporaryDirectory(prefix='trainer-backup-',dir=worker.backup_dir) as folder:
        work=Path(folder)
        media=work/'voice-data.tar.gz'
        script="import tarfile; t=tarfile.open(fileobj=__import__('sys').stdout.buffer,mode='w|gz'); t.add('/data/recordings',arcname='recordings'); t.add('/data/outbox',arcname='outbox'); t.close()"
        with media.open('xb') as output:
            result=worker._compose(['exec','-T','voice','python','-c',script],timeout=300,stdout=output)
        if result.returncode:raise RuntimeError('Voice backup failed')
        archive=work/'bundle.tar.gz'
        with tarfile.open(archive,'w:gz') as bundle:
            bundle.add(dump,arcname='postgres.dump');bundle.add(media,arcname='voice-data.tar.gz')
            for name in HOST_CONFIGURATION_PATHS:
                path=worker.root/name
                if path.exists():bundle.add(path,arcname='configuration/'+name,recursive=True)
            manifest=json.dumps({'version':1,'created':stamp,'consistency':'online',
                'includes':['postgres','recordings','outbox','configuration','audit'],
                'excludes':['backup-key','root-ca-private-key','model-weights'],
                'note':'Open recordings may end at snapshot time; not a cross-service atomic snapshot.'}).encode()
            entry=tarfile.TarInfo('manifest.json');entry.size=len(manifest)
            bundle.addfile(entry,io.BytesIO(manifest))
        partial=final.with_suffix('.partial')
        encrypt(archive,partial,key);partial.replace(final)
    return final


def create_container(dump, stamp, backup_dir, key, sources):
    """Create one authenticated archive from directly mounted container paths.

    ``sources`` is a sequence of ``(path, archive_name)`` selected by the caller;
    missing optional paths are skipped. The encryption key is passed in memory and
    is never added to the archive or read from a mounted secret file.
    """
    final=backup_dir/f'full-{stamp}.t112'
    with tempfile.TemporaryDirectory(prefix='trainer-backup-') as folder:
        work=Path(folder);archive=work/'bundle.tar.gz'
        with tarfile.open(archive,'w:gz') as bundle:
            bundle.add(dump,arcname='postgres.dump')
            included=['postgres']
            for source,name in sources:
                source=Path(source)
                if source.exists():
                    bundle.add(source,arcname=name,recursive=True)
                    included.append(name)
            manifest=json.dumps({'version':2,'created':stamp,'consistency':'online',
                'includes':included,
                'excludes':['backup-key','private-keys','database-password','model-weights'],
                'note':'PostgreSQL dump is consistent. Mounted media/config/audit files are captured independently and may change during the archive.'}).encode()
            entry=tarfile.TarInfo('manifest.json');entry.size=len(manifest)
            bundle.addfile(entry,io.BytesIO(manifest))
        partial=backup_dir/f'.full-{stamp}.partial'
        encrypt(archive,partial,key);partial.replace(final)
    return final


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare-key','create','decrypt'])
    parser.add_argument('--source',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    if args.action=='prepare-key':
        ensure_key(root);print('Private recovery key prepared; keep a separate protected copy.')
    elif args.action=='create':
        from ops_worker import OperationsWorker
        status,error=OperationsWorker()._backup()
        print('Full backup: '+status)
        if error:raise SystemExit(error)
    else:
        if not args.source or not args.output:parser.error('--source and --output required')
        decrypt(args.source,args.output,read_key(root));print('Authenticated archive recovered; not extracted or applied.')
