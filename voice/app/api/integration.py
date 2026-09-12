"""Authenticated server-to-server API. Human=operator, AI=victim."""
import asyncio
import json
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.api.calls import authorize, create_call, hangup_call
from app.api.chat import active
from app.domain.messages import CreateCall, CallerReply

router = APIRouter(prefix='/integration', tags=['External AI'], dependencies=[Depends(authorize)])

class NewCall(BaseModel):
    session_id: UUID
    extension: str = '201'

class Speak(BaseModel):
    message_id: UUID
    text: str = Field(min_length=1, max_length=4000)
    interrupt: bool = False

@router.post('/calls', status_code=201)
async def start(body: NewCall, request: Request):
    existing = request.app.state.manager.sessions.get(str(body.session_id))
    if existing and request.app.state.manager.calls[existing].mode != 'external':
        raise HTTPException(409, 'Session already controlled by another mode')
    return await create_call(CreateCall(session_id=body.session_id, extension=body.extension, mode='external'), request)

@router.get('/calls/{cid}')
async def snapshot(cid: UUID, request: Request):
    manager = request.app.state.manager
    chat = manager.chat.get(cid)
    if chat is None:
        raise HTTPException(404, 'Call not found')
    if live := manager.get(cid):
        chat['status'] = live['status']
    chat['messages'] = [{**m, 'role': 'operator' if m['role'] == 'me' else 'victim'} for m in chat['messages']]
    return chat

@router.post('/calls/{cid}/speak', status_code=202)
async def speak(cid: UUID, body: Speak, request: Request):
    rt = active(request, cid)
    if rt.mode != 'external':
        raise HTTPException(409, 'Call must be in external mode')
    if not body.text.strip():
        raise HTTPException(422, 'Text cannot be blank')
    async with rt.command_lock:
        if rt.context.stop.is_set():
            raise HTTPException(409, "Call has ended")
        previous = next((m for m in rt.manager.chat.get(cid)['messages'] if m['id'] == str(body.message_id)), None)
        if previous:
            if previous['text'] != body.text or previous['role'] == 'me':
                raise HTTPException(409, 'message_id already used with different content')
            return {'message_id': str(body.message_id), 'status': previous['status'], 'duplicate': True}
        if body.interrupt:
            await rt.stop_playback()
        if rt.context.stop.is_set():
            raise HTTPException(409, "Call has ended")
        try:
            await rt.accept_reply(CallerReply(reply_id=body.message_id, text=body.text), 'bot')
        except ValueError:
            raise HTTPException(429, 'Playback queue is full')
        return {'message_id': str(body.message_id), 'status': 'queued', 'duplicate': False}

@router.post('/calls/{cid}/stop')
async def stop(cid: UUID, request: Request):
    rt = active(request, cid)
    if rt.mode != 'external':
        raise HTTPException(409, 'Call must be in external mode')
    async with rt.command_lock:
        await rt.stop_playback()
    return {'stopped': True}

@router.post('/calls/{cid}/hangup')
async def hangup(cid: UUID, request: Request):
    return await hangup_call(cid, request)

@router.get('/calls/{cid}/events')
async def events(cid: UUID, request: Request, after: int = Query(0, ge=0), follow: bool = True):
    store = request.app.state.manager.chat
    if not store.get(cid):
        raise HTTPException(404, 'Call not found')
    try:
        cursor = max(after, int(request.headers.get('last-event-id', '0')))
    except ValueError:
        raise HTTPException(400, 'Invalid Last-Event-ID')
    if not follow:
        return {'events': store.events(cid, cursor)}
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            batch = store.events(cid, cursor)
            for event in batch:
                cursor = int(event['event_id'])
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if not batch:
                yield ': heartbeat\n\n'
                await asyncio.sleep(.25)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store'})
