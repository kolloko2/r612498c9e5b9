"""Server-only dialogue providers. Never expose credentials or upstream errors to UI."""
import json
import logging
import os
import time

import httpx

logger = logging.getLogger('uvicorn.error.llm')

# Профили выбираются администратором под доступное железо. Требования к серверу в
# ТЗ заданы диапазоном и без видеокарты; профили авторинга и телефонная модель
# используют CPU, независимо от наличия видеокарты.
# Модель разворачивается локально через Ollama: внешние сервисы контуру запрещены.
PROFILES = {
    "mock": {"model": "scenario-mock", "context": 0, "title": "Без модели",
             "hint": "Детерминированные ответы. Занятия идут, генерация сценариев недоступна."},
    "fast": {"model": "qwen3:4b", "context": 8192, "title": "Быстрая (слабый сервер)",
             "hint": "Модель авторинга с меньшим объёмом памяти; телефонная модель настраивается отдельно."},
    "standard": {"model": "qwen3:8b", "context": 8192, "title": "Точная (рекомендуется)",
                 "hint": "Локальный авторинг на процессоре; телефон использует отдельную модель. Замеры — docs/LOCAL_MODEL.md."},
}
DEFAULT_PROFILE = "mock"
LOCAL_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "300"))
# A phone turn must not inherit the minutes-long scenario-generation budget.
VOICE_REPLY_TIMEOUT_SECONDS = 15.0
DEFAULT_PHONE_MODEL = 'qwen3:4b-instruct-2507-q4_K_M'


def phone_configuration():
    """Short dialogue uses its own local weights; authoring profiles stay intact."""
    config = configuration().copy()
    if config['provider'] == 'ollama':
        config['model'] = os.getenv('PHONE_LLM_MODEL') or DEFAULT_PHONE_MODEL
        config['context'] = 4096
    return config


def local_options(config, max_tokens, *, phone=False):
    options = {'num_ctx': config.get('context') or 8192,
               'num_predict': max_tokens, 'temperature': 0.2, 'presence_penalty': 0,
               # CPU is the deployment target, including on a GPU-equipped PC.
               'num_gpu': 0}
    if phone:
        options['num_thread'] = max(1, min(32, int(os.getenv('PHONE_LLM_THREADS', '6'))))
    return options


async def warm_phone_model():
    """Use the same host, weights and context as real calls, not container loopback."""
    config = phone_configuration()
    if config['provider'] != 'ollama':
        return
    async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
        response = await client.post(os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434') + '/api/chat',
            json={'model': config['model'], 'messages': [], 'stream': False,
                  'keep_alive': -1, 'options': local_options(config, 120, phone=True)})
        response.raise_for_status()


def profile_name():
    name = os.getenv("LLM_PROFILE", "").strip().lower()
    return name if name in PROFILES else ""


def configuration():
    """Профиль, если он задан администратором; иначе прежние переменные окружения."""
    selected = profile_name()
    if selected:
        profile = PROFILES[selected]
        provider = "mock" if selected == "mock" else "ollama"
        return {"provider": provider, "model": profile["model"], "profile": selected,
                "profile_title": profile["title"], "context": profile["context"],
                "configured": True}
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini") if provider == "openrouter" else os.getenv("DIALOGUE_MODEL", "qwen3:1.7b")
    return {"provider": provider, "model": model if provider != "mock" else "scenario-mock",
            "profile": "", "profile_title": "", "context": 0,
            "configured": provider in ("mock", "ollama") or (provider == "openrouter" and bool(os.getenv("OPENROUTER_API_KEY")))}


async def complete(messages, *, max_tokens=220, json_mode=False, phone=False):
    """`json_mode` принуждает провайдера выдавать валидный JSON.

    Значение `True` требует просто корректный JSON, а словарь — конкретную JSON
    Schema. Схема важнее, чем кажется: без неё модель охотно возвращает валидный
    JSON, который просто повторяет присланные ей данные вместо ответа.

    Рассуждающие модели вроде qwen3 иначе начинают ответ с размышлений, и он не
    разбирается, даже когда режим рассуждений выключен параметром. Ограничение
    формата на стороне провайдера надёжнее любых указаний в промпте.
    """
    if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 2000:
        raise ValueError("Invalid completion budget")
    config = phone_configuration() if phone else configuration()
    if config["provider"] == "mock":
        if isinstance(json_mode, dict) and 'reply' in json_mode.get('properties', {}):
            return json.dumps({'reply': 'Я не знаю. Спросите, пожалуйста, по-другому.'}, ensure_ascii=False)
        return "Я не знаю. Спросите, пожалуйста, по-другому."
    if not config["configured"]:
        raise ValueError("LLM provider is not configured")
    # Локальная модель на процессоре выдаёт единицы токенов в секунду, поэтому
    # генерация сценария законно занимает минуты. Облачный провайдер отвечает
    # быстро, и для него прежний короткий лимит остаётся.
    budget = LOCAL_TIMEOUT_SECONDS if config["provider"] == "ollama" else 35
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=httpx.Timeout(budget, connect=8), trust_env=False) as client:
        if config["provider"] == "openrouter":
            response = await client.post("https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "X-Title": "112 Training Simulator"},
                json={"model": config["model"], "messages": messages, "temperature": 0.2,
                      "max_tokens": max_tokens, "stream": False,
                      **({"response_format": {"type": "json_object"}} if json_mode else {})})
            response.raise_for_status()
            try:
                text = response.json()["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError):
                raise ValueError("Invalid provider response") from None
        else:
            response = await client.post(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434") + "/api/chat",
                # keep_alive держит модель в памяти между редкими запросами:
                # иначе каждый из них платит десяток секунд за загрузку весов.
                json={"model": config["model"], "messages": messages, "stream": False, "think": False,
                      "keep_alive": -1 if phone else os.getenv("OLLAMA_KEEP_ALIVE", "30m"),
                      **({"format": json_mode if isinstance(json_mode, dict) else "json"} if json_mode else {}),
                      "options": local_options(config, max_tokens, phone=phone)})
            response.raise_for_status()
            text = response.json()["message"]["content"]
            if phone:
                data = response.json()
                logger.info('phone_llm model=%s elapsed_ms=%d prompt_tokens=%s output_tokens=%s reason=%s cpu_only=true',
                            config['model'], int((time.monotonic() - started) * 1000),
                            data.get('prompt_eval_count'), data.get('eval_count'), data.get('done_reason'))
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty model reply")
    if max_tokens > 220:
        if len(text) > 20000:
            raise ValueError("Model reply exceeds review limit")
        return text.strip()
    return text.strip()[:1200]

REPLY_INSTRUCTION = 'Return only JSON {"reply":"your spoken reply"}. Speak Russian, at most 25 words. No reasoning or explanations.'
REPLY_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}},
                "required": ["reply"]}


async def reply(messages, *, max_tokens=220):
    """One short phone utterance. Schema bounds structure, not factual accuracy.

    Invalid/truncated JSON returns an empty reply for the caller's grounded fallback.
    All system messages survive, including a final role-repair instruction.
    """
    system = [message for message in messages if message.get("role") == "system"]
    rest = [message for message in messages if message.get("role") != "system"]
    joined = '\n'.join([*(message['content'] for message in system), REPLY_INSTRUCTION])
    instructed = [{"role": "system", "content": joined}, *rest]
    # Refuse oversized context rather than letting the runner silently discard
    # the initial scenario, address or constraints. Normal turns are much smaller.
    if sum(len(message['content']) for message in instructed) > 9000:
        raise ValueError('Phone context exceeds safe budget')
    raw = await complete(instructed, max_tokens=min(max_tokens, 120), json_mode=REPLY_SCHEMA, phone=True)
    try:
        value = json.loads(raw).get("reply", "")
    except (ValueError, AttributeError):
        return ""
    return value.strip() if isinstance(value, str) else ""
