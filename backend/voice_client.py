"""Единственный серверный клиент голосового модуля.

Клиент жил внутри роутера рабочего места, поэтому доклад дежурному не мог
создать звонок, не потянув за собой весь модуль. Здесь он вынесен целиком без
изменения поведения: тот же токен из окружения, тот же таймаут, та же проверка
TLS и то же сообщение об ошибке.

Токен и адрес читаются из окружения при каждом вызове: администратор меняет их
через техническое обслуживание, а не перезапуском процесса.
"""

from __future__ import annotations

import os

import httpx
from fastapi import HTTPException

from transport_tls import httpx_verify

TIMEOUT_SECONDS = 20


async def request(path: str, method: str = "GET", body=None):
    token = os.getenv("VOICE_API_TOKEN", "")
    if not token:
        raise HTTPException(503, "Голосовой модуль не настроен: отсутствует серверный токен")
    voice_url = os.getenv("VOICE_URL", "http://127.0.0.1:8001")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, trust_env=False,
                                     verify=httpx_verify(voice_url)) as client:
            response = await client.request(method, voice_url + "/api/v1/" + path,
                                            headers={"Authorization": "Bearer " + token}, json=body)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        raise HTTPException(503, "Голосовой модуль недоступен. Проверьте Voice и назначенный учебный SIP-номер.")
