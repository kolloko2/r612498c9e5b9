"""Local launcher for this computer's WSL Asterisk and portable MicroSIP."""
import configparser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
PHONE = ROOT / 'MicroSIP' / 'MicroSIP.exe'


def preflight():
    """Do not launch a retrying supervisor for a stand that is not installed."""
    if not shutil.which('wsl'):
        return ['WSL is not available. Install or connect a dedicated training stand.']
    result = subprocess.run(['wsl', '--list', '--quiet'], capture_output=True, timeout=20)
    encoding = 'utf-16-le' if b'\x00' in result.stdout else 'utf-8'
    installed = result.stdout.decode(encoding, errors='replace').replace('\x00', '').splitlines()
    errors = []
    if result.returncode or 'Ubuntu-24.04' not in [name.strip() for name in installed]:
        errors.append('Configured WSL distribution Ubuntu-24.04 is not installed. Existing distributions were not reconfigured.')
    if not PHONE.is_file():
        errors.append('Portable MicroSIP is missing: tools/windows-training/MicroSIP/MicroSIP.exe')
    if not PHONE.with_suffix('.ini').is_file():
        errors.append('The training MicroSIP account configuration is missing.')
    return errors


def linux(*args):
    result = subprocess.run(['wsl', '-d', 'Ubuntu-24.04', '-u', 'root', '--', *args], capture_output=True)
    if result.returncode:
        raise RuntimeError('Linux command failed: ' + ' '.join(args[:2]) + '; exit=' + str(result.returncode))
    return result.stdout.decode('utf-8').strip()


def configure_phone():
    ip = linux('hostname', '-I').split()[0]
    values = json.loads(linux('cat', '/etc/voice-training/secrets.json'))
    ini = PHONE.with_suffix('.ini')
    data = ini.read_bytes()
    encoding = 'utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_string(data.decode(encoding))
    config['Settings']['accountId'] = '1'
    config['Settings']['enableLocalAccount'] = '0'
    config['Settings']['audioCodecs'] = 'PCMA/8000/1 PCMU/8000/1'
    config['Settings']['EC'] = '1'
    config['Settings']['AA'] = '0'
    # Full input volume clipped loud speech in live tests and reduced STT quality.
    config['Settings']['volumeInput'] = '90'
    config['Account1'] = {
        'label':'Training 201', 'server':ip, 'domain':ip, 'username':'201',
        'authID':'201', 'password':values['SIP201_PASSWORD'], 'displayName':'Operator 201',
        'transport':'udp', 'registerRefresh':'60', 'keepAlive':'15', 'publish':'0',
        'ICE':'0', 'allowRewrite':'1', 'disableSessionTimer':'0',
    }
    backup = ROOT.parent/'work'/'microsip-before-training.ini'
    if not backup.exists(): shutil.copy2(ini, backup)
    with ini.open('w', encoding='utf-16') as file:
        config.write(file, space_around_delimiters=False)
    return ip, values


def request(path, values, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request('http://127.0.0.1:8001'+path, data=data,
            headers={'Authorization':'Bearer '+values['API_TOKEN'], 'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=65) as response:
        return json.load(response)


def main():
    action = sys.argv[1] if len(sys.argv)>1 else 'start'
    if action in ('start', 'check'):
        errors = preflight()
        if errors:
            for error in errors:
                print(error)
            print('SIP was not started. No calls or credential changes were made.')
            return 2
        if action == 'check':
            print('Local launcher prerequisites found; telephony/media readiness must be checked separately.')
            return 0
    if action == 'start':
        subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')), str(ROOT/'site-supervisor.py')],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        # WSL systemd services alone do not keep the distribution alive.
        check = subprocess.run(['powershell','-NoProfile','-Command',
            "@(Get-CimInstance Win32_Process -Filter \"Name='wsl.exe'\" | Where-Object { $_.CommandLine -like '*voice-training-keepalive*' }).Count"],
            capture_output=True, text=True)
        if check.returncode or check.stdout.strip() == '0':
            subprocess.Popen(['wsl','-d','Ubuntu-24.04','-u','root','--','bash','-lc',
                              'exec -a voice-training-keepalive sleep infinity'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        linux('systemctl','start','voice-asterisk','voice-ollama','voice-dialogue','voice-gateway','voice-web')
        try:
            if request('/api/v1/health', {'API_TOKEN':''}).get('active_calls', 0):
                print('Active call preserved. Website: http://127.0.0.1:8002')
                return
        except Exception:
            pass
        # Close only this portable phone so its INI is not overwritten on exit.
        subprocess.run([str(PHONE), '/exit'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        ip, values = configure_phone()
        subprocess.Popen([str(PHONE)], cwd=PHONE.parent)
        for _ in range(30):
            try:
                state=request('/api/v1/health',values)
                print('Asterisk and Voice Gateway are running. Phone: 201. Server: '+ip)
                print(json.dumps(state))
                return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError('Voice Gateway did not become ready')
    values=json.loads(linux('cat','/etc/voice-training/secrets.json'))
    if action == 'call':
        import uuid
        conversation = request('/api/v1/health', values).get('pipeline_mode') == 'conversation'
        print('Answer in MicroSIP. Say a phrase, then pause for the spoken reply. Training fire scenario: you are the operator, the bot is the victim. Test lasts 10 minutes.' if conversation else 'Answer the call in MicroSIP. Speak during the five-second test tone.', flush=True)
        call=request('/api/v1/calls',values,{'session_id':str(uuid.uuid4()),'extension':'201'})
        call_id=call['call_id']
        try:
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                state=request('/api/v1/calls/'+call_id,values)
                if state['status']=='active':
                    if conversation:
                        end = time.monotonic() + 600
                        while time.monotonic() < end:
                            time.sleep(1)
                            if request('/api/v1/calls/'+call_id, values)['status'] in ('ended','failed'):
                                break
                    else:
                        time.sleep(7)
                    break
                if state['status'] in ('failed','ended'):
                    raise RuntimeError('Call ended before media was ready: '+state['status'])
                time.sleep(0.2)
        finally:
            state=request('/api/v1/calls/'+call_id+'/hangup',values,{})
            report=ROOT/'training-last-call.json'
            report.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            print('Call status: '+state['status']+'. Result: '+str(report))
    elif action=='status':
        print(json.dumps(request('/api/v1/health',values)))
        print(linux('asterisk','-rx','pjsip show contacts'))
    else:
        raise ValueError('Use start, call or status')


if __name__ == '__main__':
    sys.exit(main())
