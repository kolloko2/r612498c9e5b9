import importlib.util
import os
import json
from pathlib import Path
import httpx
from fastapi.testclient import TestClient


def test_configured_server_origin_stays_explicit(monkeypatch):
    monkeypatch.setenv('ALLOWED_ORIGINS', 'https://training.example')
    monkeypatch.setenv('COOKIE_SECURE', 'true')
    spec = importlib.util.spec_from_file_location('lan_frontend', Path(__file__).with_name('server.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, json={'user': {'id': 'synthetic'}, 'session_token': 'synthetic-token'})
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with TestClient(module.app, base_url='https://training.example') as client:
        assert client.get('/login').status_code == 200
        assert client.get('/login', headers={'Host': 'untrusted.example'}).status_code == 403
        assert client.post('/api/v1/auth/login', json={}, headers={'X-Voice-UI': '1', 'Origin': 'https://evil.example'}).status_code == 403
        result = client.post('/api/v1/auth/login', json={}, headers={'X-Voice-UI': '1', 'Origin': 'https://training.example'})
        assert result.status_code == 200
        assert 'Secure' in result.headers['set-cookie']


def test_local_proxy_origin_and_server_token(monkeypatch):
    monkeypatch.setenv('BACKEND_TOKEN','test-backend-token')
    spec=importlib.util.spec_from_file_location('student_frontend',Path(__file__).with_name('server.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original=httpx.AsyncClient
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.headers['authorization']=='Bearer test-backend-token'
        assert 'cookie' not in request.headers
        assert any(prefix in str(request.url) for prefix in ('/api/v1/student/', '/api/v1/instructor/'))
        return httpx.Response(200,json={'id':'example'},headers={'Set-Cookie':'must-not-cross-users=1'})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with TestClient(module.app,base_url='http://127.0.0.1:3000') as client:
        home=client.get('/')
        assert home.status_code==200
        assert "frame-ancestors 'none'" in home.headers['content-security-policy']
        assert "frame-ancestors 'self'" in client.get('/map').headers['content-security-policy']
        assert 'test-backend-token' not in home.text
        assert client.post('/api/v1/student/sessions',json={}).status_code==403
        assert client.post('/api/v1/student/sessions',json={},headers={'X-Voice-UI':'1','Origin':'https://untrusted.example'}).status_code==403
        assert not requests
        result=client.post('/api/v1/student/sessions',json={},headers={'X-Voice-UI':'1','Origin':'http://127.0.0.1:3000'})
        assert result.status_code==200
        assert len(requests)==1
        assert client.get('/assets/student.js').status_code==200
        assert client.get('/favicon.ico').headers['content-type']=='image/x-icon'
        assert client.get('/assets/favicon.svg').status_code==200
        assert client.get('/.env').status_code==404
        assert client.get('/instructor').status_code==200
        assert client.get('/assets/instructor.js').status_code==200
        assert client.get('/scenarios').status_code==200
        assert client.get('/assets/scenarios.js').status_code==200
        assert client.get('/generation').status_code==200
        assert client.get('/assets/generation.js').status_code==200
        assert client.get('/materials').status_code==200
        assert client.get('/assets/materials.js').status_code==200
        assert client.get('/assessment').status_code==200
        assert client.get('/assets/assessment.js').status_code==200
        assert client.get('/api/v1/student/sessions?limit=200&offset=200').status_code==200
        assert requests[-1].url.params['limit']=='200'
        assert requests[-1].url.params['offset']=='200'
        assert client.get('/api/v1/instructor/statistics?group_id=example-group').status_code==200
        assert requests[-1].url.params['group_id']=='example-group'
        assert client.post('/api/v1/instructor/lessons/example/start', headers={'X-Voice-UI':'1'}).status_code==200
        assert client.put('/api/v1/instructor/scenarios/test/rubric',json={}).status_code==403
        assert client.put('/api/v1/instructor/scenarios/test/rubric',json={},headers={'X-Voice-UI':'1','Origin':'http://127.0.0.1:3000'}).status_code==200
        body={'title':'Synthetic material','body':'Text','file_base64':'dGV4dA==','filename':'example.txt'}
        assert client.post('/api/v1/instructor/materials',json=body,headers={'X-Voice-UI':'1'}).status_code==200
        assert json.loads(requests[-1].content)==body
        before=len(requests)
        assert client.post('/api/v1/instructor/materials',content=iter([b'x'*1024*1024]*37),headers={'X-Voice-UI':'1'}).status_code==413
        assert len(requests)==before


def test_auth_cookie_is_server_only_and_identity_header_cannot_be_spoofed(monkeypatch):
    spec=importlib.util.spec_from_file_location('auth_frontend',Path(__file__).with_name('server.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original=httpx.AsyncClient
    observed=[]
    def handler(request):
        observed.append(request.headers.get('X-User-Session'))
        if request.url.path.endswith('/login'):
            return httpx.Response(200,json={'user':{'id':'synthetic-user','role':'student'},'session_token':'synthetic-test-session'})
        if request.url.path.endswith('/logout'):
            return httpx.Response(204)
        return httpx.Response(200,json=[])
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with TestClient(module.app,base_url='http://127.0.0.1:3000') as client:
        assert client.get('/login').status_code==200
        client.get('/api/v1/student/sessions',headers={'X-User-Session':'forged'})
        assert observed[-1]==''
        response=client.post('/api/v1/auth/login',json={'username':'synthetic','password':'synthetic-password'},headers={'X-Voice-UI':'1'})
        assert response.status_code==200
        assert 'session_token' not in response.json()
        assert 'synthetic-test-session' not in response.text
        assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
        client.get('/api/v1/student/sessions',headers={'X-User-Session':'forged'})
        assert observed[-1]=='synthetic-test-session'
        for path in ('/api/history','/api/calls','/api/calls/example/events','/voice-console'):
            assert client.get(path).status_code==403
        assert client.post('/api/v1/auth/logout',headers={'X-Voice-UI':'1'}).status_code==204
        client.get('/api/v1/student/sessions')
        assert observed[-1]==''
