"""Пройти одну карточку ИИ от начала до конца, как это сделал бы обучающийся.

Нужно для экранов результата в руководствах: оценка, разбор и рекомендации.
Создаёт у преподавателя занятие «Занятие с ИИ: смена» (текстовый канал),
если его нет, и проходит в нём карточку под kursant1. Учебные данные синтетические.

    python tools/manuals/demo_attempt.py https://АДРЕС
"""
import json
import ssl
import sys
import time
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else 'https://212.22.78.93').rstrip('/')
PASSWORD = 'Trener112-2026'
TITLE = 'Занятие с ИИ: смена'
CTX = ssl._create_unverified_context()


class Client:
    def __init__(self, username):
        self.cookie = ''
        self.call('POST', '/auth/login', {'username': username, 'password': PASSWORD})

    def call(self, method, path, body=None, ok=(200, 201)):
        request = urllib.request.Request(BASE + '/api/v1' + path, method=method,
                                         data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                         headers={'Content-Type': 'application/json', 'Origin': BASE, 'X-Voice-UI': '1',
                                                  **({'Cookie': self.cookie} if self.cookie else {})})
        try:
            with urllib.request.urlopen(request, context=CTX, timeout=120) as response:
                cookie = response.headers.get('set-cookie', '')
                if cookie:
                    self.cookie = cookie.split(';')[0]
                raw = response.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode()[:400]
            if error.code in ok:
                return detail
            raise SystemExit(f'{method} {path} -> {error.code}: {detail}')


teacher = Client('prepod')
group = next(g for g in teacher.call('GET', '/instructor/groups') if g['title'] == 'Смена А (демо)')
lesson = next((l for l in teacher.call('GET', '/instructor/lessons') if l['title'] == TITLE and l['state'] == 'running'), None)
if not lesson:
    lesson = teacher.call('POST', '/instructor/lessons', {
        'title': TITLE, 'group_id': group['id'], 'mode': 'actions', 'ai_cards': True,
        'dds_profile': 'utilities', 'cards_per_student': 3, 'transport': 'text'})
    teacher.call('POST', f"/instructor/lessons/{lesson['id']}/start")
    print('создано занятие', TITLE)

student = Client('kursant1')
card = student.call('POST', f"/student/lessons/{lesson['id']}/next", {})
path = f"/student/sessions/{card['id']}"
card = student.call('POST', path + '/open', {})
service = card['owner_service']
c = card['card']
summary = (f"{c['incident_type']}. Адрес: {c.get('city', 'Москва')}, улица {c.get('street', '')}, дом {c.get('house', '')}. "
           f"{c['description']}")
print('карточка', card['number'], card['card']['incident_type'])
time.sleep(4)
student.call('POST', path + '/services', {'service': service, 'status': 'Принята',
                                          'comment': 'Принята в работу. Происшествие в зоне ответственности службы.'},
             ok=(200, 201, 422))  # повторный запуск продолжает уже принятую карточку
crew = card['crew_options'][0]
student.call('POST', path + '/crew', {'message_id': str(uuid.uuid4()), 'crew_id': crew['id'], 'decision_by': 'dispatcher'}, ok=(200, 201, 409, 422))
contacts = student.call('GET', path + '/phone/contacts')


def talk(contact, text, question=''):
    started = student.call('POST', path + '/phone/dial', {'message_id': str(uuid.uuid4()), 'number': contact['number'], 'transport': 'text'})
    bid = started['id']
    for phrase in [text] + ([question] if question else []):
        answer = student.call('POST', path + f'/briefings/{bid}/messages', {'message_id': str(uuid.uuid4()), 'text': phrase})
        print(f"  ☎ {contact['name']}: «{answer['messages'][-1]['content']}»")
    student.call('POST', path + f'/briefings/{bid}/finish', {'message_id': str(uuid.uuid4()), 'recipient': started['recipient_hint']}, ok=(200, 409))


by_role = {c['role']: c for c in contacts}
talk(by_role['crew_leader'], f'Диспетчер ДДС. Адрес: {summary} Выезжайте, доложите по прибытии.', 'Сколько вам ехать?')
talk(by_role['superior'], f'Докладываю: {summary} Карточка принята, бригада направлена.')
talk(by_role['organization_head'], f'Диспетчер ДДС. По адресу {summary} Нужен доступ для бригады.', 'Где ключи и кто встретит?')
talk(by_role['crew_specialist'], f'По адресу {summary} Что понадобится для работ?')
if 'applicant' in by_role:
    talk(by_role['applicant'], 'Здравствуйте, это дежурная служба. Уточните, пожалуйста, подъезд и как пройти.')

done = set()
last_report = ''
deadline = time.time() + 600
while time.time() < deadline:
    state = student.call('POST', path + '/updates', {})
    for update in state.get('situation_updates', []):
        if update['id'] in done or not update.get('unlocks_status'):
            continue
        text = update.get('text') or ''
        student.call('POST', path + '/services', {'service': service, 'status': update['unlocks_status'],
                                                  'comment': (update['unlocks_status'] + ': ' + text)[:900]},
                     ok=(200, 201, 409, 422))  # при повторном запуске статус уже стоит
        done.add(update['id'])
        last_report = text
        print('  доклад:', update['unlocks_status'])
    if any(u.get('unlocks_status') == 'Работы завершены' for u in state.get('situation_updates', []) if u['id'] in done):
        break
    time.sleep(10)
talk(by_role['superior'], f'Докладываю о результате: {last_report} Работы завершены, происшествие отработано.')
student.call('POST', path + '/processed', {})
result = student.call('POST', path + '/finish', {}, ok=(200, 409))
review = result.get('dds_review', {}) if isinstance(result, dict) else {}
print('итог:', review.get('score_percent'), '%', 'зачтено' if review.get('passed') else 'не зачтено', result if not review else '')
print('SESSION', card['id'])

# Разбор ИИ считается в фоне после завершения; ждём его, как ждал бы обучающийся.
started = time.time()
while time.time() - started < 600:
    ai = (student.call('GET', path) or {}).get('ai_review') or {}
    if ai.get('status') != 'pending':
        break
    time.sleep(15)
print('разбор ИИ:', ai.get('status'), f'через {int(time.time() - started)} с', ai.get('error') or (ai.get('summary') or '')[:300])
for finding in (ai.get('findings') or [])[:6]:
    print('  -', finding.get('kind'), '|', (finding.get('explanation') or '')[:160])
