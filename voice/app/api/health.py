import importlib.util
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

# Провайдер речи требует и файлы модели, и установленную библиотеку. Образ Voice
# собирается без них, когда INSTALL_LOCAL_PROVIDERS=false, и тогда смонтированные
# модели ничего не значат: разговор обрывается на первой реплике. Health обязан
# сообщать об этом заранее, а не показывать готовность.
PROVIDER_MODULES = {
    "vosk": "vosk",
    "hybrid": "vosk",
    "sherpa": "sherpa_onnx",
    "silero": "torch",
}


def _library_ready(provider: str) -> bool | None:
    """None — провайдеру библиотека не нужна (mock и внешние службы)."""
    module = PROVIDER_MODULES.get((provider or "").lower())
    if module is None:
        return None
    return importlib.util.find_spec(module) is not None


def _missing(provider: str) -> bool:
    return _library_ready(provider) is False


@router.get("/health")
async def health(request: Request):
    manager = request.app.state.manager
    settings = manager.settings
    ready = not manager.ari or manager.ari.ready.is_set()
    # Гибридный режим дополнительно требует библиотеку точной модели.
    speech_broken = (_missing(settings.stt_provider) or _missing(settings.tts_provider)
                     or (settings.stt_provider == "hybrid" and settings.stt_final_model
                         and _missing("sherpa")))
    stt_model = Path(settings.stt_model) if settings.stt_model else None
    stt_final_model = Path(settings.stt_final_model) if settings.stt_final_model else None
    tts_voice = Path(settings.tts_voice) if settings.tts_voice else None
    # Без файлов модели речь так же недоступна, как без библиотеки.
    speech_broken = speech_broken or (
        (settings.stt_provider in ("vosk", "hybrid") and not (stt_model and stt_model.is_dir()))
        or (settings.stt_provider == "hybrid" and stt_final_model and not stt_final_model.exists())
        or (settings.tts_provider in ("silero", "piper") and not (tts_voice and tts_voice.is_file())))
    tts_fallback_voice = Path(settings.tts_fallback_voice) if settings.tts_fallback_voice else None
    return JSONResponse({
        "status": "ok" if ready and not speech_broken else "degraded",
        "telephony_mode": settings.telephony_mode,
        "pipeline_mode": settings.pipeline_mode,
        "topology_verified": settings.topology_verified,
        "active_calls": len(manager.calls),
        "speech": {
            "stt_provider": settings.stt_provider,
            "stt_library_installed": _library_ready(settings.stt_provider),
            "stt_final_library_installed": (_library_ready("sherpa")
                                            if settings.stt_final_model else None),
            "tts_library_installed": _library_ready(settings.tts_provider),
            "stt_fallback_provider": settings.stt_fallback_provider or None,
            "stt_model_configured": bool(settings.stt_model),
            "stt_model_available": bool(stt_model and stt_model.is_dir()),
            "stt_final_model_configured": bool(settings.stt_final_model),
            "stt_final_model_available": bool(stt_final_model and stt_final_model.exists()),
            "tts_provider": settings.tts_provider,
            "tts_fallback_provider": settings.tts_fallback_provider or None,
            "tts_voice_configured": bool(settings.tts_voice),
            "tts_voice_available": bool(tts_voice and tts_voice.is_file()),
            "tts_fallback_voice_configured": bool(settings.tts_fallback_voice),
            "tts_fallback_voice_available": bool(tts_fallback_voice and tts_fallback_voice.is_file()),
        },
        "backend_control": {
            "mode": settings.backend_mode,
            "delivery": ("in_process_mock" if settings.backend_mode == "mock"
                         else "at_least_once_with_application_ack"),
            "reconnect_budget_s": settings.backend_reconnect_budget_s,
        },
        "security_audit": {
            "enabled": manager.settings.security_audit_dir is not None,
            "failed": request.app.state.security_audit.failed,
            "minimum_retention_days": request.app.state.security_audit.minimum_retention_days,
            "automatic_deletion": False,
        },
    # 503 делает контейнер unhealthy: Docker и кабинет администратора видят,
    # что звонок не состоится, а не только то, что процесс запущен.
    }, status_code=200 if ready and not speech_broken else 503)
