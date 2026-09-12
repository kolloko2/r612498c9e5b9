from uuid import UUID, uuid4
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from app.api.calls import authorize
from app.domain.messages import CallerReply
from app.backend.control_ws import ControlWS

router = APIRouter(prefix='/chat', dependencies=[Depends(authorize)])

class Say(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    message_id: UUID

class Mode(BaseModel):
    mode: Literal['manual', 'auto']

def active(request, cid):
    runtime = request.app.state.manager.calls.get(str(cid))
    if not runtime or not runtime.context.ready.is_set() or runtime.context.stop.is_set():
        raise HTTPException(409, 'Нет активного звонка. Позвоните на 201 и примите звонок.')
    return runtime

@router.get('')
async def listing(request: Request):
    return request.app.state.manager.chat.recent()

@router.get('/{cid}')
async def get(cid: UUID, request: Request):
    manager = request.app.state.manager
    chat = manager.chat.get(cid)
    if not chat:
        raise HTTPException(404, 'История не найдена')
    if live := manager.get(cid):
        chat['status'] = live['status']
    return chat

@router.post('/{cid}/say')
async def say(cid: UUID, body: Say, request: Request):
    runtime = active(request, cid)
    if not body.text.strip():
        raise HTTPException(400, 'Введите текст')
    if runtime.mode != 'manual':
        raise HTTPException(409, 'Для ввода текста включите ручной режим')
    await runtime.accept_reply(CallerReply(reply_id=body.message_id, text=body.text), 'operator')
    return {'id': str(body.message_id)}

@router.post('/{cid}/stop')
async def stop(cid: UUID, request: Request):
    runtime = active(request, cid)
    await runtime.stop_playback()
    return {'stopped': True}

@router.post('/{cid}/mode')
async def mode(cid: UUID, body: Mode, request: Request):
    runtime = active(request, cid)
    if runtime.mode == "external":
        raise HTTPException(409, "External call: finish it before changing control mode")
    if runtime.mode == body.mode:
        return {'mode': body.mode}
    settings = runtime.settings if body.mode == 'auto' else runtime.settings.model_copy(update={'backend_mode': 'mock'})
    backend = ControlWS(settings, runtime.context, runtime.on_message, runtime.fail)
    backend.manual = body.mode != 'auto'
    try:
        await backend.start()
    except Exception:
        await backend.close()
        raise HTTPException(503, 'Dialogue service unavailable; current mode retained')
    await runtime.stop_playback()
    previous = runtime.backend
    runtime.mode = body.mode
    runtime.backend = backend
    await previous.close()
    runtime.manager.chat.update(cid, mode=body.mode)
    return {'mode': body.mode}
