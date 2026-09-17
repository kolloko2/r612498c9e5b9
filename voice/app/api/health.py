from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    manager = request.app.state.manager
    settings = manager.settings
    ready = not manager.ari or manager.ari.ready.is_set()
    stt_model = Path(settings.stt_model) if settings.stt_model else None
    stt_final_model = Path(settings.stt_final_model) if settings.stt_final_model else None
    tts_voice = Path(settings.tts_voice) if settings.tts_voice else None
    tts_fallback_voice = Path(settings.tts_fallback_voice) if settings.tts_fallback_voice else None
    return JSONResponse({
        "status": "ok" if ready else "degraded",
        "telephony_mode": settings.telephony_mode,
        "pipeline_mode": settings.pipeline_mode,
        "topology_verified": settings.topology_verified,
        "active_calls": len(manager.calls),
        "speech": {
            "stt_provider": settings.stt_provider,
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
    }, status_code=200 if ready else 503)
