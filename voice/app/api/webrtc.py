"""Учётные данные телефона в браузере (WebRTC) для учебного номера.

Абонент w<номер> настраивается в Asterisk с паролем, выведенным из пароля ARI
(см. deploy/asterisk/entrypoint.py). Voice выдаёт его только Backend по
служебному токену; Backend передаёт его лишь обучающемуся, которому этот номер
назначен в идущем занятии.
"""
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.calls import authorize

router = APIRouter(prefix="/webrtc", dependencies=[Depends(authorize)])


def web_password(ari_password: str, extension: str) -> str:
    digest = hmac.new(ari_password.encode("utf-8"), f"webrtc:{extension}".encode("ascii"), hashlib.sha256)
    return digest.hexdigest()[:32]


@router.get("/{extension}")
async def credentials(extension: str, request: Request):
    settings = request.app.state.manager.settings
    if extension not in settings.allowed_extensions.split(","):
        raise HTTPException(404, "Extension is not provisioned")
    password = settings.ari_password.get_secret_value()
    if not password:
        raise HTTPException(503, "Asterisk is not configured")
    state = None
    ari = request.app.state.manager.ari
    if ari:
        try:
            state = (await ari.request("GET", f"endpoints/PJSIP/{settings.webrtc_prefix}{extension}")).get("state")
        except Exception:
            state = None
    return {"extension": extension, "username": settings.webrtc_prefix + extension,
            "password": web_password(password, extension), "registered": state == "online"}
