"""Local training dialogue and scenario service; never contacts emergency services."""
import asyncio
from difflib import SequenceMatcher
import hmac
import json
import os
import re
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, field_validator

MODEL = os.getenv("DIALOGUE_MODEL", "qwen3:1.7b")
TOKEN = os.environ.get("DIALOGUE_TOKEN", "")
DB = os.getenv("DIALOGUE_DB", "dialogue.sqlite3")
SEED = json.loads(Path(__file__).with_name("scenario.json").read_text(encoding="utf-8"))
app = FastAPI(title="Учебный диалог 112")


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    title: str = Field(min_length=3, max_length=120)
    description: str = Field("", max_length=1000)
    victim_name: str = Field(min_length=1, max_length=80)
    incident: str = Field(min_length=3, max_length=1000)
    location: str = Field(min_length=3, max_length=500)
    known_facts: list[str] = Field(min_length=1, max_length=30)
    unknown_facts: list[str] = Field(default_factory=list, max_length=20)
    emotion: str = Field(min_length=2, max_length=200)
    behavior: str = Field("", max_length=1000)
    opening: str = Field(min_length=3, max_length=1000)
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
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS scenarios (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        seed = normalize_seed(SEED)
        self.db.execute("INSERT OR IGNORE INTO scenarios VALUES (?, ?)", (seed.id, seed.model_dump_json()))
        self.db.commit()

    def load(self, sid):
        row = self.db.execute("SELECT body FROM sessions WHERE id=?", (sid,)).fetchone()
        return json.loads(row[0]) if row else {"step": 0, "seq": 0, "messages": [], "replies": {}, "ended": False}

    def save(self, sid, state):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (sid, json.dumps(state, ensure_ascii=False)))

    def list_scenarios(self):
        rows = self.db.execute("SELECT body FROM scenarios ORDER BY lower(json_extract(body, '$.title'))").fetchall()
        return [json.loads(row[0]) for row in rows]

    def scenario(self, scenario_id):
        row = self.db.execute("SELECT body FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def first_enabled(self):
        return next((item for item in self.list_scenarios() if item["enabled"]), None)

    def put_scenario(self, scenario):
        body = scenario.model_dump()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO scenarios VALUES (?, ?)", (scenario.id, json.dumps(body, ensure_ascii=False)))
        return body

    def delete_scenario(self, scenario_id):
        with self.db:
            return self.db.execute("DELETE FROM scenarios WHERE id=?", (scenario_id,)).rowcount


async def generate(messages):
    payload = {"model": MODEL, "messages": messages, "stream": False, "think": False,
               "keep_alive": -1, "options": {"num_ctx": 4096, "num_predict": 160,
                                               "num_thread": 8, "num_batch": 256, "temperature": 0.2}}
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.post("http://127.0.0.1:11434/api/chat", json=payload)
        response.raise_for_status()
        text = response.json()["message"]["content"].strip()
        if not text:
            raise ValueError("Empty model reply")
        return text[:1200]


class Engine:
    def __init__(self, store, model=generate):
        self.store, self.model = store, model

    async def handle(self, sid, event):
        if event.get("session_id") != sid:
            raise ValueError("Session mismatch")
        eid = str(UUID(event["event_id"]))
        state = self.store.load(sid)
        if eid in state["replies"]:
            return state["replies"][eid]
        kind = event["type"]
        if kind == "call.ended":
            state["ended"] = True
            self.store.save(sid, state)
            return None
        if event.get("payload", {}).get("mode") != "auto":
            return None
        if state["ended"] or kind not in ("call.connected", "operator.utterance"):
            return None
        if kind == "call.connected":
            if state["messages"]:
                return None
            requested = event.get("payload", {}).get("scenario_id")
            scenario = self.store.scenario(requested) if requested else self.store.first_enabled()
            if not scenario or not scenario["enabled"]:
                raise ValueError("Scenario unavailable")
            state["scenario"] = scenario
            text = scenario["opening"]
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
engine = Engine(store)
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
    warmup_task = asyncio.create_task(warm_model())


def authorized(authorization: str = Header("")):
    if not TOKEN or not hmac.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401, "Неверный токен")


@app.get("/api/v1/health")
async def health():
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        try:
            response = await client.get("http://127.0.0.1:11434/api/tags")
            response.raise_for_status()
            ready = any(model["name"] == MODEL for model in response.json()["models"])
        except (httpx.HTTPError, KeyError):
            ready = False
    return {"status": "ok" if ready else "model_unavailable", "scenarios": len(store.list_scenarios()), "model": MODEL}


@app.get("/api/v1/scenarios", dependencies=[Depends(authorized)])
async def list_scenarios():
    return store.list_scenarios()


@app.post("/api/v1/scenarios/validate", dependencies=[Depends(authorized)])
async def validate_scenario(scenario: Scenario):
    return {"valid": True, "rules": rules_for(scenario.model_dump())}


@app.post("/api/v1/scenarios", status_code=201, dependencies=[Depends(authorized)])
async def create_scenario(scenario: Scenario):
    if store.scenario(scenario.id):
        raise HTTPException(409, "Сценарий с таким кодом уже существует")
    return store.put_scenario(scenario)


@app.get("/api/v1/scenarios/{scenario_id}", dependencies=[Depends(authorized)])
async def get_scenario(scenario_id: str):
    value = store.scenario(scenario_id)
    if not value:
        raise HTTPException(404, "Сценарий не найден")
    return value


@app.put("/api/v1/scenarios/{scenario_id}", dependencies=[Depends(authorized)])
async def update_scenario(scenario_id: str, scenario: Scenario):
    if scenario_id != scenario.id:
        raise HTTPException(400, "Код сценария нельзя изменить")
    if not store.scenario(scenario_id):
        raise HTTPException(404, "Сценарий не найден")
    return store.put_scenario(scenario)


@app.delete("/api/v1/scenarios/{scenario_id}", status_code=204, dependencies=[Depends(authorized)])
async def delete_scenario(scenario_id: str):
    value = store.scenario(scenario_id)
    if not value:
        raise HTTPException(404, "Сценарий не найден")
    enabled = [item for item in store.list_scenarios() if item["enabled"]]
    if value["enabled"] and len(enabled) == 1:
        raise HTTPException(409, "Нельзя удалить единственный включённый сценарий")
    store.delete_scenario(scenario_id)


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
    await ws.accept()
    pending = asyncio.Queue(maxsize=32)

    async def process():
        while True:
            event = await pending.get()
            # If speech was split into several quick fragments while the model
            # was busy, answer only the newest one instead of building a queue.
            if event.get("type") == "operator.utterance":
                await asyncio.sleep(0.12)
                while not pending.empty():
                    newer = pending.get_nowait()
                    if newer.get("type") == "operator.utterance":
                        event = newer
            reply = await engine.handle(sid, event)
            if reply:
                await ws.send_json(reply)

    async def receive():
        while True:
            event = await ws.receive_json()
            if event.get("session_id") != sid:
                raise ValueError("Session mismatch")
            if event.get("type") == "call.ended":
                await engine.handle(sid, event)
                return
            pending.put_nowait(event)

    tasks = [asyncio.create_task(process()), asyncio.create_task(receive())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, ValueError, KeyError, asyncio.QueueFull):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        active.discard(sid)
