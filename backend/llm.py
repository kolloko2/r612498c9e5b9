"""Server-only dialogue providers. Never expose credentials or upstream errors to UI."""
import asyncio
import json
import logging
import os
import re
import time

import httpx

logger = logging.getLogger('uvicorn.error.llm')

# Профили выбираются администратором под доступное железо. Минимальный сервер
# работает на CPU, видеокарта при наличии ускоряет ответы. Устройство — LLM_DEVICE.
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
# Full authoring is bounded as one operation, including a possible repair.
AUTHORING_TIMEOUT_SECONDS = 900
# A phone turn must not inherit the minutes-long scenario-generation budget.
# Below the Voice provider limit (20 s) with room for speech recognition.
VOICE_REPLY_TIMEOUT_SECONDS = 18.0
DEFAULT_PHONE_MODEL = 'qwen3:4b-instruct-2507-q4_K_M'
# Called before every interactive request: background authoring yields the CPU.
phone_activity_hooks = []
# Called before every phone (call) request: lets AI reviews wait for a quiet moment.
phone_call_hooks = []
PHONE_WARMUP_TIMEOUT_SECONDS = 90
PHONE_WARMUP_ATTEMPTS = 6


def phone_configuration():
    """Short dialogue uses its own local weights; authoring profiles stay intact."""
    config = configuration().copy()
    if config['provider'] == 'ollama':
        phone_model = os.getenv('PHONE_LLM_MODEL') or DEFAULT_PHONE_MODEL
        # Same weights as authoring: identical runner options keep one resident
        # model. Different num_ctx would make Ollama reload the phone runner
        # after every generation and stall the first reply for ~15 seconds.
        config['context'] = config.get('context') or 8192 if shares_runner(config['model'], phone_model) else 4096
        config['model'] = phone_model
    return config


def shares_runner(model, phone_model):
    return model == phone_model or os.getenv('LLM_SHARED_RUNNER', '').lower() in ('1', 'true', 'yes')


DEVICES = {
    'auto': 'Автоматически: видеокарта, если есть, иначе процессор',
    'cpu': 'Только процессор (минимальный сервер по ТЗ)',
    'gpu': 'Видеокарта (все слои модели на GPU)',
}


def device() -> str:
    value = os.getenv('LLM_DEVICE', 'auto').strip().lower()
    return value if value in DEVICES else 'auto'


def local_options(config, max_tokens, *, phone=False):
    options = {'num_ctx': config.get('context') or 8192,
               'num_predict': max_tokens, 'temperature': 0.2, 'presence_penalty': 0}
    selected = device()
    # auto (-1): Ollama сама выгружает слои на видеокарту, а без неё работает на
    # CPU. Значение передаётся явно: иначе модель, загруженная ранее в режиме
    # cpu (keep_alive=-1), так и осталась бы на процессоре.
    options['num_gpu'] = {'cpu': 0, 'gpu': 999}.get(selected, -1)
    if selected != 'gpu' and (phone or shares_runner(config.get('model'), os.getenv('PHONE_LLM_MODEL') or DEFAULT_PHONE_MODEL)):
        # num_thread is a runner option: authoring must use the phone value or
        # Ollama restarts the shared runner. More threads were slower on the VM.
        options['num_thread'] = max(1, min(32, int(os.getenv('PHONE_LLM_THREADS', '6'))))
        # A cancelled request stops only between batches. With the default 1024
        # tokens a cancelled review kept the CPU for ~35 s and starved the call that
        # cancelled it; 128 reads equally fast here (~27 tokens/s) and stops in ~5 s.
        options['num_batch'] = max(32, min(1024, int(os.getenv('LLM_NUM_BATCH', '128'))))
    return options


async def warm_phone_model():
    """Use the same host, weights and context as real calls, not container loopback."""
    config = phone_configuration()
    if config['provider'] != 'ollama':
        return
    async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
        # All replicas share Ollama. Never unload another replica's active model
        # during startup. Explicit options let Ollama select the proper runner.
        response = await client.post(os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434') + '/api/chat',
            json={'model': config['model'], 'messages': [], 'stream': False,
                  'keep_alive': -1, 'options': local_options(config, 120, phone=True)})
        response.raise_for_status()


async def warm_phone_model_with_retry():
    """Bound startup races with Ollama; cancellation must remain immediate."""
    try:
        async with asyncio.timeout(PHONE_WARMUP_TIMEOUT_SECONDS):
            for attempt in range(PHONE_WARMUP_ATTEMPTS):
                try:
                    await warm_phone_model()
                    logger.info('Phone model preload ready attempt=%d', attempt + 1)
                    return True
                except httpx.HTTPError as error:
                    retryable = not isinstance(error, httpx.HTTPStatusError) or (
                        error.response.status_code == 429 or error.response.status_code >= 500)
                    logger.warning('Phone model preload failed attempt=%d reason=%s',
                                   attempt + 1, type(error).__name__)
                    if not retryable or attempt + 1 == PHONE_WARMUP_ATTEMPTS:
                        return False
                    await asyncio.sleep(min(2 ** attempt, 15))
    except TimeoutError:
        logger.warning('Phone model preload deadline exceeded')
    return False


def profile_name():
    name = os.getenv("LLM_PROFILE", "").strip().lower()
    return name if name in PROFILES else ""


def configuration():
    """Профиль, если он задан администратором; иначе прежние переменные окружения."""
    selected = profile_name()
    if selected:
        profile = PROFILES[selected]
        provider = "mock" if selected == "mock" else "ollama"
        # The administrator may point a profile at already installed weights,
        # e.g. the phone model, so one resident runner serves both tasks.
        model = (os.getenv("LLM_MODEL") or profile["model"]) if provider == "ollama" else profile["model"]
        return {"provider": provider, "model": model, "profile": selected,
                "profile_title": profile["title"], "context": profile["context"],
                "configured": True}
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini") if provider == "openrouter" else os.getenv("DIALOGUE_MODEL", "qwen3:1.7b")
    return {"provider": provider, "model": model if provider != "mock" else "scenario-mock",
            "profile": "", "profile_title": "", "context": 0,
            "configured": provider in ("mock", "ollama") or (provider == "openrouter" and bool(os.getenv("OPENROUTER_API_KEY")))}


def _mark_activity():
    for hook in phone_activity_hooks:
        try:
            hook()
        except Exception:
            logger.warning('Phone activity hook failed')


async def _keep_marking_activity():
    # A review on a CPU server lasts minutes: keep background authoring paused
    # for the whole request, not only for the quiet period after its start.
    while True:
        await asyncio.sleep(20)
        _mark_activity()


async def complete(messages, *, max_tokens=220, json_mode=False, phone=False, timeout=None):
    """`json_mode` принуждает провайдера выдавать валидный JSON.

    Значение `True` требует просто корректный JSON, а словарь — конкретную JSON
    Schema. Схема важнее, чем кажется: без неё модель охотно возвращает валидный
    JSON, который просто повторяет присланные ей данные вместо ответа.

    Рассуждающие модели вроде qwen3 иначе начинают ответ с размышлений, и он не
    разбирается, даже когда режим рассуждений выключен параметром. Ограничение
    формата на стороне провайдера надёжнее любых указаний в промпте.
    """
    authoring = isinstance(json_mode, dict) and 'scenario' in json_mode.get('properties', {}) and not phone
    if not isinstance(max_tokens, int) or not 1 <= max_tokens <= (5000 if authoring else 2000):
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
    budget = (timeout or (AUTHORING_TIMEOUT_SECONDS if authoring else LOCAL_TIMEOUT_SECONDS)) if config["provider"] == "ollama" else 35
    heartbeat = None
    if phone:
        for hook in phone_call_hooks:
            try:
                hook()
            except Exception:
                logger.warning('Phone call hook failed')
    if not authoring:
        # Calls, live grading and reviews wait for a student: background
        # authoring yields the CPU to every interactive request, not only calls.
        _mark_activity()
        if phone_activity_hooks:
            heartbeat = asyncio.ensure_future(_keep_marking_activity())
    try:
        return await _request(config, messages, max_tokens, json_mode, phone, authoring, budget)
    finally:
        if heartbeat:
            heartbeat.cancel()


async def _request(config, messages, max_tokens, json_mode, phone, authoring, budget):
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
                      "keep_alive": -1 if phone or shares_runner(config["model"], os.getenv('PHONE_LLM_MODEL') or DEFAULT_PHONE_MODEL)
                      else os.getenv("OLLAMA_KEEP_ALIVE", "30m"),
                      **({"format": json_mode if isinstance(json_mode, dict) else "json"} if json_mode else {}),
                      "options": {**local_options(config, max_tokens, phone=phone),
                                  # A spoken reply is one line: stop instead of writing more.
                                  **({'stop': ['\n']} if phone and not json_mode and max_tokens > 1 else {})}})
            response.raise_for_status()
            text = response.json()["message"]["content"]
            if phone:
                data = response.json()
                logger.info('phone_llm model=%s elapsed_ms=%d prompt_tokens=%s output_tokens=%s reason=%s device=%s',
                            config['model'], int((time.monotonic() - started) * 1000),
                            data.get('prompt_eval_count'), data.get('eval_count'), data.get('done_reason'), device())
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty model reply")
    if max_tokens > 220:
        if len(text) > (40000 if authoring else 20000):
            raise ValueError("Model reply exceeds review limit")
        return text.strip()
    return text.strip()[:1200]

# Plain text, not a JSON schema: on the CPU server the schema grammar made the
# small model mix alphabets («Пёtra») and spent tokens on the wrapper.
REPLY_INSTRUCTION = ('Ответь одной короткой репликой по-русски, как в телефонном разговоре: '
                     'не больше 15 слов, без кавычек, имён и пояснений.')
REPLY_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}},
                "required": ["reply"]}
QUOTES = '«»"\'“”„ '
MIXED_SCRIPT = re.compile(r'[А-Яа-яЁё][A-Za-z]|[A-Za-z][А-Яа-яЁё]')
SPEAKER_PREFIX = re.compile(r'^(?:[А-ЯЁA-Z][А-Яа-яЁёA-Za-z .\-]{0,40}|Ответ|Реплика):\s*')


def clean_reply(raw) -> str:
    """First spoken line of the model output; empty when it is unusable."""
    text = str(raw or '').strip()
    if text.startswith('{'):
        try:
            text = str(json.loads(text).get('reply', ''))
        except (ValueError, AttributeError):
            return ''
    text = next((line.strip() for line in text.splitlines() if line.strip()), '')
    text = SPEAKER_PREFIX.sub('', text).strip().strip(QUOTES)
    # A word glued from Cyrillic and Latin letters is a broken token, not speech.
    if not text or MIXED_SCRIPT.search(text):
        return ''
    return text[:400]


async def reply(messages, *, max_tokens=220):
    """One short phone utterance. The caller supplies a grounded fallback.

    An unusable output returns an empty reply for the caller's grounded fallback.
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
    return clean_reply(await complete(instructed, max_tokens=min(max_tokens, 120), phone=True))
