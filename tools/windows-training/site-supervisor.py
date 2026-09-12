"""Local Windows availability supervisor. No remote reporting, no phone calls."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
REPAIR = '/opt/voice-gateway/site-repair.py'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

def health():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8002/api/health', timeout=5) as r:
            return json.load(r).get('status') == 'ok'
    except Exception:
        return False

def repair():
    result = subprocess.run(['wsl', '-d', 'Ubuntu-24.04', '-u', 'root', '--',
                             '/opt/voice-gateway/.venv/bin/python', REPAIR],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, creationflags=HIDDEN)
    logging.info('Repair exit=%s', result.returncode)
    return result.returncode == 0

def main():
    # Exclusive local socket prevents duplicate supervisors from shortcut/task starts.
    guard = socket.socket()
    try:
        guard.bind(('127.0.0.1', 18092))
    except OSError:
        return
    log = RotatingFileHandler(ROOT/'site-supervisor.log', maxBytes=512000, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[log], format='%(asctime)s %(message)s')
    logging.info('Supervisor started')
    keepalive = None
    failed = 0
    last = None
    while True:
        try:
            if keepalive is None or keepalive.poll() is not None:
                keepalive = subprocess.Popen(['wsl', '-d', 'Ubuntu-24.04', '-u', 'root', '--',
                    'bash', '-lc', 'exec -a voice-site-keepalive sleep infinity'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=HIDDEN)
                repair()
            ok = health()
            if ok != last:
                logging.info('Site %s', 'healthy' if ok else 'unavailable')
                last = ok
            failed = 0 if ok else failed + 1
            if failed >= 3:
                repair()
                failed = 0
        except Exception as exc:
            logging.error('Supervisor recovery: %s', type(exc).__name__)
        time.sleep(10)

if __name__ == '__main__':
    main()
