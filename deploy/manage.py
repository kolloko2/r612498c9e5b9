"""Small cross-platform launcher; never expand credentials on command lines."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ['docker', 'compose', '--env-file', str(ROOT / '.env.docker'),
           '-f', str(ROOT / 'docker-compose.yml')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'up', 'stop', 'status', 'backup-db', 'export-images'])
    parser.add_argument('--directory', action='store_true', default=None, help='Include the synthetic LDAPS directory overlay')
    parser.add_argument('--tls', action='store_true', default=None, help='Use the encrypted transport overlay (see docs/TLS.md)')
    parser.add_argument('--cluster', action='store_true', default=None, help='Use the bounded two-replica Backend profile')
    parser.add_argument('--plain', action='store_true', help='Explicitly disable TLS/cluster for plain development')
    args = parser.parse_args()
    try:previous=json.loads((ROOT/'deploy/operations/deployment-profile.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):previous={}
    for key in ('directory','tls','cluster'):
        if getattr(args,key) is None:
            # A new stand starts in the supported encrypted profile. Plain mode
            # remains an explicit development choice and has no backup executor.
            setattr(args,key,bool(previous.get(key, key == 'tls')))
    if args.plain:args.tls=args.cluster=False
    compose = COMPOSE.copy()
    if args.directory:
        directory_env = ROOT / 'deploy' / 'directory' / 'private' / 'test.env'
        if not directory_env.is_file():
            parser.exit(1, 'Generate the private test directory ENV first.\n')
        compose += ['--env-file', str(directory_env), '-f', str(ROOT / 'deploy' / 'directory' / 'compose.yaml')]
    if args.cluster:
        compose += ['-f',str(ROOT/'deploy/cluster/compose.yaml')]
    if args.tls:
        backup_env=ROOT/'deploy/private/backup.env'
        ca_cert=ROOT/'deploy/tls/ca/ca.cert.pem'
        if not backup_env.is_file():
            parser.exit(1, 'Run python deploy/full_backup.py prepare-key and protect its recovery key first.\n')
        if not ca_cert.is_file():
            parser.exit(1, 'Prepare the TLS profile first (see docs/TLS.md).\n')
        compose += ['-f',str(ROOT/'deploy/tls/docker-compose.tls.yml'),'--profile','tls']
        if args.cluster:compose += ['-f',str(ROOT/'deploy/cluster/compose.tls.yaml')]
    if not (ROOT / '.env.docker').is_file():
        parser.exit(1, 'Run python deploy/prepare.py first.\n')
    if args.action == 'export-images':
        folder = ROOT / 'output' / 'docker'
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / ('trainer112-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.tar')
        images = ['trainer112-backend:dev', 'trainer112-frontend:dev',
                  'trainer112-voice:dev', 'trainer112-asterisk:dev', 'postgres:16-bookworm']
        if args.directory:
            images.append('trainer112-directory:dev')
        if args.tls:images.extend(['trainer112-postgres-tls:dev','trainer112-backup:dev'])
        if args.cluster:images.append('haproxy:3.0-alpine')
        result = subprocess.call(['docker', 'image', 'save', '-o', str(destination), *images], cwd=ROOT)
        print(f'Image export {"created" if result == 0 else "FAILED"}: {destination}')
        return result
    if args.action == 'backup-db':
        folder = ROOT / 'deploy' / 'backups'
        folder.mkdir(exist_ok=True)
        destination = folder / ('postgres-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.dump')
        # Binary stdout is written directly: no PowerShell text encoding corruption.
        with destination.open('xb') as output:
            result = subprocess.run(compose + ['exec', '-T', 'postgres', 'pg_dump',
                '-U', 'trainer', '-d', 'trainer', '-Fc'], stdout=output, cwd=ROOT)
        if result.returncode:
            print(f'Backup FAILED. Incomplete file retained: {destination}', file=sys.stderr)
            return result.returncode
        print(f'PostgreSQL backup created: {destination}')
        return 0
    commands = {'build': ['build'], 'up': ['up', '-d', '--wait', '--wait-timeout', '180'],
                'stop': ['stop'], 'status': ['ps']}
    if args.action=='up' and args.cluster:commands['up'] += ['--scale','backend=1']
    result=subprocess.call(compose + commands[args.action], cwd=ROOT)
    if args.action=='up' and args.cluster and result==0:
        result=subprocess.call(compose+['up','-d','--no-deps','--wait','--wait-timeout','180','--scale','backend=2','backend'],cwd=ROOT)
    if args.action=='up' and result==0:
        from ops_worker import _atomic_json
        _atomic_json(ROOT/'deploy/operations/deployment-profile.json',{'directory':args.directory,'tls':args.tls,'cluster':args.cluster})
    return result


if __name__ == '__main__':
    raise SystemExit(main())
