"""Start the local student workstation. Existing Voice is configured separately."""
import os
import argparse
import secrets
import subprocess
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--review-db', help='Isolated SQLite review database; do not load deployment .env')
    args = parser.parse_args()
    if not args.review_db:
        load_dotenv(ROOT / '.env', override=False)
    env = os.environ.copy()
    if args.review_db:
        env.update(DATABASE_URL='', DIALOGUE_DB=str(Path(args.review_db).resolve()),
                   LLM_PROVIDER='mock', LLM_PROFILE='mock', COOKIE_SECURE='false',
                   DIALOGUE_URL='http://127.0.0.1:8000', DIALOGUE_TOKEN=secrets.token_urlsafe(32))
        env.pop('INTERNAL_CA_FILE', None)
        env['TERRITORIAL_ROUTES_FILE'] = str(ROOT / 'deploy' / 'territorial-routes.demo.json')
    env.setdefault('LLM_PROVIDER', 'mock')
    if not env.get('DIALOGUE_TOKEN'):
        env['DIALOGUE_TOKEN'] = secrets.token_urlsafe(32)
    env['BACKEND_TOKEN'] = env['DIALOGUE_TOKEN']
    env.setdefault('DIALOGUE_DB', str(ROOT / 'backend' / 'dialogue.sqlite3'))
    env.setdefault('DIALOGUE_URL', 'http://127.0.0.1:8000')
    children = []
    try:
        for service, port in [('backend', 8000), ('frontend', 3000)]:
            children.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT / service, env=env))
        print('Student workspace: http://127.0.0.1:3000', flush=True)
        while all(p.poll() is None for p in children):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
        for process in children:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if any(p.returncode not in (0, None, -15, 1) for p in children):
            sys.exit(1)


if __name__ == '__main__':
    main()
