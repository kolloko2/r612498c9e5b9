import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.audio.formats import FRAME_BYTES
from app.domain.messages import CreateCall


bearer = HTTPBearer(auto_error=False)


async def authorize(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    token = request.app.state.manager.settings.api_token.get_secret_value()
    if token and not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
        raise HTTPException(401, "Invalid bearer token")


router = APIRouter(dependencies=[Depends(authorize)])


@router.post("/calls", status_code=201)
async def create_call(body: CreateCall, request: Request):
    try:
        return await request.app.state.manager.create(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(429, str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/calls/{call_id}")
async def get_call(call_id: UUID, request: Request):
    snapshot = request.app.state.manager.get(call_id)
    if not snapshot:
        raise HTTPException(404, "Call not found")
    return snapshot


@router.post("/calls/{call_id}/hangup")
async def hangup_call(call_id: UUID, request: Request):
    snapshot = await request.app.state.manager.hangup(call_id)
    if not snapshot:
        raise HTTPException(404, "Call not found")
    return snapshot


@router.post("/calls/{call_id}/mock/audio")
async def inject_mock_audio(call_id: UUID, request: Request):
    manager = request.app.state.manager
    if manager.settings.telephony_mode != "mock":
        raise HTTPException(404, "Mock input disabled")
    runtime = manager.calls.get(str(call_id))
    if not runtime or not runtime.context.ready.is_set():
        raise HTTPException(409, "Call must be active")
    audio = bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio) > 320000:
            raise HTTPException(413, "Maximum 10 seconds of PCM per request")
    if not audio or len(audio) % FRAME_BYTES:
        raise HTTPException(400, "Expected raw slin16 PCM in multiples of 640 bytes")
    count = len(audio) // FRAME_BYTES
    if runtime.mock_input.qsize() + count > runtime.mock_input.maxsize:
        raise HTTPException(429, "Mock microphone queue is full")
    for offset in range(0, len(audio), FRAME_BYTES):
        runtime.mock_input.put_nowait(bytes(audio[offset:offset + FRAME_BYTES]))
    return {"accepted_frames": count}
