"""Одноразовые коды TOTP (RFC 6238) для второго фактора входа.

Работает с любым приложением-аутентификатором (Google Authenticator,
Яндекс Ключ, Microsoft Authenticator, FreeOTP, KeePassXC): 6 цифр, шаг 30 с,
HMAC-SHA1. Сеть для проверки не нужна: код считается из общего секрета и
времени сервера, поэтому второй фактор работает в закрытом контуре.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
STEP_SECONDS = 30
# Допуск на расхождение часов телефона и сервера: предыдущий и следующий шаг.
WINDOW = 1
ISSUER = "Тренажёр 112"
RECOVERY_CODES = 10


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(secret: str, code: str, last_step: int | None = None, now: float | None = None) -> int | None:
    """Шаг времени, которому соответствует код, или None.

    Код, уже использованный на этом или более раннем шаге, не принимается
    повторно: подсмотренный код нельзя ввести второй раз.
    """
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def otpauth_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


def qr_svg(uri: str) -> str | None:
    """QR-код для сканирования приложением; без библиотеки остаётся ручной ввод ключа."""
    try:
        import segno
    except ImportError:
        return None
    return segno.make(uri, error="m").svg_inline(scale=5, border=2, dark="#111", light="#fff", omitsize=True)


def recovery_codes() -> list[str]:
    # Без похожих символов (0/O, 1/I), чтобы код было легко переписать с бумаги.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
            for _ in range(RECOVERY_CODES)]


def hash_recovery(code: str) -> str:
    normalized = "".join(ch for ch in code.upper() if ch.isalnum())
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()
