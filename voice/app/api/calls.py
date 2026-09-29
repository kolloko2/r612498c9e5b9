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


@router.get("/calls/{call_id}/recording")
async def get_recording(call_id: UUID, request: Request, format: str = "wav"):
    """Общая запись звонка (оператор и собеседник) для разбора попытки.

    Файл ищется по идентификатору звонка в каталоге записей, поэтому доступен
    и после перезапуска модуля. Ответ — JSON с base64: так его без изменений
    передают Backend и BFF, которые проксируют только JSON.
    """
    import asyncio
    import base64
    import wave
    from app.audio import mp3
    if format not in ("wav", "mp3"):
        raise HTTPException(422, "format must be wav or mp3")
    root = request.app.state.manager.settings.recording_dir
    matches = sorted(root.glob(f"*/{call_id}/mixed.wav"))
    if not matches:
        raise HTTPException(404, "Recording not found")
    path = matches[-1]
    if path.stat().st_size > 30 * 1024 * 1024:
        raise HTTPException(413, "Recording is too large")
    with wave.open(str(path), "rb") as audio:
        seconds = round(audio.getnframes() / audio.getframerate(), 1)
    if format == "mp3":
        # Записи до появления MP3 кодируются при первом запросе и сохраняются рядом.
        target = path.with_suffix(".mp3")
        if not target.exists():
            if not mp3.available():
                raise HTTPException(503, "MP3 encoder is not installed")
            await asyncio.to_thread(mp3.encode, path)
        path = target
    return {"call_id": str(call_id), "format": format, "content_type": "audio/mpeg" if format == "mp3" else "audio/wav",
            "duration_seconds": seconds, "size_bytes": path.stat().st_size,
            "file_base64": base64.b64encode(path.read_bytes()).decode("ascii")}


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
