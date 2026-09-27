"""Owner-scoped reference library with bounded, teacher-approved AI excerpts."""
import base64
import binascii
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import UUID, uuid4
from zipfile import ZipFile, BadZipFile

from pypdf import PdfReader

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from material_text import extract, retrieve
from curriculum import Difficulty, DdsProfile, DIFFICULTIES, PROFILES

MAX_FILE = 25 * 1024 * 1024


def material_context(store, teacher_id: str | None, profile: str = 'general',
                     group_id: str | None = None, query: str = '') -> list[dict]:
    """Retrieve task-relevant passages across all published, scoped materials."""
    if not teacher_id:
        return []
    try:
        rows = store.db.execute(
            "SELECT body FROM materials WHERE teacher_id=? "
            "ORDER BY json_text(body,'updated_at') DESC", (teacher_id,)).fetchall()
    except Exception:
        return []
    documents = []
    for row in rows:
        item = json.loads(row[0])
        if not item.get('published') or item.get('dds_profile') not in ('general', profile):
            continue
        if group_id and group_id not in item.get('group_ids', []):
            continue
        if '_passages' not in item:
            # Old documents remain usable without expensive OCR inside a call.
            item['_passages'], _ = extract(None, '', item.get('body', ''))
        documents.append(item)
    return retrieve(documents, query)


def install_upload_limit(app):
    @app.middleware('http')
    async def bounded_upload(request, call_next):
        if request.method in ('POST', 'PUT') and request.url.path.startswith('/api/v1/instructor/materials'):
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 36 * 1024 * 1024:
                    return JSONResponse({'detail': 'Запрос превышает 36 МиБ'}, status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        return await call_next(request)


class MaterialWrite(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(default='', max_length=2000)
    body: str = Field(default='', max_length=100000)
    difficulty: Difficulty = 'basic'
    dds_profile: DdsProfile = 'general'
    group_ids: list[UUID] = Field(default_factory=list, max_length=100)
    published: bool = False
    filename: str = Field(default='', max_length=180)
    file_base64: str = Field(default='', max_length=34952536)
    remove_attachment: bool = False


class MaterialUpdate(MaterialWrite):
    revision: int = Field(ge=1)


def attachment(body):
    if not body.file_base64:
        if body.filename:
            raise HTTPException(422, 'Выберите файл повторно')
        return None
    if body.remove_attachment:
        raise HTTPException(422, 'Нельзя одновременно удалить и загрузить файл')
    name = body.filename
    if not name or PurePosixPath(name).name != name or any(c in name for c in '\\:\x00\r\n'):
        raise HTTPException(422, 'Некорректное имя файла')
    ext = PurePosixPath(name).suffix.lower()
    if ext not in ('.pdf', '.txt', '.docx', '.xlsx'):
        raise HTTPException(422, 'Допустимы PDF, TXT (UTF-8), DOCX и XLSX')
    try:
        raw = base64.b64decode(body.file_base64, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, 'Некорректный файл') from None
    if not raw or len(raw) > MAX_FILE:
        raise HTTPException(422, 'Размер файла: от 1 байта до 25 МиБ')
    if ext == '.pdf' and not raw.startswith(b'%PDF-'):
        raise HTTPException(422, 'Файл не похож на PDF')
    if ext == '.txt':
        try:
            text = raw.decode('utf-8-sig')
            if '\x00' in text:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise HTTPException(422, 'TXT должен содержать текст UTF-8') from None
    if ext in ('.docx', '.xlsx'):
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                names = {i.filename for i in infos}
                main = 'word/document.xml' if ext == '.docx' else 'xl/workbook.xml'
                if not {'[Content_Types].xml', main} <= names or len(infos) > 2000 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
                    raise ValueError()
                if any('vbaproject' in i.filename.lower() or i.flag_bits & 1 for i in infos):
                    raise ValueError()
        except (BadZipFile, ValueError):
            raise HTTPException(422, 'Некорректный или неподдерживаемый документ Office') from None
    return raw


def router(store, accounts, learning, authorize):
    api = APIRouter(dependencies=[Depends(authorize)])
    teacher = accounts.require('teacher')
    student = accounts.require('student')
    with store.db:
        store.db.execute('CREATE TABLE IF NOT EXISTS materials (id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL, body TEXT NOT NULL, attachment BLOB)')

    def load(mid, user, student_view=False):
        row = store.db.execute('SELECT teacher_id,body,attachment FROM materials WHERE id=?', (str(mid),)).fetchone()
        if not row:
            raise HTTPException(404, 'Материал не найден')
        value = json.loads(row[1])
        if student_view:
            groups = {r[0] for r in store.db.execute('SELECT group_id FROM group_members WHERE student_id=?', (user['id'],))}
            permitted = value['published'] and bool(groups.intersection(value['group_ids']))
        else:
            permitted = row[0] == user['id']
        if not permitted:
            raise HTTPException(404, 'Материал не найден')
        return value, row[2]

    def present(value, raw=None, detail=False, student_view=False):
        result = {k:v for k,v in value.items() if not k.startswith('_')}
        if not detail and result.get('extraction'):
            result['extraction'] = {k:v for k,v in result['extraction'].items() if k != 'preview'}
        if not detail:
            result.pop('body', None)
        else:
            result['file_base64'] = base64.b64encode(raw).decode('ascii') if raw else ''
        if student_view:
            result.pop('group_ids', None)
            result.pop('teacher_id', None)
        return result

    def save(body, user, old=None, prior_file=None):
        groups = list(dict.fromkeys(str(g) for g in body.group_ids))
        if any(not learning._group(g, user['id']) for g in groups):
            raise HTTPException(404, 'Группа не найдена')
        if body.published and not groups:
            raise HTTPException(422, 'Для публикации выберите группы')
        raw = attachment(body)
        filename = body.filename
        if raw is None and old and not body.remove_attachment:
            raw, filename = prior_file, old['filename']
        if not body.body and raw is None:
            raise HTTPException(422, 'Добавьте текст или файл методички')
        usage = store.db.execute('SELECT COUNT(*),COALESCE(SUM(length(attachment)),0) FROM materials WHERE teacher_id=? AND id!=?', (user['id'], old['id'] if old else '')).fetchone()
        if usage[0] >= 100 or usage[1] + len(raw or b'') > 500 * 1024 * 1024:
            raise HTTPException(409, 'Лимит преподавателя: 100 материалов и 500 МиБ файлов')
        at = datetime.now(timezone.utc).isoformat()
        value = {**body.model_dump(mode='json', exclude={'file_base64', 'remove_attachment', 'revision'}),
                 'id': old['id'] if old else str(uuid4()), 'teacher_id': user['id'],
                 'filename': filename if raw else '', 'file_size': len(raw or b''), 'group_ids': groups,
                 'revision': old['revision'] + 1 if old else 1, 'created_at': old['created_at'] if old else at, 'updated_at': at}
        value['_passages'], value['extraction'] = extract(raw, filename, body.body)
        with store.db:
            if old:
                latest = store.db.execute('SELECT body FROM materials WHERE id=?', (old['id'],)).fetchone()
                if not latest or json.loads(latest[0])['revision'] != old['revision']:
                    raise HTTPException(409, 'Материал изменился во время обработки файла. Перечитайте актуальную версию.')
            usage = store.db.execute('SELECT COUNT(*),COALESCE(SUM(length(attachment)),0) FROM materials WHERE teacher_id=? AND id!=?', (user['id'], value['id'])).fetchone()
            if usage[0] >= 100 or usage[1] + len(raw or b'') > 500 * 1024 * 1024:
                raise HTTPException(409, 'Лимит преподавателя: 100 материалов и 500 МиБ файлов')
            store.db.execute('INSERT INTO materials VALUES (?,?,?,?) ON CONFLICT (id) DO UPDATE SET teacher_id=excluded.teacher_id,body=excluded.body,attachment=excluded.attachment', (value['id'], user['id'], json.dumps(value, ensure_ascii=False), raw))
        return present(value, raw, detail=True)

    @api.get('/api/v1/instructor/curriculum')
    async def teacher_catalog(user=Depends(teacher)):
        return {'difficulties': DIFFICULTIES, 'profiles': PROFILES}

    @api.get('/api/v1/student/curriculum')
    async def student_catalog(user=Depends(student)):
        return {'difficulties': DIFFICULTIES, 'profiles': PROFILES}

    @api.get('/api/v1/instructor/materials')
    async def teacher_list(user=Depends(teacher)):
        rows = store.db.execute("SELECT body FROM materials WHERE teacher_id=? ORDER BY json_text(body,'updated_at') DESC,id DESC", (user['id'],))
        return [present(json.loads(r[0])) for r in rows]

    @api.post('/api/v1/instructor/materials', status_code=201)
    def create(body: MaterialWrite, user=Depends(teacher)):
        return save(body, user)

    @api.get('/api/v1/instructor/materials/{mid}')
    async def teacher_detail(mid: UUID, user=Depends(teacher)):
        value, raw = load(mid, user)
        return present(value, raw, detail=True)

    @api.put('/api/v1/instructor/materials/{mid}')
    def update(mid: UUID, body: MaterialUpdate, user=Depends(teacher)):
        value, raw = load(mid, user)
        if value['revision'] != body.revision:
            raise HTTPException(409, 'Материал изменился. Сохраните свой текст отдельно и перечитайте актуальную версию.')
        return save(body, user, value, raw)

    @api.get('/api/v1/student/materials')
    async def student_list(user=Depends(student)):
        groups = {r[0] for r in store.db.execute('SELECT group_id FROM group_members WHERE student_id=?', (user['id'],))}
        result = []
        for row in store.db.execute("SELECT body FROM materials WHERE json_text(body,'published')='true' ORDER BY json_text(body,'updated_at') DESC,id DESC"):
            value = json.loads(row[0])
            if groups.intersection(value['group_ids']):
                result.append(present(value, student_view=True))
        return result

    @api.get('/api/v1/student/materials/{mid}')
    async def student_detail(mid: UUID, user=Depends(student)):
        value, raw = load(mid, user, student_view=True)
        return present(value, raw, detail=True, student_view=True)

    return api
