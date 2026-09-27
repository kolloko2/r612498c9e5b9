"""Read-only delivery preflight: no database migration, downloads or secret output."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    checks=[]
    def record(name, ok, detail=''):
        checks.append({'check':name,'ok':bool(ok),'detail':detail})
    for relative in ['.env.docker','deploy/private/backup.env','deploy/tls/ca/ca.cert.pem',
                     'deploy/models/v5_5_ru.pt','deploy/models/vosk-model-small-ru-0.22']:
        p=ROOT/relative
        record(relative,p.exists(),'present' if p.exists() else 'missing')
    model=ROOT/'deploy/models/sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19'
    record('hybrid STT final model',model.is_dir() and any(model.rglob('*.onnx')))
    mapfile=ROOT/'deploy/maps/regional.sqlite'
    try:
        with sqlite3.connect(mapfile.as_uri()+'?mode=ro',uri=True) as db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        record('offline map',bool(tables),'read-only SQLite package opens')
    except (OSError,sqlite3.Error) as error:
        record('offline map',False,type(error).__name__)
    for image in ['trainer112-backend:dev','trainer112-frontend:dev','trainer112-voice:dev',
                  'trainer112-asterisk:dev','trainer112-postgres-tls:dev','trainer112-backup:dev',
                  'haproxy:3.0-alpine']:
        result=subprocess.run(['docker','image','inspect',image,'--format','{{.Id}}'],capture_output=True,text=True)
        record(image,result.returncode==0,'available locally' if result.returncode==0 else 'missing')
    result=subprocess.run(['docker','exec','trainer112-backend-1','python','-c',
        "import os,json,urllib.request,llm; c=llm.configuration(); "
        "v=json.load(urllib.request.urlopen(os.environ.get('OLLAMA_URL','http://host.docker.internal:11434')+'/api/tags',timeout=10)); "
        "p=llm.phone_configuration(); names=[m['name'] for m in v['models']]; "
        "print(json.dumps({'model':c['model'],'phone_model':p['model'],'installed':all(n in names for n in [c['model'],p['model']])}))"],
        capture_output=True,text=True)
    try:
        model=json.loads(result.stdout)
        record('local LLM',model['installed'],model['model']+'; phone: '+model['phone_model'])
    except (ValueError,KeyError):
        record('local LLM',False,'running backend/Ollama unavailable')
    print(json.dumps({'passed':all(c['ok'] for c in checks),'checks':checks,
                     'scope':'This host only. Target-host boot and physical phone are separate checks.'},ensure_ascii=False,indent=2))
    return 0 if all(c['ok'] for c in checks) else 1


if __name__=='__main__':
    sys.exit(main())
