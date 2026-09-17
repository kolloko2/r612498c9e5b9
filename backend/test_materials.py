import base64
import io
from zipfile import ZipFile

import pytest
from accounts import Accounts
from learning import Learning
from materials import router, install_upload_limit, attachment, MaterialWrite
from fastapi import HTTPException
from test_rbac_integration import classroom

BASE = '/api/v1/instructor/materials'
STUDENT = '/api/v1/student/materials'


@pytest.fixture
def library(classroom):
    c = classroom
    accounts = Accounts(c['store'])
    c['client'].app.include_router(router(c['store'], accounts, Learning(c['store'], accounts), lambda: None))
    return c


def payload(c, **changes):
    return {'title': 'Учебная памятка', 'body': 'Синтетический справочный текст',
            'group_ids': [c['group']['id']], **changes}


def test_publish_group_scope_and_revoke(library):
    c = library; client = c['client']; h = c['headers']
    response = client.post(BASE, headers=h['teacher1'], json=payload(c))
    assert response.status_code == 201, response.text
    doc = response.json(); url = BASE+'/'+doc['id']; student_url = STUDENT+'/'+doc['id']
    assert client.get(STUDENT, headers=h['student1']).json() == []
    assert client.get(student_url, headers=h['student1']).status_code == 404
    assert client.get(url, headers=h['teacher2']).status_code == 404
    assert client.post(BASE, headers=h['student1'], json=payload(c)).status_code == 403
    published = client.put(url, headers=h['teacher1'], json=payload(c, published=True, revision=1))
    assert published.status_code == 200
    assert len(client.get(STUDENT, headers=h['student1']).json()) == 1
    value = client.get(student_url, headers=h['student1']).json()
    assert value['body'] == 'Синтетический справочный текст'
    assert 'group_ids' not in value and 'teacher_id' not in value
    assert client.get(student_url, headers=h['student2']).status_code == 404
    assert client.get(STUDENT, headers=h['student2']).json() == []
    assert client.put(url, headers=h['teacher1'], json=payload(c, revision=1)).status_code == 409
    assert client.get(student_url, headers=h['student1']).status_code == 200
    assert client.put(url, headers=h['teacher1'], json=payload(c, revision=2)).status_code == 200
    assert client.get(student_url, headers=h['student1']).status_code == 404


def test_attachment_roundtrip_preserve_replace_remove(library):
    c = library; client = c['client']; h = c['headers']['teacher1']
    encoded = base64.b64encode('Учебный текст <script>alert(1)</script>'.encode()).decode()
    created = client.post(BASE, headers=h, json=payload(c, filename='памятка.txt', file_base64=encoded, published=True)).json()
    url = BASE+'/'+created['id']
    assert created['file_base64'] == encoded
    assert 'file_base64' not in client.get(BASE, headers=h).json()[0]
    assert client.get(STUDENT+'/'+created['id'], headers=c['headers']['student1']).json()['file_base64'] == encoded
    saved = client.put(url, headers=h, json=payload(c, revision=1, title='Исправленная памятка')).json()
    assert saved['file_base64'] == encoded and saved['filename'] == 'памятка.txt'
    assert client.put(url, headers=h, json=payload(c, revision=2, filename='../bad.txt', file_base64=encoded)).status_code == 422
    assert client.get(url, headers=h).json()['revision'] == 2
    removed = client.put(url, headers=h, json=payload(c, revision=2, remove_attachment=True)).json()
    assert removed['file_base64'] == '' and removed['filename'] == '' and removed['file_size'] == 0


def test_validation_and_foreign_groups(library):
    c = library; client = c['client']; h = c['headers']
    assert client.post(BASE, headers=h['teacher1'], json=payload(c, published=True, group_ids=[])).status_code == 422
    assert client.post(BASE, headers=h['teacher2'], json=payload(c)).status_code == 404
    assert client.post(BASE, headers=h['teacher1'], json=payload(c, body='')).status_code == 422
    assert client.post(BASE, headers=h['teacher1'], json=payload(c, difficulty='expert')).status_code == 422
    catalog = client.get('/api/v1/student/curriculum', headers=h['student1']).json()
    assert len(catalog['difficulties']) == 3 and len(catalog['profiles']) == 6
    assert client.get('/api/v1/instructor/curriculum', headers=h['student1']).status_code == 403
    for filename, raw in [('bad.pdf', b'not pdf'), ('bad.txt', b'\xff'), ('bad.exe', b'MZ'), ('bad.docx', b'PK')]:
        assert client.post(BASE, headers=h['teacher1'], json=payload(c, filename=filename, file_base64=base64.b64encode(raw).decode())).status_code == 422


def test_docx_signature_size_and_macro_rejection():
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<document/>')
    value = MaterialWrite(title='Учебный документ', filename='test.docx', file_base64=base64.b64encode(stream.getvalue()).decode())
    assert attachment(value) == stream.getvalue()
    with ZipFile(stream, 'a') as archive:
        archive.writestr('word/vbaProject.bin', b'ignored')
    value.file_base64 = base64.b64encode(stream.getvalue()).decode()
    with pytest.raises(HTTPException):
        attachment(value)
    value.filename = 'test.pdf'
    value.file_base64 = base64.b64encode(b'%PDF-'+b'x'*(5*1024*1024)).decode()
    with pytest.raises(HTTPException):
        attachment(value)


def test_request_limit_preserves_small_body_and_blocks_chunked():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    app = FastAPI()
    install_upload_limit(app)

    @app.post(BASE)
    async def echo(request: Request):
        return await request.json()

    with TestClient(app) as client:
        assert client.post(BASE, json={'body': 'text'}).json() == {'body': 'text'}
        assert client.post(BASE, content=iter([b'x' * 1024 * 1024] * 9)).status_code == 413
