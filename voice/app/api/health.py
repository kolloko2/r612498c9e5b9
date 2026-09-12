from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    manager = request.app.state.manager
    ready = not manager.ari or manager.ari.ready.is_set()
    return JSONResponse({"status": "ok" if ready else "degraded",
                         "telephony_mode": manager.settings.telephony_mode,
                         "pipeline_mode": manager.settings.pipeline_mode,
                         "active_calls": len(manager.calls)}, status_code=200 if ready else 503)
