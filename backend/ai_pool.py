"""Autonomous AI card supply: the model authors DDS exercises ahead of time and each
student receives an unused one without a teacher in the loop.

A complete exercise takes minutes on the minimal CPU server, so the pool is filled
in the background and a card is issued instantly. Only exercises that passed the
same structural validation as teacher drafts enter the pool; the teacher can review
every issued AI card afterwards in the attempt report.
"""
import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from cluster import LockUnavailable, ClusterUnavailable
from dds_authoring import service_for
from practice_plan import fingerprint as practice_fingerprint

logger = logging.getLogger('ai_pool')
# A conversation in progress has priority over the background pool on a CPU server.
# While a lesson is on, calls come every few minutes: background authoring waits
# for ten quiet minutes so a call never shares the CPU with it.
PHONE_QUIET_SECONDS = float(os.getenv('AI_POOL_PHONE_QUIET_SECONDS', '600'))


class PhoneBusy(Exception):
    pass

# Simple household situations, as the customer recommended: no fires or traffic
# accidents. The model writes the address, circumstances, contacts and reports.
TOPICS = {
    'utilities': [
        'мусор выброшен в неположенном месте во дворе',
        'животное застряло в вентиляционной шахте жилого дома',
        'нет доступа в подъезд: не работает домофон или замок',
        'протечка воды с крыши в квартиру верхнего этажа',
        'засор канализации, вода в подвале жилого дома',
        'не работает лифт, в доме маломобильный жилец',
        'упало дерево во дворе и перегородило проезд',
        'открыт канализационный люк на тротуаре',
        'нет горячей воды во всём подъезде',
        'отсутствует освещение на лестничной клетке',
        'сосульки и наледь над входом в подъезд',
        'переполнена контейнерная площадка',
        'порыв трубы холодного водоснабжения во дворе',
        'не закрывается входная дверь подъезда',
        'в подвале дома шумит и капает вода',
        'брошенные строительные отходы у подъезда',
        'птица залетела в подъезд и не может выбраться',
        'кошка застряла на дереве во дворе',
        'бездомная собака не даёт пройти к подъезду',
        'течёт батарея в квартире, соседи снизу жалуются',
        'не работает уличный фонарь у детской площадки',
        'сломана скамейка и торчат гвозди на детской площадке',
        'засорён мусоропровод, запах на лестнице',
        'дверь в подвал открыта, туда заходят посторонние',
        'на крыше гремит оторванный лист кровли',
        'большая лужа перекрыла вход в подъезд после дождя',
        'брошенный автомобиль мешает вывозу мусора',
        'не работает домофон, жильцы не могут открыть дверь гостям',
        'граффити на стене подъезда',
        'шум от неисправной вентиляции в подвале',
    ],
}
TOPICS['general'] = TOPICS['utilities']

# Names are chosen by the server: otherwise a small model repeats the same ones.
def sentence_case(text):
    """«ДДС: КОШКА ЗАСТРЯЛА» → «ДДС: Кошка застряла»; normal text is left as is."""
    if not isinstance(text, str) or re.search('[а-яё]', text):
        return text
    prefix, _, rest = text.rpartition(': ')
    rest = rest[:1] + rest[1:].lower()
    return f'{prefix}: {rest}' if prefix else rest


FULL_NAMES = ['Алексей Белов', 'Марина Громова', 'Сергей Киселёв', 'Ольга Лаптева', 'Павел Мельников',
              'Наталья Носова', 'Игорь Орехов', 'Светлана Полякова', 'Роман Рябов', 'Татьяна Сафонова',
              'Виктор Тихонов', 'Юлия Устинова', 'Андрей Фомин', 'Екатерина Чернова', 'Денис Шилов',
              'Ирина Яковлева', 'Максим Зуев', 'Галина Жукова', 'Николай Ершов', 'Вера Кудрявцева']


def now():
    return datetime.now(timezone.utc).isoformat()


class Pool:
    def __init__(self, store, coordinator, produce, provider=lambda: 'mock'):
        self.store = store
        self.coordinator = coordinator
        self.produce = produce
        self.provider = provider
        self.target = max(0, min(100, int(os.getenv('AI_POOL_TARGET', '4'))))
        self.profiles = [p.strip() for p in os.getenv('AI_POOL_PROFILES', 'utilities').split(',') if p.strip() in TOPICS]
        self.difficulty = os.getenv('AI_POOL_DIFFICULTY', 'basic')
        self.wake = asyncio.Event()
        self.task = None
        self.generating = None
        self.last_error = ''
        with store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS ai_card_pool (id TEXT PRIMARY KEY, profile TEXT NOT NULL, '
                             'difficulty TEXT NOT NULL, topic TEXT NOT NULL, status TEXT NOT NULL, student_id TEXT, '
                             'created_at TEXT NOT NULL, issued_at TEXT, body TEXT NOT NULL)')
            store.db.execute('CREATE TABLE IF NOT EXISTS ai_pool_activity (id TEXT PRIMARY KEY, at DOUBLE PRECISION NOT NULL)')

    def mark_phone(self):
        """Shared across Backend replicas: a call on any of them pauses generation everywhere."""
        with self.store.db:
            self.store.db.execute('INSERT INTO ai_pool_activity VALUES (?,?) ON CONFLICT (id) DO UPDATE SET at=excluded.at',
                                  ('phone', time.time()))

    def mark_call(self):
        """A real phone request (not a review or a check) on any replica."""
        with self.store.db:
            self.store.db.execute('INSERT INTO ai_pool_activity VALUES (?,?) ON CONFLICT (id) DO UPDATE SET at=excluded.at',
                                  ('call', time.time()))

    def last_call(self):
        row = self.store.db.execute("SELECT at FROM ai_pool_activity WHERE id='call'").fetchone()
        return float(row[0]) if row else 0.0

    def call_active(self, seconds):
        return time.time() - self.last_call() < seconds

    def phone_busy(self):
        row = self.store.db.execute("SELECT at FROM ai_pool_activity WHERE id='phone'").fetchone()
        return bool(row and time.time() - float(row[0]) < PHONE_QUIET_SECONDS)

    def counts(self):
        rows = self.store.db.execute("SELECT profile, count(*) FROM ai_card_pool WHERE status='ready' GROUP BY profile").fetchall()
        return {row[0]: row[1] for row in rows}

    def status(self):
        issued = self.store.db.execute("SELECT count(*) FROM ai_card_pool WHERE status='issued'").fetchone()[0]
        return {'enabled': self.target > 0, 'target': self.target, 'profiles': self.profiles,
                'ready': self.counts(), 'issued': issued, 'generating': self.generating,
                'paused_for_calls': self.phone_busy(),
                'last_error': self.last_error}

    def take(self, profile, difficulty, student_id):
        """Atomically reserve an unused exercise; a student never gets a repeated topic while others remain."""
        profile = profile if profile in TOPICS else 'utilities'
        rows = self.store.db.execute(
            "SELECT id, topic, difficulty FROM ai_card_pool WHERE status='ready' AND profile=? ORDER BY created_at, id",
            (profile,)).fetchall()
        seen = {row[0] for row in self.store.db.execute(
            "SELECT topic FROM ai_card_pool WHERE status='issued' AND student_id=?", (student_id,)).fetchall()}
        rows.sort(key=lambda row: (row[1] in seen, bool(difficulty) and row[2] != difficulty))
        for pid, topic, _ in rows:
            with self.store.db:
                cursor = self.store.db.execute(
                    "UPDATE ai_card_pool SET status='issued', student_id=?, issued_at=? WHERE id=? AND status='ready'",
                    (student_id, now(), pid))
                if cursor.rowcount != 1:
                    continue
                body = self.store.db.execute('SELECT body FROM ai_card_pool WHERE id=?', (pid,)).fetchone()[0]
            self.wake.set()
            return json.loads(body)
        self.wake.set()
        return None

    def next_topic(self, profile):
        recent = [row[0] for row in self.store.db.execute(
            'SELECT topic FROM ai_card_pool WHERE profile=? ORDER BY created_at DESC LIMIT 12', (profile,)).fetchall()]
        topics = TOPICS[profile]
        unused = [topic for topic in topics if topic not in recent]
        return random.choice(unused or topics)

    async def fill_once(self):
        """Generate one exercise for the most depleted profile. False — nothing to do or busy."""
        counts = self.counts()
        needed = [p for p in self.profiles if counts.get(p, 0) < self.target]
        if not needed:
            return False
        if self.phone_busy():
            raise PhoneBusy()
        profile = min(needed, key=lambda p: counts.get(p, 0))
        try:
            lease = await self.coordinator.acquire('ai-pool', 'refill', timeout_s=0)
        except (LockUnavailable, ClusterUnavailable):
            return False
        try:
            if self.counts().get(profile, 0) >= self.target:
                return True
            topic = self.next_topic(profile)
            sid = 'ai_' + uuid4().hex
            owner = service_for(profile, 'other')
            self.generating = {'profile': profile, 'topic': topic, 'started_at': now()}
            names = ', '.join(random.sample(FULL_NAMES, 5))
            brief = (f'Тема: {topic}. Придумай свои конкретные обстоятельства, вымышленный адрес '
                     f'в Москве, собеседников и доклады. Ситуация бытовая и простая, без пострадавших. '
                     f'Имена собеседников: {names}. '
                     'У каждого собеседника свой факт, которого нет в карточке, — его нужно узнать по телефону.')
            if random.random() < 0.5:
                brief += ' Добавь заявителя пятым контактом: у него есть бытовая подробность, которой нет в карточке.'
            curriculum = {'difficulty': self.difficulty, 'dds_profile': profile,
                          'learning_objectives': 'Полный цикл ДДС: приём карточки, бригада, доклады, закрытие.'}
            work = asyncio.ensure_future(self.produce(
                brief, 'other', sid, curriculum=curriculum, mode='dds', owner_service=owner,
                teacher_id='', automatic=True))
            while not work.done():
                await asyncio.wait({work}, timeout=2)
                if not work.done() and self.phone_busy():
                    # Closing the request makes Ollama stop at once; the call gets the CPU.
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    logger.info('AI pool: generation paused for a phone call')
                    raise PhoneBusy()
            scenario, rubric, config, _ = work.result()
            # The model sometimes writes a title in capitals («КОШКА ЗАСТРЯЛА»).
            card = scenario.get('prefilled_card') or {}
            for holder, key in ((scenario, 'title'), (card, 'incident_type')):
                holder[key] = sentence_case(holder.get(key))
            # The model sometimes breaks a given name («Лапт-ева», «Лапт及ева»).
            for contact in scenario.get('phone_contacts') or []:
                plain = re.sub(r'[^А-Яа-яЁё ]', '', str(contact.get('name') or ''))
                if plain != contact.get('name') and plain in FULL_NAMES:
                    contact['name'] = plain
            scenario['enabled'] = True
            # The structure was validated like a teacher draft; prepared hints are
            # part of that validated exercise, not live improvisation.
            scenario['practice_approved_version'] = practice_fingerprint(scenario)
            meta = {'model': config.get('model', ''), 'provider': config.get('provider', ''),
                    'generated_at': now(), 'topic': topic}
            body = {'scenario': scenario, 'rubric': rubric, 'ai_generated': meta}
            with self.store.db:
                self.store.db.execute('INSERT INTO scenarios VALUES (?,?)', (sid, json.dumps(scenario, ensure_ascii=False)))
                self.store.db.execute('INSERT INTO scenario_owners VALUES (?,?)', (sid, 'ai-pool'))
                self.store.db.execute('INSERT INTO ai_card_pool VALUES (?,?,?,?,?,?,?,?,?)',
                                      (sid, profile, self.difficulty, topic, 'ready', None, meta['generated_at'], None,
                                       json.dumps(body, ensure_ascii=False)))
            self.last_error = ''
            logger.info('AI pool: exercise ready profile=%s topic=%s', profile, topic)
            return True
        except PhoneBusy:
            raise
        except Exception as error:
            self.last_error = type(error).__name__
            logger.warning('AI pool generation failed: %s: %s', type(error).__name__, str(error)[:300])
            raise
        finally:
            self.generating = None
            await self.release_model()
            await lease.release()

    async def release_model(self):
        """Restart the runner between background exercises while no call is active.

        The CPU runner's memory grows with every long structured generation, so it
        is unloaded — and loaded again at once: the phone model must stay resident,
        otherwise the next call pays the load and loses the prepared role.
        """
        if self.provider() != 'ollama' or self.phone_busy():
            return
        import httpx
        import llm
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                await client.post(os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434') + '/api/generate',
                                  json={'model': llm.configuration()['model'], 'keep_alive': 0})
        except Exception:
            logger.warning('AI pool: model unload failed')
        try:
            await llm.warm_phone_model()
        except Exception:
            logger.warning('AI pool: phone model reload failed')

    async def run(self):
        await asyncio.sleep(float(os.getenv('AI_POOL_START_DELAY_SECONDS', '45')))
        while True:
            try:
                worked = self.provider() != 'none' and await self.fill_once()
            except asyncio.CancelledError:
                raise
            except PhoneBusy:
                await asyncio.sleep(15)
                continue
            except Exception:
                worked = False
                await asyncio.sleep(120)
            if not worked:
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 300)
                except TimeoutError:
                    pass

    def start(self):
        if self.target and self.profiles and self.task is None:
            self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
