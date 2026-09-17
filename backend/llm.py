"""Server-only dialogue providers. Never expose credentials or upstream errors to UI."""
import json
import os

import httpx

# Профили выбираются администратором под доступное железо. Требования к серверу в
# ТЗ заданы диапазоном и без видеокарты, поэтому по умолчанию система рассчитана
# на CPU, а ускоренный профиль включается, только когда видеокарта есть.
# Модель разворачивается локально через Ollama: внешние сервисы контуру запрещены.
PROFILES = {
    "mock": {"model": "scenario-mock", "context": 0, "title": "Без модели",
             "hint": "Детерминированные ответы. Занятия идут, генерация сценариев недоступна."},
    "standard": {"model": "qwen3:4b", "context": 8192, "title": "Стандартная (CPU)",
                 "hint": "Рекомендуемый профиль без видеокарты. Генерация сценария — секунды."},
    "accelerated": {"model": "qwen3:8b", "context": 8192, "title": "Ускоренная (видеокарта)",
                    "hint": "Требует видеокарту. Та же работа в десятки раз быстрее."},
}
DEFAULT_PROFILE = "mock"
LOCAL_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "300"))


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


async def complete(messages, *, max_tokens=220, json_mode=False):
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
    config = configuration()
    if config["provider"] == "mock":
        return "Я не знаю. Спросите, пожалуйста, по-другому."
    if not config["configured"]:
        raise ValueError("LLM provider is not configured")
    # Локальная модель на процессоре выдаёт единицы токенов в секунду, поэтому
    # генерация сценария законно занимает минуты. Облачный провайдер отвечает
    # быстро, и для него прежний короткий лимит остаётся.
    budget = LOCAL_TIMEOUT_SECONDS if config["provider"] == "ollama" else 35
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
                      "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "30m"),
                      **({"format": json_mode if isinstance(json_mode, dict) else "json"} if json_mode else {}),
                      "options": {"num_ctx": config.get("context") or (8192 if max_tokens > 220 else 4096),
                                  "num_predict": max_tokens if max_tokens > 220 else 160, "temperature": 0.2}})
            response.raise_for_status()
            text = response.json()["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty model reply")
    if max_tokens > 220:
        if len(text) > 20000:
            raise ValueError("Model reply exceeds review limit")
        return text.strip()
    return text.strip()[:1200]

REPLY_INSTRUCTION = 'Верни только JSON {"reply":"твоя реплика"} без пояснений.'
REPLY_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}},
                "required": ["reply"]}


async def reply(messages, *, max_tokens=220):
    """Одна произносимая реплика от рассуждающей модели.

    Модели семейства qwen3 начинают ответ с размышлений, и это не отключается ни
    параметром `think`, ни указанием `/no_think`: в вывод попадает «Хорошо, мне
    нужно...». Просьба вернуть JSON с единственным полем принуждается на стороне
    провайдера грамматикой, поэтому рассуждения физически не помещаются в ответ.

    Пустой или неразобранный ответ — не исключение вызывающего кода: он получает
    пустую строку и подставляет свой детерминированный запасной вариант.
    """
    system = [message for message in messages if message.get("role") == "system"]
    rest = [message for message in messages if message.get("role") != "system"]
    joined = (system[0]["content"] + chr(10) + REPLY_INSTRUCTION) if system else REPLY_INSTRUCTION
    instructed = [{"role": "system", "content": joined}, *rest]
    raw = await complete(instructed, max_tokens=max_tokens, json_mode=REPLY_SCHEMA)
    try:
        value = json.loads(raw).get("reply", "")
    except (ValueError, AttributeError):
        return ""
    return value.strip() if isinstance(value, str) else ""
