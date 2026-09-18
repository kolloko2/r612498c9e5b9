"""Local training dialogue and scenario service; never contacts emergency services."""
import asyncio
from difflib import SequenceMatcher
import hmac
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, field_validator
from llm import complete, configuration, reply as speak
from briefing import check as briefing_check, duty_reply as briefing_duty_reply
from categories import CategoryId
from curriculum import Difficulty, DdsProfile
from database import connect_database

MODEL = os.getenv("DIALOGUE_MODEL", "qwen3:1.7b")
TOKEN = os.environ.get("DIALOGUE_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and not DATABASE_URL.startswith(("postgresql://", "postgres://")):
    raise RuntimeError("DATABASE_URL must be a PostgreSQL URL; use DIALOGUE_DB for SQLite")
DB = DATABASE_URL or os.getenv("DIALOGUE_DB", "dialogue.sqlite3")
SEED = json.loads(Path(__file__).with_name("scenario.json").read_text(encoding="utf-8"))
app = FastAPI(title="Учебный диалог 112")


class SituationUpdate(BaseModel):
    """Вводная: новое обстоятельство, приходящее по ходу работы с карточкой.

    Заказчик просил моделировать полный жизненный цикл происшествия, а не
    заполнение формы. Настоящая работа диспетчера состоит в том, что обстановка
    меняется: бригада не проехала, появился второй пострадавший, заявитель
    перезвонил. Вводная приходит через заданное время после выдачи карточки и
    требует от диспетчера пересмотреть решение.

    Срабатывание детерминированное — по секундомеру карточки, а не по решению
    модели: одна и та же карточка обязана вести себя одинаково у всех
    обучающихся группы.
    """
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    # Отсчёт идёт от выдачи карточки: норматив реакции считается оттуда же.
    after_seconds: int = Field(ge=5, le=3600)
    text: str = Field(min_length=3, max_length=600)
    # Источник вводной виден диспетчеру: он должен понимать, кто сообщил.
    source: str = Field("Служба 112", min_length=1, max_length=120)
    # Статус, который эта вводная открывает. Диспетчер не ставит «Прибытие»
    # раньше, чем ему сообщили о прибытии: статус отражает доклад с места, а не
    # желание обучающегося прокликать цепочку до конца.
    unlocks_status: str = Field("", max_length=60)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    title: str = Field(min_length=3, max_length=120)
    category_id: CategoryId = 'other'
    difficulty: Difficulty = 'basic'
    dds_profile: DdsProfile = 'general'
    learning_objectives: str = Field('', max_length=1500)
    description: str = Field("", max_length=1000)
    victim_name: str = Field(min_length=1, max_length=80)
    incident: str = Field(min_length=3, max_length=1000)
    location: str = Field(min_length=3, max_length=500)
    known_facts: list[str] = Field(min_length=1, max_length=30)
    unknown_facts: list[str] = Field(default_factory=list, max_length=20)
    emotion: str = Field(min_length=2, max_length=200)
    behavior: str = Field("", max_length=1000)
    opening: str = Field(min_length=3, max_length=1000)
    # До шести вводных на сценарий: больше превращает занятие в поток
    # уведомлений, за которым не видно работы с карточкой.
    updates: list[SituationUpdate] = Field(default_factory=list, max_length=6)
    # Служба, в которой работает обучающийся. Он ведёт статусы только своей
    # ДДС; остальные назначенные службы видит, но не трогает — так устроен
    # реальный АРМ.
    owner_service: str = Field("", max_length=160)
    enabled: bool = True

    @field_validator("known_facts", "unknown_facts")
    @classmethod
    def clean_facts(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if value and value not in result:
                result.append(value)
        return result


class ScenarioView(Scenario):
    editable: bool
    version: str


class ScenarioUpdate(Scenario):
    version: str = Field(pattern=r'^[a-f0-9]{64}$')


def scenario_version(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def normalize_seed(value):
    if "victim_name" in value:
        return Scenario.model_validate(value)
    return Scenario(
        id=value.get("id", "fire_victim_v2"), title=value.get("title", "Пожар"),
        description=value.get("brief", "Учебный вызов 112"), victim_name="Анна",
        incident="Пожар в квартире, сильный дым из кухни.",
        location="Москва, улица Лесная, дом 12, квартира 34, третий этаж.",
        known_facts=["В квартире находятся Анна и её восьмилетний сын", "Выход через кухню отрезан дымом", "Оба находятся у открытого окна"],
        unknown_facts=["Точная причина пожара", "Есть ли другие люди в подъезде"],
        emotion="Испугана, говорит коротко, иногда сбивается, но отвечает оператору.",
        behavior="Не придумывает факты. Если не знает ответ, прямо говорит, что не знает.",
        opening=value.get("opening", "Это учебный звонок. Помогите, у нас пожар, мы не можем выйти!"), enabled=True)


def rules_for(scenario):
    known = "\n".join(f"- {item}" for item in scenario["known_facts"])
    unknown = "\n".join(f"- {item}" for item in scenario["unknown_facts"]) or "- нет"
    return f"""Ты играешь пострадавшего в учебном звонке 112. Ты — {scenario['victim_name']}. Человек на линии — оператор 112.
Происшествие: {scenario['incident']}
Место: {scenario['location']}
Известные тебе факты:
{known}
Неизвестные тебе факты:
{unknown}
Состояние: {scenario['emotion']}
Поведение: {scenario['behavior']}
Ты звонишь за помощью для себя. Все сообщения user — слова оператора, обращённые к тебе. Не продолжай фразу оператора от его лица.
Если оператор спрашивает «Как я могу помочь?», объясни своё происшествие и попроси помочь тебе. Не отвечай «Помогу» или «Чем могу помочь».
Отвечай на последний вопрос с учётом предыдущего разговора. Не меняй адрес, людей и обстоятельства. Если факта нет, скажи «Не знаю»; не выдумывай действия или спасение. На непонятную фразу попроси уточнить. Не повторяй приветствие каждый раз.
Отвечай только от лица пострадавшего, по-русски, одним или двумя законченными короткими предложениями, не более 40 слов. Текст будет произнесён вслух: без Markdown, списков, скобок с эмоциями и названий ролей. Используй обычную разговорную речь и пунктуацию для пауз. Не становись оператором, не давай оценку обучаемому и не добавляй сведений, которых нет в сценарии. Это только тренажёр; не заявляй, что настоящий вызов принят или помощь отправлена."""


def spoken_reply(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"^\s*(?:Пострадавший|Пострадавшая|Ответ|Анна)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"\*(?:плачет|вздыхает|кричит|шепчет)\*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text.replace('*', '').replace('`', '')).strip(' «»"')
    if not text:
        raise ValueError("Empty spoken reply")
    if text[-1] not in '.!?…':
        text += '.'
    return text


def wrong_role(text):
    return bool(re.search(r"\b(?:помогу|чем (?:я )?могу помочь|ваш вызов принят|помощь (?:уже )?отправлена|назовите (?:ваш )?адрес|служба 112 слушает)\b", text, re.I))


def people_answer(utterance, scenario):
    if not re.search(r"(?:вы (?:там )?од(?:ин|на|ни)|с вами (?:кто|есть)|кто (?:с вами|рядом)|сколько (?:вас|людей))", utterance, re.I):
        return None
    # Use only an explicit scenario fact; never infer that the victim is alone.
    for fact in scenario['known_facts']:
        if re.search(r'находятся|со мной|с нами', fact, re.I):
            name = scenario.get('victim_name', '')
            if name:
                fact = re.sub(r'\b' + re.escape(name) + r'\b', 'я', fact)
                fact = fact.replace('её ', 'мой ').replace('его ', 'мой ')
                fact = fact.replace('находятся я', 'находимся я')
            return spoken_reply(fact)
    return None


def likely_playback_echo(utterance, messages):
    """Detect a microphone transcription of a recently synthesized reply."""
    normalized = " ".join(utterance.casefold().split())
    if len(normalized) < 18:
        return False
    recent = [message["content"] for message in messages[-6:] if message["role"] == "assistant"]
    for reply in recent[-2:]:
        candidate = " ".join(reply.casefold().split())
        if normalized in candidate and len(normalized) >= len(candidate) * 0.55:
            return True
        if candidate in normalized and len(candidate) >= len(normalized) * 0.55:
            return True
        if SequenceMatcher(None, normalized, candidate).ratio() >= 0.72:
            return True
    return False


class Store:
    def __init__(self, path):
        self.db = connect_database(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS scenarios (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS scenario_owners (scenario_id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL)")
        seed = normalize_seed(SEED)
        self.db.execute("INSERT INTO scenarios VALUES (?, ?) ON CONFLICT (id) DO NOTHING", (seed.id, seed.model_dump_json()))
        self.db.commit()

    def load(self, sid):
        row = self.db.execute("SELECT body FROM sessions WHERE id=?", (sid,)).fetchone()
        return json.loads(row[0]) if row else {"step": 0, "seq": 0, "messages": [], "replies": {}, "ended": False}

    def save(self, sid, state):
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES (?, ?) ON CONFLICT (id) DO UPDATE SET body=excluded.body", (sid, json.dumps(state, ensure_ascii=False)))

    def list_scenarios(self):
        rows = self.db.execute("SELECT body FROM scenarios ORDER BY lower(json_text(body, 'title')), id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def scenario(self, scenario_id):
        row = self.db.execute("SELECT body FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def first_enabled(self):
        return next((item for item in self.list_scenarios() if item["enabled"]), None)

    def put_scenario(self, scenario):
        body = scenario.model_dump()
        with self.db:
            self.db.execute("INSERT INTO scenarios VALUES (?, ?) ON CONFLICT (id) DO UPDATE SET body=excluded.body", (scenario.id, json.dumps(body, ensure_ascii=False)))
        return body

    def delete_scenario(self, scenario_id):
        with self.db:
            return self.db.execute("DELETE FROM scenarios WHERE id=?", (scenario_id,)).rowcount


async def generate(messages):
    """Реплика собеседника: у рассуждающих моделей иначе в ответ идут размышления."""
    text = await speak(messages)
    if not text:
        raise ValueError("Empty model reply")
    return text


class Engine:
    def __init__(self, store, model=generate):
        self.store, self.model = store, model
        self.locks = {}

    async def handle(self, sid, event):
        # REST and Voice share one conversation and must not overwrite each other.
        async with self.locks.setdefault(sid, asyncio.Lock()):
            reply = await self._handle(sid, event)
            # Persist receipt even for control events which have no caller reply.
            state = self.store.load(sid)
            eid = str(UUID(event['event_id']))
            if eid not in state['replies']:
                state['replies'][eid] = reply
                self.store.save(sid, state)
            return reply

    async def _handle(self, sid, event):
        if event.get("session_id") != sid:
            raise ValueError("Session mismatch")
        eid = str(UUID(event["event_id"]))
        state = self.store.load(sid)
        if eid in state["replies"]:
            return state["replies"][eid]
        kind = event["type"]
        if kind == 'session.resume':
            previous=event.get('payload',{}).get('previous_call_id')
            state['superseded_calls']=list(dict.fromkeys([*state.get('superseded_calls',[]),previous]))[-20:]
            state['ended']=False
            self.store.save(sid,state)
            return None
        if kind == "call.ended":
            call_id=event.get('payload',{}).get('call_id')
            if call_id and call_id in state.get('superseded_calls',[]):return None
            state["ended"] = True
            state['last_call_end']={key:event.get('payload',{}).get(key) for key in ('call_id','reason','status')}
            self.store.save(sid, state)
            return None
        if event.get("payload", {}).get("mode") != "auto":
            return None
        if state["ended"] or kind not in ("call.connected", "operator.utterance"):
            return None
        duty = state.get("duty")
        if kind == "call.connected":
            if state["messages"]:
                return None
            if duty:
                # Доклад из ДДС в службу: собеседник принимает информацию, а не просит помощи.
                text = f"Дежурный, {duty['service']}. Слушаю вас."
            else:
                requested = event.get("payload", {}).get("scenario_id")
                scenario = state.get("scenario") or (self.store.scenario(requested) if requested else self.store.first_enabled())
                if not scenario or not scenario["enabled"]:
                    raise ValueError("Scenario unavailable")
                state["scenario"] = scenario
                text = scenario["opening"]
        elif duty:
            utterance = event["payload"]["text"].strip()[:4000]
            if not utterance:
                return None
            if likely_playback_echo(utterance, state["messages"]):
                state["replies"][eid] = None
                state["echoes_ignored"] = state.get("echoes_ignored", 0) + 1
                self.store.save(sid, state)
                return None
            spoken = " ".join([*(m["content"] for m in state["messages"] if m["role"] == "user"), utterance])
            # Полнота доклада считается по сохранённой карточке, а не моделью:
            # ответ собеседника не может подтвердить приём вместо проверки.
            state["duty_report"] = briefing_check(spoken, duty["card"])
            history = [{"role": m["role"], "content": m["content"]} for m in state["messages"][-8:]]
            history.append({"role": "user", "content": utterance})
            try:
                text = spoken_reply(await briefing_duty_reply(history, duty["card"], duty["service"],
                                                              state["duty_report"]["missing"]))
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                text = "Повторите, пожалуйста, последнюю фразу."
                state["provider_error"] = "Сервис диалога недоступен. Повторите запрос позднее."
            else:
                state.pop("provider_error", None)
            state["messages"].append({"role": "user", "content": utterance})
        else:
            utterance = event["payload"]["text"].strip()[:4000]
            if not utterance:
                return None
            if likely_playback_echo(utterance, state["messages"]):
                state["replies"][eid] = None
                state["echoes_ignored"] = state.get("echoes_ignored", 0) + 1
                self.store.save(sid, state)
                return None
            scenario = state.get("scenario")
            if not scenario:
                raise ValueError("Scenario was not selected")
            history = state["messages"][-20:] + [{"role": "user", "content": utterance}]
            try:
                text = people_answer(utterance, scenario) or await self.model([{"role": "system", "content": rules_for(scenario)}] + history)
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Empty model response")
                text = spoken_reply(text)
                if wrong_role(text):
                    text = spoken_reply(await self.model(
                        [{"role": "system", "content": rules_for(scenario)}] + history +
                        [{"role": "system", "content": "Исправь роль: ты пострадавший, который просит помощи. Ответь на последний вопрос оператора только фактами сценария. Не предлагай помощь оператору."}]))
                    if wrong_role(text):
                        raise ValueError("Role reversal")
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                text = "Не расслышала вас. Повторите, пожалуйста."
                state["provider_error"] = "Сервис диалога недоступен. Повторите запрос позднее."
            else:
                state.pop("provider_error", None)
            state["messages"].append({"role": "user", "content": utterance})
        state["messages"].append({"role": "assistant", "content": text})
        state["seq"] += 1
        payload = {"reply_id": str(uuid4()), "text": text, "should_interrupt": False}
        if kind == "operator.utterance":
            payload["utterance_id"] = str(UUID(event["payload"]["utterance_id"]))
        reply = {"event_id": str(uuid4()), "seq": state["seq"], "session_id": sid,
                 "type": "caller.reply", "elapsed_ms": event.get("elapsed_ms", 0), "payload": payload}
        state["replies"][eid] = reply
        self.store.save(sid, state)
        return reply


store = Store(DB)
from cluster import Coordinator, coordinate_engine, install_global_mutation_guard, LockUnavailable, ClusterUnavailable
coordinator = Coordinator.from_database(store.db)
engine = coordinate_engine(Engine(store), coordinator)
install_global_mutation_guard(app, coordinator)
active = set()
warmup_task = None


async def warm_model():
    try:
        async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
            await client.post("http://127.0.0.1:11434/api/generate",
                              json={"model": MODEL, "prompt": "", "stream": False, "keep_alive": -1,
                                    "options": {"num_ctx": 4096, "num_thread": 8, "num_batch": 256}})
    except httpx.HTTPError:
        pass


@app.on_event("startup")
async def preload_model():
    global warmup_task
    if configuration()["provider"] == "ollama":
        warmup_task = asyncio.create_task(warm_model())


def authorized(authorization: str = Header("")):
    if not TOKEN or not hmac.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401, "Неверный токен")


from workspace import router as workspace_router
from accounts import Accounts
from learning import Learning
accounts = Accounts(store)
from security_audit import install as install_security_audit
install_security_audit(app, accounts, authorized)
from operations import router as operations_router
app.include_router(operations_router(accounts, authorized))
from maps import router as maps_router
app.include_router(maps_router(accounts, authorized))
learning = Learning(store, accounts)
app.include_router(accounts.router(authorized))
from directory_routes import router as directory_router
from directory_auth import authenticate as directory_authenticate, status as directory_status
app.include_router(directory_router(accounts, authorized, directory_authenticate, lambda: directory_status()['configured']))
app.include_router(workspace_router(store, engine, authorized, accounts, learning, coordinator))
app.include_router(learning.router(authorized))
from generation import router as generation_router
app.include_router(generation_router(store, accounts, authorized, Scenario, coordinator))
from materials import router as materials_router, install_upload_limit
app.include_router(materials_router(store, accounts, learning, authorized))
install_upload_limit(app)
from assessment import router as assessment_router
app.include_router(assessment_router(store, accounts, learning, authorized, coordinator))
from certificates import router as certificate_router
app.include_router(certificate_router(store, accounts, learning, authorized))
from arm_dispatch import router as arm_dispatch_router
app.include_router(arm_dispatch_router(store, accounts, authorized))
from group_insights import router as group_insights_router
app.include_router(group_insights_router(store, accounts, learning, authorized, coordinator))
from tickets import router as tickets_router
app.include_router(tickets_router(store, accounts, authorized, Scenario))
from briefing import router as briefing_router
app.include_router(briefing_router(store, accounts, authorized, learning))


@app.get("/api/v1/health")
async def health():
    config = configuration()
    if config["provider"] != "ollama":
        return {"status": "ok" if config["configured"] else "model_unavailable", "scenarios": len(store.list_scenarios()), "cluster":coordinator.status(), **config}
    # Адрес берётся из окружения: в контейнере Ollama живёт на хосте, а не на
    # локальной петле. Модель сверяется с выбранным профилем, а не с переменной
    # DIALOGUE_MODEL, иначе смена профиля не отражается в диагностике.
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        try:
            response = await client.get(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434") + "/api/tags")
            response.raise_for_status()
            installed = {model.get("name", "") for model in response.json()["models"]}
            wanted = config["model"]
            ready = wanted in installed or any(name.startswith(wanted + ":") for name in installed)
        except (httpx.HTTPError, KeyError, TypeError):
            ready = False
    return {"status": "ok" if ready else "model_unavailable", "scenarios": len(store.list_scenarios()), "cluster":coordinator.status(), **config}


@app.get("/api/v1/scenarios", response_model=list[ScenarioView], dependencies=[Depends(authorized), Depends(accounts.require('teacher'))])
async def list_scenarios(user=Depends(accounts.require('teacher'))):
    return [scenario_view(s, user) for s in store.list_scenarios() if scenario_visible(s['id'], user)]


def scenario_view(value, user):
    owner = store.db.execute('SELECT teacher_id FROM scenario_owners WHERE scenario_id=?', (value['id'],)).fetchone()
    return {**value, 'editable': bool(owner and owner[0] == user['id']), 'version': scenario_version(value)}


def scenario_visible(scenario_id, user):
    owner = store.db.execute('SELECT teacher_id FROM scenario_owners WHERE scenario_id=?', (scenario_id,)).fetchone()
    return not owner or owner[0] == user['id']


def scenario_editable(scenario_id, user):
    owner = store.db.execute('SELECT teacher_id FROM scenario_owners WHERE scenario_id=?', (scenario_id,)).fetchone()
    if not owner or owner[0] != user['id']:
        raise HTTPException(403, 'Можно изменять только собственные сценарии. Общий шаблон сначала скопируйте.')


@app.post("/api/v1/scenarios/validate", dependencies=[Depends(authorized), Depends(accounts.require('teacher'))])
async def validate_scenario(scenario: Scenario):
    return {"valid": True, "rules": rules_for(scenario.model_dump())}


@app.post("/api/v1/scenarios", response_model=ScenarioView, status_code=201, dependencies=[Depends(authorized), Depends(accounts.require('teacher'))])
async def create_scenario(scenario: Scenario, user=Depends(accounts.require('teacher'))):
    if store.scenario(scenario.id):
        raise HTTPException(409, "Сценарий с таким кодом уже существует")
    with store.db:
        store.db.execute('INSERT INTO scenario_owners VALUES (?,?)', (scenario.id, user['id']))
        store.db.execute('INSERT INTO scenarios VALUES (?,?)', (scenario.id, scenario.model_dump_json()))
    return scenario_view(scenario.model_dump(), user)


@app.get("/api/v1/scenarios/{scenario_id}", response_model=ScenarioView, dependencies=[Depends(authorized), Depends(accounts.require('teacher'))])
async def get_scenario(scenario_id: str, user=Depends(accounts.require('teacher'))):
    value = store.scenario(scenario_id)
    if not value or not scenario_visible(scenario_id, user):
        raise HTTPException(404, "Сценарий не найден")
    return scenario_view(value, user)


@app.put("/api/v1/scenarios/{scenario_id}", response_model=ScenarioView, dependencies=[Depends(authorized), Depends(accounts.require('teacher'))])
async def update_scenario(scenario_id: str, scenario: ScenarioUpdate, user=Depends(accounts.require('teacher'))):
    scenario_editable(scenario_id, user)
    if scenario_id != scenario.id:
        raise HTTPException(400, "Код сценария нельзя изменить")
    current = store.scenario(scenario_id)
    if not current:
        raise HTTPException(404, "Сценарий не найден")
    if scenario.version != scenario_version(current):
        raise HTTPException(409, 'Сценарий изменён в другом окне. Ваши поля сохранены в форме: создайте копию или перечитайте актуальную версию.')
    value = store.put_scenario(Scenario.model_validate(scenario.model_dump(exclude={'version'})))
    return scenario_view(value, user)


@app.delete("/api/v1/scenarios/{scenario_id}", status_code=204, dependencies=[Depends(authorized), Depends(accounts.require('teacher'))])
async def delete_scenario(scenario_id: str, user=Depends(accounts.require('teacher'))):
    scenario_editable(scenario_id, user)
    value = store.scenario(scenario_id)
    if not value:
        raise HTTPException(404, "Сценарий не найден")
    enabled = [item for item in store.list_scenarios() if item["enabled"]]
    if value["enabled"] and len(enabled) == 1:
        raise HTTPException(409, "Нельзя удалить единственный включённый сценарий")
    store.delete_scenario(scenario_id)
    with store.db:
        store.db.execute('DELETE FROM scenario_owners WHERE scenario_id=?', (scenario_id,))


@app.websocket("/ws/v1/voice/sessions/{sid}")
async def voice(ws: WebSocket, sid: UUID):
    sid = str(sid)
    if not TOKEN or not hmac.compare_digest(ws.headers.get("authorization", ""), "Bearer " + TOKEN):
        await ws.close(code=1008)
        return
    if sid in active:
        await ws.close(code=1013)
        return
    active.add(sid)
    ws.scope.setdefault('state',{})['audit_actor']={'id':'service:voice','role':'service'}
    try:
        connection_lease=await coordinator.acquire('voice-connection',sid,0)
    except (LockUnavailable,ClusterUnavailable):
        active.discard(sid)
        await ws.close(code=1013)
        return
    try:
        await ws.accept()
    except BaseException:
        await connection_lease.release()
        active.discard(sid)
        raise
    pending = asyncio.Queue(maxsize=32)

    async def process():
        while True:
            event = await pending.get()
            # Preserve every finalized operator turn for the student's transcript.
            # Silently discarding intermediate turns loses facts needed for cards.
            reply = await engine.handle(sid, event)
            if reply:
                await ws.send_json(reply)
            await ws.send_json({'event_id':str(uuid4()),'seq':event.get('seq',0),
                'session_id':sid,'type':'backend.ack','elapsed_ms':event.get('elapsed_ms',0),
                'payload':{'event_id':str(UUID(event['event_id']))}})
            if event.get('type') == 'call.ended':
                return

    async def receive():
        while True:
            event = await ws.receive_json()
            if event.get("session_id") != sid:
                raise ValueError("Session mismatch")
            pending.put_nowait(event)

    tasks = [asyncio.create_task(process()), asyncio.create_task(receive())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, ValueError, KeyError, asyncio.QueueFull, LockUnavailable, ClusterUnavailable):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        active.discard(sid)
        await connection_lease.release()
