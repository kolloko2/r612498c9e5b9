"""Live acceptance of the full-cycle 112 operator over a real TLS/SRTP phone.

A containerized baresip phone on training extension 220 answers the incoming
112 call and speaks synthesized operator phrases, including a follow-up
question. The script then saves the card with the dispatched service, waits
for the service status that arrives automatically on the tile (§11.1 of the
АРМ-112 instruction) and answers the repeated call from the same caller ID.

Uses only application APIs with the demo teacher and a separate demo student,
so real students' history is not touched. Evidence is written to
``artifacts/112-sip-acceptance-<date>/result.json``.
"""
import argparse
import json
import os
import ssl
import subprocess
import tempfile
import time
import wave
from datetime import date
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from seed_demo import Client

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('ACCEPTANCE_112_OUT', str(ROOT / 'artifacts' / f'112-sip-acceptance-{date.today()}')))
SCENARIO = 'ticket-01-3-1e17605a'


def docker(*args):
    return subprocess.check_output(['docker', *args], stderr=subprocess.STDOUT, encoding='utf-8', errors='replace').strip()


def client(name):
    c = Client(os.environ.get('ACCEPTANCE_BASE_URL', 'https://127.0.0.1:3000'))
    c.context = ssl.create_default_context(cafile=str(ROOT / 'deploy/tls/ca/ca.cert.pem'))
    c.login(name)
    return c


def speech(name, *phrases, gap_seconds=30):
    """Synthesized operator speech; phrases are separated by silence for the reply."""
    parts = []
    for index, text in enumerate(phrases):
        docker('exec', 'trainer112-voice-1', 'python', '-m', 'tools.speech_smoke',
               '--tts-model', '/models/v5_5_ru.pt', '--stt-model', '/models/vosk-model-small-ru-0.22',
               '--text', text, '--wav', f'/tmp/{name}-{index}.wav')
        docker('cp', f'trainer112-voice-1:/tmp/{name}-{index}.wav', str(OUT / f'{name}-{index}.wav'))
        with wave.open(str(OUT / f'{name}-{index}.wav'), 'rb') as audio:
            params = audio.getparams()
            parts.append(audio.readframes(audio.getnframes()))
    silence = bytes(params.framerate * params.sampwidth * params.nchannels * gap_seconds)
    with wave.open(str(OUT / f'{name}.wav'), 'wb') as audio:
        audio.setparams(params)
        audio.writeframes(silence.join(parts))
    return OUT / f'{name}.wav'


def phone(secret, audio, output, seconds):
    name = 'trainer112-112-acceptance-' + uuid4().hex[:8]
    output.mkdir(parents=True, exist_ok=True)
    docker('run', '-d', '--name', name, '--network', 'container:trainer112-asterisk-1',
           '-e', f'SIP_PROBE_SECONDS={seconds}', '-e', 'SIP_PROBE_SILENCE_SECONDS=40',
           '-e', 'SIP_PROBE_LEAD_SECONDS=12',
           '-v', f'{secret}:/run/secrets/sip220_password:ro',
           '-v', f'{ROOT / "deploy/tls/ca/ca.cert.pem"}:/ca/ca.cert.pem:ro',
           '-v', f'{audio}:/audio/operator.wav:ro',
           '-v', f'{output}:/output', 'trainer112-sip220-probe:dev')
    time.sleep(4)
    return name


def wait_exit(name, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if docker('inspect', '-f', '{{.State.Running}}', name) != 'true':
            break
        time.sleep(2)
    # Строки статистики аудио каждые 100 мс вытесняют сведения о вызове.
    log = '\n'.join(line for line in docker('logs', name).splitlines() if 'audio=' not in line)
    docker('rm', '-f', name)
    return log


def voice_evidence(call_ids):
    code = """import os,json,ssl,urllib.request
ctx=ssl.create_default_context(cafile=os.environ['INTERNAL_CA_FILE'])
h={'Authorization':'Bearer '+os.environ['VOICE_API_TOKEN']}
get=lambda p: json.load(urllib.request.urlopen(urllib.request.Request('https://voice:8001/api/v1/'+p,headers=h),context=ctx,timeout=10))
out=[]
for cid in IDS:
 call=get('calls/'+cid); chat=get('chat/'+cid)
 out.append({'call_id':cid,'status':call['status'],'messages':[{k:m.get(k) for k in ('role','status','text')} for m in chat['messages']]})
print(json.dumps(out,ensure_ascii=False))
""".replace('IDS', repr(call_ids))
    return json.loads(docker('exec', 'trainer112-backend-1', 'python', '-c', code))


def main(args):
    OUT.mkdir(parents=True, exist_ok=True)
    teacher, student = client('prepod'), client(args.student)
    call = lambda c, method, path, body=None: c.call(method, path, body)[1]
    me = call(student, 'GET', '/auth/me')
    print('Synthesizing operator speech', flush=True)
    first = speech('first', 'Система сто двенадцать, оператор слушает. Что у вас случилось и по какому адресу?',
                   'Уточните, пожалуйста, есть ли пострадавшие и в каком они состоянии?')
    second = speech('repeat', 'Сто двенадцать, слушаю вас. Что изменилось?')
    env = dotenv_values(ROOT / '.env.docker')
    password = json.loads(env['SIP_ACCOUNTS_JSON'])['220']
    evidence = {'scenario_id': SCENARIO, 'steps': []}
    with tempfile.TemporaryDirectory(prefix='trainer112-112-') as temp:
        secret = Path(temp) / 'password'
        secret.write_text(password, encoding='utf-8')
        probe = phone(secret, first, OUT / 'first', 110)
        group = call(teacher, 'POST', '/instructor/groups', {'title': 'SIP-приёмка 112 ' + uuid4().hex[:6]})
        call(teacher, 'POST', f'/instructor/groups/{group["id"]}/members', {'student_id': me['id']})
        lesson = call(teacher, 'POST', '/instructor/lessons', {
            'title': 'Приёмка: оператор 112 по SIP', 'group_id': group['id'], 'mode': 'fill',
            'scenario_ids': [SCENARIO], 'cards_per_student': 1, 'transport': 'sip',
            'sip_extensions': {me['id']: '220'}})
        evidence['lesson_id'] = lesson['id']
        call(teacher, 'POST', f'/instructor/lessons/{lesson["id"]}/start', {})
        workspace = call(student, 'POST', f'/student/lessons/{lesson["id"]}/next', {})
        sid = evidence['session_id'] = workspace['id']
        base = '/student/sessions/' + sid
        started = call(student, 'POST', base + '/call')
        evidence['first_call_id'] = started.get('call_id')
        print('First call', evidence['first_call_id'], flush=True)
        # The operator saves the card with the dispatched service while talking.
        time.sleep(20)
        card = {**workspace['card'], 'city': 'Москва', 'description': 'Со слов заявителя: плохо с сердцем.',
                'services': ['Служба 103']}
        saved = student.call('PUT', base + '/card', {'revision': workspace['revision'], 'card': card})
        evidence['steps'].append({'card_saved': saved[0]})
        print('Card saved', saved[0], flush=True)
        evidence['first_phone_log'] = wait_exit(probe, 140)[-4000:]
        # The line is free: the service status and the repeated call may arrive.
        probe = phone(secret, second, OUT / 'repeat', 70)
        repeat = None
        for _ in range(40):
            value = call(student, 'POST', base + '/updates')
            repeat = value.get('repeat_call_status') or []
            if repeat:
                break
            time.sleep(3)
        evidence['service_states'] = value.get('service_states')
        evidence['status_events'] = [e for e in value['events'] if e['type'] == 'service.status_received']
        evidence['repeat_call_status'] = repeat
        evidence['situation_updates'] = value.get('situation_updates')
        evidence['description_after'] = value['card'].get('description')
        print('Repeat call', repeat, flush=True)
        evidence['repeat_phone_log'] = wait_exit(probe, 110)[-4000:]
        ids = [evidence['first_call_id'], *[item['call_id'] for item in repeat]]
        evidence['voice'] = voice_evidence([i for i in ids if i])
        final = call(student, 'POST', base + '/updates')
        evidence['repeat_call_status_final'] = final.get('repeat_call_status')
    (OUT / 'result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding='utf-8')
    print('Evidence:', OUT / 'result.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--student', default='kursant2', help='Demo student account (not a real trainee)')
    main(parser.parse_args())
