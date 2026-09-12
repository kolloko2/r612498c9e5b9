"""Runs inside the installed WSL instance; never edits secrets or places calls."""
import json
import subprocess
import urllib.request
import urllib.error

SERVICES = ['voice-asterisk', 'voice-ollama', 'voice-dialogue', 'voice-gateway', 'voice-web']

def get(port, path):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=4) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try: return json.load(e)
        except Exception: return None
    except Exception:
        return None

def main():
    for service in SERVICES:
        subprocess.run(['systemctl', 'reset-failed', service], capture_output=True, timeout=10)
    subprocess.run(['systemctl', 'start', *SERVICES], check=True, capture_output=True, timeout=60)
    gateway = get(8001, '/api/v1/health')
    # Healthy running calls must not be disrupted by a broken web front end.
    if gateway is None or (gateway.get('status') != 'ok' and not gateway.get('active_calls')):
        subprocess.run(['systemctl', 'restart', 'voice-gateway'], check=True, capture_output=True, timeout=60)
    if get(8002, '/api/health') is None:
        subprocess.run(['systemctl', 'restart', 'voice-web'], check=True, capture_output=True, timeout=30)

if __name__ == '__main__':
    main()
