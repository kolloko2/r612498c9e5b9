import asyncio
import json
import os
from pathlib import Path
from uuid import UUID
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse

app = FastAPI(title='Голосовой чат')
BASE = os.getenv('VOICE_URL', 'http://127.0.0.1:8001')
TOKEN = os.environ['API_TOKEN']
DIALOGUE = os.getenv('DIALOGUE_URL', 'http://127.0.0.1:8000')
DIALOGUE_TOKEN = os.environ.get('BACKEND_TOKEN', '')

@app.middleware('http')
async def local_only(request, call_next):
    if request.headers.get('host', '').split(':')[0] not in ('127.0.0.1', 'localhost'):
        return Response('Local access only', status_code=403)
    if request.method != 'GET' and (request.headers.get('origin') not in (None, 'http://127.0.0.1:8002', 'http://localhost:8002')
                                   or request.headers.get('x-voice-ui') != '1'):
        return Response('Invalid origin', status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
    return response

async def gateway(path, method='GET', body=None):
    async with httpx.AsyncClient(timeout=65, trust_env=False) as client:
        r = await client.request(method, BASE + '/api/v1/' + path,
                                 headers={'Authorization': 'Bearer ' + TOKEN}, json=body)
        if r.status_code >= 400:
            try:
                detail = r.json().get('detail', 'Ошибка сервера звонков')
            except ValueError:
                detail = 'Ошибка сервера звонков'
            raise HTTPException(r.status_code, detail)
        return r.json()

async def dialogue(path, method='GET', body=None):
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            r = await client.request(method, DIALOGUE + '/api/v1/' + path,
                                     headers={'Authorization': 'Bearer ' + DIALOGUE_TOKEN}, json=body)
    except httpx.HTTPError:
        raise HTTPException(503, 'Сервис сценариев недоступен')
    if r.status_code >= 400:
        try:
            detail = r.json().get('detail', 'Ошибка сервиса сценариев')
        except ValueError:
            detail = 'Ошибка сервиса сценариев'
        raise HTTPException(r.status_code, detail)
    return None if r.status_code == 204 else r.json()

@app.get('/')
async def index():
    return FileResponse(Path(__file__).with_name('index.html'))

@app.get('/api/health')
async def health():
    try:
        return await gateway('health')
    except httpx.HTTPError:
        raise HTTPException(503, 'Сервер звонков недоступен. Запустите Start-Training.cmd.')

@app.get('/api/history')
async def history():
    return await gateway('chat')

@app.post('/api/calls')
async def create(request: Request):
    body = await request.json()
    return await gateway('calls', 'POST', {'session_id': str(UUID(body['session_id'])), 'extension': '201',
                                         'mode': body.get('mode', 'manual'),
                                         'scenario_id': body.get('scenario_id')})

@app.api_route('/api/scenarios', methods=['GET', 'POST'])
async def scenarios(request: Request):
    body = await request.json() if request.method == 'POST' else None
    return await dialogue('scenarios', request.method, body)

@app.post('/api/scenarios/validate')
async def validate_scenario(request: Request):
    return await dialogue('scenarios/validate', 'POST', await request.json())

@app.api_route('/api/scenarios/{scenario_id}', methods=['GET', 'PUT', 'DELETE'])
async def scenario(scenario_id: str, request: Request):
    body = await request.json() if request.method == 'PUT' else None
    result = await dialogue('scenarios/' + scenario_id, request.method, body)
    return Response(status_code=204) if result is None else result

@app.post('/api/calls/{cid}/{action}')
async def action(cid: UUID, action: str, request: Request):
    if action not in ('hangup', 'say', 'stop', 'mode'):
        raise HTTPException(404)
    body = await request.json()
    return await gateway(f'calls/{cid}/hangup' if action == 'hangup' else f'chat/{cid}/{action}', 'POST', body)

@app.get('/api/calls/{cid}/events')
async def events(cid: UUID, request: Request):
    async def stream():
        previous = None
        while not await request.is_disconnected():
            try:
                data = json.dumps(await gateway(f'chat/{cid}'), ensure_ascii=False)
                if data != previous:
                    yield 'data: ' + data + '\n\n'
                    previous = data
                else:
                    yield ': heartbeat\n\n'
            except (httpx.HTTPError, HTTPException):
                yield 'event: unavailable\ndata: {}\n\n'
            await asyncio.sleep(0.35)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

@app.get('/api/calls/{cid}/export/{fmt}')
async def export(cid: UUID, fmt: str):
    chat = await gateway(f'chat/{cid}')
    if fmt == 'json':
        content = json.dumps(chat, ensure_ascii=False, indent=2)
    elif fmt == 'txt':
        roles = {'me': 'Оператор (я)', 'operator': 'Пострадавший (ввод текста)', 'bot': 'Пострадавший (ИИ)'}
        content = f"Звонок: {cid}\nСессия: {chat['session_id']}\n\n" + '\n'.join(
            f"{m['time']} {roles[m['role']]}: {m['text']} [{m['status']}]" for m in chat['messages'])
    else:
        raise HTTPException(404)
    return Response(content.encode('utf-8-sig'), media_type='application/octet-stream',
                    headers={'Content-Disposition': f'attachment; filename="dialogue-{cid}.{fmt}"'})
