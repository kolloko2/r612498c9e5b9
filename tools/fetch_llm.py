"""Проверить Ollama и подтянуть модель профиля, выбранного в .env.docker.

Ollama устанавливается на хост, а не в контейнер: модель обслуживается одним
процессом и переживает пересборку стенда, а веса не попадают в образ. Контейнер
обращается к ней по `OLLAMA_URL` (по умолчанию `http://host.docker.internal:11434`).

Запуск:

    python tools/fetch_llm.py            # профиль из .env.docker
    python tools/fetch_llm.py --profile accelerated
    python tools/fetch_llm.py --check    # только проверить, ничего не качать

Скрипт ничего не устанавливает сам: если Ollama нет, он скажет, откуда её взять.
Стенд работает и без модели — занятие, карточки, нормативы и доклад дежурному
идут на детерминированном ядре.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from llm import PROFILES  # noqa: E402  (путь к backend задаётся выше)

DEFAULT_URL = "http://127.0.0.1:11434"


def configured() -> tuple[str, str]:
    """Профиль и адрес Ollama из .env.docker, если он есть."""
    profile, url = "standard", DEFAULT_URL
    env = ROOT / ".env.docker"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, _, raw = line.partition("=")
            value = raw.strip().strip("'\"")
            if key.strip() == "LLM_PROFILE" and value:
                profile = value
            elif key.strip() == "OLLAMA_URL" and value:
                url = value
    # Внутри контейнера адрес хоста называется иначе, чем с самого хоста.
    return profile, url.replace("host.docker.internal", "127.0.0.1")


def available(url: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return [item["name"] for item in payload.get("models", [])]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


def pull(model: str) -> bool:
    print(f"  загрузка модели {model} — это единоразово и занимает несколько минут")
    try:
        result = subprocess.run(["ollama", "pull", model], check=False)
    except FileNotFoundError:
        print("  команда ollama не найдена в PATH")
        return False
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), help="Профиль модели")
    parser.add_argument("--check", action="store_true", help="Только проверить")
    args = parser.parse_args()

    profile, url = configured()
    profile = args.profile or profile
    spec = PROFILES.get(profile)
    if spec is None:
        raise SystemExit(f"Неизвестный профиль: {profile}")

    print(f"Профиль: {profile} — {spec['title']}")
    if profile == "mock":
        print("Профиль «Без модели»: генерация сценариев недоступна, занятие идёт полностью.")
        return

    model = spec["model"]
    print(f"Модель: {model}")
    print(f"Ollama: {url}")

    models = available(url)
    if models is None:
        print()
        print("Ollama недоступна по этому адресу.")
        print("  Установить: https://ollama.com/download")
        print("  После установки она поднимается сама и слушает порт 11434.")
        print("  Стенд без неё работает: поставьте LLM_PROVIDER='mock' в .env.docker,")
        print("  и занятие пойдёт на детерминированных ответах.")
        sys.exit(1)

    # Ollama называет модели с тегом; 'qwen3:4b' и 'qwen3:4b-instruct' — разные.
    if any(name == model or name.startswith(model + "-") for name in models):
        print("Модель уже загружена.")
        return
    print("Модель ещё не загружена. Есть:", ", ".join(models) or "(пусто)")
    if args.check:
        sys.exit(1)
    if not pull(model):
        print("Не удалось загрузить модель.")
        sys.exit(1)
    print("Готово. Проверьте /api/v1/health — там появится выбранная модель.")


if __name__ == "__main__":
    main()
