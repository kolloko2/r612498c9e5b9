"""Настроить MicroSIP на текущий стенд Docker и показать, что делать дальше.

Учебный номер, пароль и порт берутся из приватного `.env.docker`, поэтому
пароль нигде не печатается и не попадает в репозиторий. Прежний файл настроек
сохраняется рядом с пометкой `.bak`.

    python tools/configure_softphone.py            # номер 201
    python tools/configure_softphone.py --extension 202
    python tools/configure_softphone.py --show     # ничего не менять, только показать

Скрипт трогает только `tools/windows-training/MicroSIP/MicroSIP.ini`.
"""

from __future__ import annotations

import argparse
import configparser
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INI = ROOT / "tools" / "windows-training" / "MicroSIP" / "MicroSIP.ini"
ENV = ROOT / ".env.docker"
# Широкая полоса включена и на стороне Asterisk: медиатракт тренажёра работает
# на 16 кГц, и G.722 убирает сужение до 8 кГц перед распознаванием речи.
CODECS = "G722/16000/1 PCMA/8000/1 PCMU/8000/1"


def settings() -> dict[str, str]:
    if not ENV.exists():
        raise SystemExit("Нет .env.docker — сначала выполните python deploy/prepare.py")
    values: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip("'\"")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", default="201", help="Учебный внутренний номер")
    parser.add_argument("--show", action="store_true", help="Только показать параметры")
    args = parser.parse_args()

    values = settings()
    accounts = json.loads(values.get("SIP_ACCOUNTS_JSON", "{}"))
    password = accounts.get(args.extension)
    if not password:
        raise SystemExit(f"Номер {args.extension} не найден в SIP_ACCOUNTS_JSON")

    # TLS-профиль слушает 5061, обычный — 5060. Профиль виден по развёрнутому стенду.
    profile = ROOT / "deploy" / "operations" / "deployment-profile.json"
    tls = json.loads(profile.read_text(encoding="utf-8")).get("tls", False) if profile.exists() else False
    host = values.get("SIP_EXTERNAL_ADDRESS", "127.0.0.1")
    port = "5061" if tls else "5060"
    transport = "tls" if tls else "udp"

    print(f"Сервер:    {host}:{port}")
    print(f"Транспорт: {transport}" + (" (SRTP обязателен)" if tls else ""))
    print(f"Логин:     {args.extension}")
    print(f"Пароль:    {password}")
    print(f"Кодеки:    {CODECS}")
    if args.show:
        return

    if not INI.exists():
        raise SystemExit(f"Не найден {INI}")
    data = INI.read_bytes()
    encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_string(data.decode(encoding))
    config.setdefault("Settings", {})
    config["Settings"]["accountId"] = "1"
    config["Settings"]["enableLocalAccount"] = "0"
    config["Settings"]["audioCodecs"] = CODECS
    config["Settings"]["EC"] = "1"
    config["Settings"]["AA"] = "0"
    # Полная громкость входа обрезала громкую речь и ухудшала распознавание.
    config["Settings"]["volumeInput"] = "90"
    config["Account1"] = {
        "label": f"Тренажёр {args.extension}", "server": f"{host}:{port}", "domain": host,
        "username": args.extension, "authID": args.extension, "password": password,
        "displayName": f"Оператор {args.extension}", "transport": transport,
        "registerRefresh": "60", "keepAlive": "15", "publish": "0", "ICE": "0",
        "allowRewrite": "1", "disableSessionTimer": "0",
    }
    if tls:
        config["Account1"]["SRTP"] = "mandatory"

    backup = INI.with_suffix(".ini.bak")
    if not backup.exists():
        shutil.copy2(INI, backup)
    with INI.open("w", encoding="utf-16") as stream:
        config.write(stream, space_around_delimiters=False)

    print()
    print(f"Настройки записаны, прежние сохранены в {backup.name}.")
    print("Дальше:")
    print(f"  1. Запустите {INI.with_name('MicroSIP.exe')}")
    print("  2. Дождитесь в нижней строке состояния «Online»")
    print("  3. В рабочем месте создайте карточку с каналом «Телефон · Voice / SIP»")
    print("  4. Примите входящий вызов — заявитель заговорит голосом")


if __name__ == "__main__":
    main()
