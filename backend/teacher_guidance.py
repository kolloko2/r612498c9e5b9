"""Teacher-approved corrections used as bounded context for later model calls."""
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class Correction(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: UUID
    profile: str = Field('general', max_length=30)
    situation: str = Field(min_length=5, max_length=500)
    incorrect: str = Field(min_length=3, max_length=500)
    correct: str = Field(min_length=3, max_length=700)


def initialize(store):
    with store.db:
        store.db.execute('CREATE TABLE IF NOT EXISTS teacher_corrections '
                         '(id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL, body TEXT NOT NULL)')


def examples(store, teacher_id, profile='general', limit=4):
    """Only this teacher's approved examples; newest first, bounded prompt size."""
    if not teacher_id:
        return []
    rows = store.db.execute("SELECT body FROM teacher_corrections WHERE teacher_id=? "
                            "ORDER BY json_text(body,'at') DESC", (teacher_id,)).fetchall()
    values = [json.loads(row[0]) for row in rows]
    return [{'situation': item['situation'], 'incorrect': item['incorrect'],
             'correct': item['correct']} for item in values
            if item.get('active', True) and item['profile'] in ('general', profile)][:limit]


def router(store, accounts, authorize):
    initialize(store)
    api = APIRouter(prefix='/api/v1/instructor/corrections', dependencies=[Depends(authorize)])
    teacher = accounts.require('teacher')

    @api.get('')
    async def listing(user=Depends(teacher)):
        rows = store.db.execute('SELECT body FROM teacher_corrections WHERE teacher_id=? '
                                'ORDER BY id DESC LIMIT 100', (user['id'],)).fetchall()
        return [json.loads(row[0]) for row in rows]

    @api.post('', status_code=201)
    async def add(body: Correction, user=Depends(teacher)):
        identifier = str(body.request_id)
        old = store.db.execute('SELECT teacher_id,body FROM teacher_corrections WHERE id=?',
                               (identifier,)).fetchone()
        payload = body.model_dump(mode='json')
        if old:
            if old[0] != user['id'] or json.loads(old[1])['request'] != payload:
                raise HTTPException(409, 'Идентификатор исправления уже использован')
            return json.loads(old[1])
        count = store.db.execute('SELECT COUNT(*) FROM teacher_corrections WHERE teacher_id=?',
                                 (user['id'],)).fetchone()[0]
        if count >= 100:
            raise HTTPException(409, 'Достигнут лимит исправлений')
        value = {**payload, 'id': identifier, 'teacher_id': user['id'], 'active': True,
                 'at': datetime.now(timezone.utc).isoformat(), 'request': payload}
        with store.db:
            store.db.execute('INSERT INTO teacher_corrections VALUES (?,?,?)',
                             (identifier, user['id'], json.dumps(value, ensure_ascii=False)))
        return value

    @api.post('/{cid}/disable')
    async def disable(cid: UUID, user=Depends(teacher)):
        row = store.db.execute('SELECT body FROM teacher_corrections WHERE id=? AND teacher_id=?',
                               (str(cid), user['id'])).fetchone()
        if not row:
            raise HTTPException(404, 'Исправление не найдено')
        value = json.loads(row[0])
        if value.get('active', True):
            value['active'] = False
            value['disabled_at'] = datetime.now(timezone.utc).isoformat()
            with store.db:
                store.db.execute('UPDATE teacher_corrections SET body=? WHERE id=?',
                                 (json.dumps(value, ensure_ascii=False), str(cid)))
        return value

    return api
