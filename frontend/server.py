import asyncio
import json
import os
import re
import ssl
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

BASE = os.getenv('VOICE_URL', 'http://127.0.0.1:8001')
TOKEN = os.environ.get('API_TOKEN', '')
DIALOGUE = os.getenv('DIALOGUE_URL', 'http://127.0.0.1:8000')
DIALOGUE_TOKEN = os.environ.get('BACKEND_TOKEN', '')
USER_SESSION = ContextVar('user_session', default='')
ALLOWED_ORIGINS = {value.strip() for value in os.getenv('ALLOWED_ORIGINS',
    'http://127.0.0.1:8002,http://localhost:8002,http://127.0.0.1:3000,http://localhost:3000').split(',') if value.strip()}
ALLOWED_HOSTS = {'127.0.0.1', 'localhost'} | {urlsplit(value).hostname for value in ALLOWED_ORIGINS}
if any(urlsplit(value).scheme not in ('http', 'https') or not urlsplit(value).hostname
       or urlsplit(value).path or urlsplit(value).query or urlsplit(value).fragment
       or urlsplit(value).username for value in ALLOWED_ORIGINS):
    raise ValueError('ALLOWED_ORIGINS must contain exact http(s) origins without paths')
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
MAP_DATA_DIR = Path(os.getenv('MAP_DATA_DIR', str(Path(__file__).resolve().parents[1] / 'deploy/maps')))


def internal_http_verify(url):
    """Extend normal OS trust only for HTTPS internal-service URLs."""
    if urlsplit(url).scheme.lower() != 'https':
        return True
    ca_file = os.getenv('INTERNAL_CA_FILE', '').strip()
    if not ca_file:
        return True
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=ca_file)
    return context


async def strip_upstream_cookie(request: httpx.Request):
    # Browser identity is forwarded only in X-User-Session. A pooled upstream
    # client must never carry a Set-Cookie value from one browser user to another.
    request.headers.pop('cookie', None)


@asynccontextmanager
async def lifespan(application: FastAPI):
    hooks = {'request': [strip_upstream_cookie]}
    async with httpx.AsyncClient(timeout=65, trust_env=False, event_hooks=hooks,
                                 verify=internal_http_verify(BASE)) as voice_client, \
            httpx.AsyncClient(timeout=80, trust_env=False, event_hooks=hooks,
                              verify=internal_http_verify(DIALOGUE)) as dialogue_client:
        application.state.voice_client = voice_client
        application.state.dialogue_client = dialogue_client
        yield


app = FastAPI(title='Голосовой чат', lifespan=lifespan)

@app.middleware('http')
async def local_only(request, call_next):
    if request.url.hostname not in ALLOWED_HOSTS:
        return Response('Local access only', status_code=403)
    if request.method not in ('GET', 'HEAD') and (request.headers.get('origin') not in ALLOWED_ORIGINS | {None}
                                   or request.headers.get('x-voice-ui') != '1'):
        return Response('Invalid origin', status_code=403)
    # Legacy global call/history endpoints have no user ownership. Do not expose
    # them through the authenticated classroom BFF. Voice itself remains intact.
    if request.url.path == '/voice-console' or request.url.path in ('/api/history', '/api/calls', '/api/health') or request.url.path.startswith('/api/calls/'):
        return Response('Legacy console is isolated from the user workspace', status_code=403)
    context = USER_SESSION.set(request.cookies.get('training_session', ''))
    try:
        if request.method in ('POST', 'PUT') and request.url.path.startswith('/api/v1/instructor/materials'):
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 36 * 1024 * 1024:
                    return JSONResponse({'detail': 'Запрос превышает 36 МиБ'}, status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        response = await call_next(request)
    finally:
        USER_SESSION.reset(context)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    ancestors = "'self'" if request.url.path == '/map' else "'none'"
    # media-src blob: — прослушивание записей звонков, полученных как файл в разборе попытки.
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; frame-ancestors " + ancestors
    if request.url.path == '/map':
        response.headers['Content-Security-Policy'] += "; worker-src 'self'; img-src 'self' data: blob:"
    return response

async def gateway(path, method='GET', body=None):
    r = await app.state.voice_client.request(method, BASE + '/api/v1/' + path,
                                             headers={'Authorization': 'Bearer ' + TOKEN}, json=body)
    if r.status_code >= 400:
        try:
            detail = r.json().get('detail', 'Ошибка сервера звонков')
        except ValueError:
            detail = 'Ошибка сервера звонков'
        raise HTTPException(r.status_code, detail)
    return r.json()

async def dialogue(path, method='GET', body=None, params=None):
    try:
        r = await app.state.dialogue_client.request(method, DIALOGUE + '/api/v1/' + path,
                                                    headers={'Authorization': 'Bearer ' + DIALOGUE_TOKEN,
                                                             'X-User-Session': USER_SESSION.get()},
                                                    json=body, params=params, timeout=240 if path.startswith('instructor/materials') else 65)
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
    return FileResponse(Path(__file__).with_name('student.html'))

@app.get('/login')
async def login_page():
    return FileResponse(Path(__file__).with_name('login.html'))

@app.get('/portal')
async def portal_page():
    return FileResponse(Path(__file__).with_name('portal.html'))

@app.get('/operations')
async def operations_page():
    return FileResponse(Path(__file__).with_name('operations.html'))

@app.get('/audit')
async def audit_page():
    return FileResponse(Path(__file__).with_name('audit.html'))

# Телефон в браузере: SIP поверх WebSocket идёт через этот сервер к Asterisk.
# Браузер видит только свой адрес (тот же сертификат и cookie), Asterisk наружу
# не открывается. Пускаем только обучающегося, которому назначен учебный номер,
# и только с запросами от его собственного абонента.
SIP_WS_URL = os.getenv('SIP_WS_URL', 'wss://asterisk:8089/ws')
SIP_FROM = re.compile(r'^(?:From|f)\s*:.*?sips?:([^@;>\s]+)@', re.I | re.M)


def sip_request_user(message):
    # Ответы (SIP/2.0 …) и пустые keep-alive (CRLF) пропускаются; в запросах From
    # обязан быть своим абонентом.
    if not message.strip() or message.lstrip().startswith('SIP/2.0'):
        return None
    match = SIP_FROM.search(message)
    return match.group(1) if match else ''


@app.websocket('/sip-ws')
async def sip_websocket(ws: WebSocket):
    import websockets
    origin, host = ws.headers.get('origin'), ws.url.hostname
    if host not in ALLOWED_HOSTS or origin not in ALLOWED_ORIGINS | {f'https://{ws.headers.get("host", "")}'}:
        await ws.close(code=4403)
        return
    token = USER_SESSION.set(ws.cookies.get('training_session', ''))
    try:
        phone = await dialogue('student/softphone')
    except HTTPException:
        phone = {'enabled': False}
    finally:
        USER_SESSION.reset(token)
    if not phone.get('enabled'):
        await ws.close(code=4403)
        return
    await ws.accept(subprotocol='sip' if 'sip' in ws.scope.get('subprotocols', []) else None)
    verify = internal_http_verify(SIP_WS_URL.replace('wss://', 'https://', 1))
    try:
        async with websockets.connect(SIP_WS_URL, subprotocols=['sip'], open_timeout=10, max_size=2 ** 16,
                                      ssl=verify if SIP_WS_URL.startswith('wss://') else None) as upstream:
            async def browser_to_asterisk():
                while True:
                    message = await ws.receive_text()
                    user = sip_request_user(message)
                    if user is not None and user != phone['username']:
                        raise PermissionError('foreign SIP identity')
                    await upstream.send(message)

            async def asterisk_to_browser():
                async for message in upstream:
                    await ws.send_text(message if isinstance(message, str) else message.decode('utf-8', 'replace'))

            tasks = [asyncio.create_task(browser_to_asterisk()), asyncio.create_task(asterisk_to_browser())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    except (OSError, PermissionError, WebSocketDisconnect, websockets.WebSocketException):
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass


@app.get('/review')
async def review_page():
    return FileResponse(Path(__file__).with_name('review.html'))

@app.get('/dds')
async def dds_page():
    return FileResponse(Path(__file__).with_name('dds.html'))

@app.get('/api/v1/auth/{action}')
async def auth_get(action: str):
    if action not in ('status', 'me', 'directory-status', 'mfa'):
        raise HTTPException(404)
    return await dialogue('auth/'+action)

@app.post('/api/v1/auth/mfa/{action}')
async def auth_mfa(action: str, request: Request):
    # Настройка второго фактора своей учётной записи: ключ, включение, отключение.
    if action not in ('setup', 'enable', 'disable'):
        raise HTTPException(404)
    body = await request.json() if action != 'setup' else None
    return await dialogue('auth/mfa/'+action, 'POST', body)

@app.post('/api/v1/auth/{action}')
async def auth_post(action: str, request: Request):
    if action not in ('bootstrap', 'login', 'logout', 'directory-login', 'mfa-login'):
        raise HTTPException(404)
    body = await request.json() if action != 'logout' else None
    result = await dialogue('auth/'+action, 'POST', body)
    if action == 'logout':
        response = Response(status_code=204)
        response.delete_cookie('training_session', path='/')
    elif result.get('mfa_required'):
        # Пароль принят, но сессии ещё нет: браузер получает только одноразовый билет на ввод кода.
        response = Response(json.dumps({'mfa_required': True, 'mfa_token': result['mfa_token'], 'expires_in': result.get('expires_in')}, ensure_ascii=False), media_type='application/json')
    else:
        response = Response(json.dumps({'user': result['user']}, ensure_ascii=False),media_type='application/json',status_code=201 if action=='bootstrap' else 200)
        response.set_cookie('training_session', result['session_token'], httponly=True, secure=COOKIE_SECURE, samesite='strict', max_age=28800, path='/')
    return response

@app.api_route('/api/v1/admin/{path:path}', methods=['GET', 'POST', 'PATCH', 'PUT'])
async def admin_proxy(path: str, request: Request):
    if '..' in path or '%' in path:
        raise HTTPException(400)
    body = await request.json() if request.method != 'GET' else None
    return await dialogue('admin/'+path, request.method, body, params=dict(request.query_params))

@app.get('/voice-console')
async def voice_console():
    return FileResponse(Path(__file__).with_name('index.html'))

@app.get('/instructor')
async def instructor_page():
    return FileResponse(Path(__file__).with_name('instructor.html'))

@app.get('/tickets')
async def tickets_page():
    return FileResponse(Path(__file__).with_name('tickets.html'))

@app.get('/scenarios')
async def scenarios_page():
    return FileResponse(Path(__file__).with_name('scenarios.html'))

@app.get('/generation')
async def generation_page():
    return FileResponse(Path(__file__).with_name('generation.html'))

@app.get('/materials')
async def materials_page():
    return FileResponse(Path(__file__).with_name('materials.html'))

@app.get('/assessment')
async def assessment_page():
    return FileResponse(Path(__file__).with_name('assessment.html'))

@app.get('/map')
async def incident_map():
    return FileResponse(Path(__file__).with_name('map.html'))

@app.api_route('/map-data/region.pmtiles', methods=['GET', 'HEAD'])
async def map_archive():
    # Public cartography only. Never expose the address DB or arbitrary paths.
    archive = MAP_DATA_DIR / 'region.pmtiles'
    if not archive.is_file():
        raise HTTPException(503, 'Локальная подложка карты не установлена')
    return FileResponse(archive, media_type='application/octet-stream')

@app.api_route('/api/v1/instructor/{path:path}', methods=['GET', 'PUT', 'POST', 'PATCH', 'DELETE'])
async def instructor_proxy(path: str, request: Request):
    if '..' in path or '%' in path:
        raise HTTPException(400)
    body = await request.json() if request.method in ('PUT','POST','PATCH') and await request.body() else None
    return await dialogue('instructor/' + path, request.method, body, params=request.query_params)

app.mount('/assets', StaticFiles(directory=Path(__file__).with_name('assets')), name='assets')

@app.get('/favicon.ico', include_in_schema=False)
async def favicon():
    return FileResponse(Path(__file__).with_name('assets') / 'favicon.ico', media_type='image/x-icon')

@app.api_route('/api/v1/student/{path:path}', methods=['GET', 'POST', 'PUT'])
async def student_proxy(path: str, request: Request):
    # This namespace is exclusively backed by Backend; the browser never calls Voice.
    if '..' in path or '%' in path:
        raise HTTPException(400)
    body = await request.json() if request.method != 'GET' and request.headers.get('content-length', '0') != '0' else None
    return await dialogue('student/' + path, request.method, body, params=request.query_params)

@app.get('/api/v1/health')
async def student_health():
    return await dialogue('health')

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

@app.post('/api/scenarios/practice-draft')
async def practice_draft(request: Request):
    return await dialogue('scenarios/practice-draft', 'POST', await request.json())

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
