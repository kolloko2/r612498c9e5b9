"""Group voice lifecycle with synthetic Voice HTTP, no telephone or model calls."""
import httpx
from uuid import uuid4
from test_rbac_integration import classroom
from test_lesson_lifecycle import create_lesson


def test_transport_recovery_preserves_card_and_does_not_repeat_normal_hangup(classroom,monkeypatch):
    c,h=classroom['client'],classroom['headers'];uid=classroom['users']['student1']['id']
    original=httpx.AsyncClient;old,new=str(uuid4()),str(uuid4());created=[]
    def respond(request):
        if request.method=='POST' and request.url.path.endswith('/calls'):
            identifier=old if not created else new;created.append(identifier)
            return httpx.Response(200,json={'call_id':identifier,'status':'calling'})
        identifier=request.url.path.split('/')[-1]
        return httpx.Response(200,json={'call_id':identifier,'status':'failed',
            'reason':'media_disconnected' if identifier==old else 'remote_hangup'})
    monkeypatch.setenv('VOICE_API_TOKEN','synthetic-token')
    monkeypatch.setattr('workspace.httpx.AsyncClient',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    lesson=create_lesson(classroom,transport='sip',sip_extensions={uid:'202'}).json()
    c.post('/api/v1/instructor/lessons/'+lesson['id']+'/start',headers=h['teacher1'])
    card=c.post('/api/v1/student/lessons/'+lesson['id']+'/next',headers=h['student1']).json()
    path='/api/v1/student/sessions/'+card['id']
    c.post(path+'/call',headers=h['student1'])
    assert c.get(path+'/call',headers=h['student1']).json()['recovery_allowed'] is True
    recovered=c.post(path+'/call/recover?expected_call_id='+old,headers=h['student1'])
    assert recovered.status_code==200 and recovered.json()['call_id']==new
    assert recovered.json()['card']==card['card'] and recovered.json()['revision']==card['revision']
    assert c.post(path+'/call/recover?expected_call_id='+old,headers=h['student1']).json()['call_id']==new
    assert len(created)==2
    assert c.post(path+'/call/recover?expected_call_id='+new,headers=h['student1']).status_code==409
    assert c.post(path+'/call/recover?expected_call_id='+new,headers=h['student2']).status_code==404


def test_sip_assignment_validation_and_live_privacy(classroom):
    c, h = classroom['client'], classroom['headers']
    uid = classroom['users']['student1']['id']
    assert create_lesson(classroom, transport='sip', sip_extensions={uid:'bad'}).status_code == 422
    # Учебный номер допустим и в текстовом занятии: доклад дежурному службы
    # идёт отдельным исходящим звонком, даже когда карточка пришла данными.
    assert create_lesson(classroom, sip_extensions={uid:'201'}).status_code == 201
    assert create_lesson(classroom, transport='sip', sip_extensions={'foreign':'201'}).status_code == 422
    lesson = create_lesson(classroom, transport='sip').json()
    url = '/api/v1/instructor/lessons/' + lesson['id']
    assert c.post(url+'/start', headers=h['teacher1']).status_code == 409
    assert c.get(url+'/live', headers=h['teacher2']).status_code == 404
    assert c.get(url+'/live', headers=h['student1']).status_code == 403
    live = c.get(url+'/live', headers=h['teacher1']).json()
    assert live['participants'][0]['active_card'] is None


def test_sip_next_duplicate_call_stop_failure_and_retry(classroom, monkeypatch):
    c, h = classroom['client'], classroom['headers']
    uid = classroom['users']['student1']['id']
    original_client = httpx.AsyncClient
    calls, fail = [], {'hangup': False}
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/hangup') and fail['hangup']:
            return httpx.Response(503)
        return httpx.Response(200, json={'call_id':'test-call', 'status':'ended' if request.url.path.endswith('/hangup') else 'active'})
    monkeypatch.setenv('VOICE_API_TOKEN', 'synthetic-test-token')
    monkeypatch.setattr('workspace.httpx.AsyncClient', lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw))
    lesson = create_lesson(classroom, transport='sip', sip_extensions={uid:'202'}, cards_per_student=2).json()
    teacher = '/api/v1/instructor/lessons/' + lesson['id']
    student = '/api/v1/student/lessons/' + lesson['id'] + '/next'
    assert c.post(teacher+'/start', headers=h['teacher1']).status_code == 200
    first = c.post(student, headers=h['student1']).json()
    assert first['transport'] == 'sip' and first['sip_extension'] == '202'
    assert first['messages'] == []
    assert c.post(student, headers=h['student1']).json()['id'] == first['id']
    session = '/api/v1/student/sessions/' + first['id']
    assert c.post(session+'/call', headers=h['student1']).status_code == 200
    assert b'202' in calls[0].content
    assert c.post(session+'/call', headers=h['student1']).status_code == 200
    assert len(calls) == 1
    live = c.get(teacher+'/live', headers=h['teacher1']).json()['participants'][0]
    assert live['active_card']['call_id'] == 'test-call'
    assert live['active_card']['last_event']['type'] == 'call.requested'
    assert live['latest_result'] is None
    assert c.post(session+'/finish', headers=h['student1']).status_code == 200
    second = c.post(student, headers=h['student1'], json={'after_session_id':first['id']}).json()
    assert second['id'] != first['id'] and second['sip_extension'] == '202'
    assert c.post('/api/v1/student/sessions/'+second['id']+'/call', headers=h['student1']).status_code == 200
    fail['hangup'] = True
    assert c.post(teacher+'/finish', headers=h['teacher1'], json={'reason':'Стоп'}).status_code == 503
    assert c.post(student, headers=h['student1']).status_code == 409
    fail['hangup'] = False
    assert c.post(teacher+'/finish', headers=h['teacher1'], json={'reason':'Стоп'}).status_code == 200
    live = c.get(teacher+'/live', headers=h['teacher1']).json()
    assert live['state'] == 'finished'
    assert live['participants'][0]['completed'] == 2
    assert live['participants'][0]['active_card'] is None


def test_mixed_ready_card_never_starts_sip(classroom, monkeypatch):
    c, h = classroom['client'], classroom['headers']
    uid = classroom['users']['student1']['id']
    lesson = create_lesson(classroom, mode='mixed', transport='sip', sip_extensions={uid:'201'},
                           prefilled_scenario_ids=[classroom['scenario_id']]).json()
    monkeypatch.setattr('workspace.random.choice', lambda choices: choices[-1])
    c.post('/api/v1/instructor/lessons/'+lesson['id']+'/start', headers=h['teacher1'])
    card = c.post('/api/v1/student/lessons/'+lesson['id']+'/next', headers=h['student1']).json()
    assert card['exercise_mode'] == 'actions' and card['transport'] == 'text'
    assert c.post('/api/v1/student/sessions/'+card['id']+'/call', headers=h['student1']).status_code == 409
