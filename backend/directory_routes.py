"""Explicit admin-managed LDAP identity links; directory users never create roles."""
import threading
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from accounts import Credentials, AUTH_ERROR


class DirectoryLink(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    username: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,39}$')


class DirectoryCredentials(Credentials):
    password: str = Field(min_length=1, max_length=128)


def router(accounts, authorize, authenticate, configured):
    with accounts.db:
        accounts.db.execute('CREATE TABLE IF NOT EXISTS directory_links (user_id TEXT PRIMARY KEY, directory_username TEXT NOT NULL UNIQUE)')
    api = APIRouter(dependencies=[Depends(authorize)])
    gate = threading.Lock()

    @api.get('/api/v1/auth/directory-status')
    def status():
        return {'configured': configured()}

    @api.get('/api/v1/admin/directory-links', dependencies=[Depends(accounts.require('admin'))])
    def links():
        with accounts._lock:
            return [{'user_id': r[0], 'username': r[1]} for r in accounts.db.execute(
                'SELECT user_id,directory_username FROM directory_links ORDER BY directory_username').fetchall()]

    @api.post('/api/v1/admin/users/{uid}/directory')
    def link(uid: UUID, body: DirectoryLink, user=Depends(accounts.require('admin'))):
        with accounts._lock, accounts.db:
            target = accounts.get_user(str(uid))
            if not target:
                raise HTTPException(404, 'Пользователь не найден')
            if target['role'] == 'admin':
                raise HTTPException(403, 'Администратор использует локальный вход')
            name = body.username.lower()
            duplicate = accounts.db.execute('SELECT user_id FROM directory_links WHERE directory_username=?', (name,)).fetchone()
            if duplicate and duplicate[0] != str(uid):
                raise HTTPException(409, 'Учётная запись каталога уже связана')
            accounts.db.execute('INSERT INTO directory_links VALUES (?,?) ON CONFLICT (user_id) DO UPDATE SET directory_username=excluded.directory_username', (str(uid), name))
            accounts._audit('directory.link.updated', user['id'])
        return {'user_id': str(uid), 'username': name}

    @api.post('/api/v1/auth/directory-login')
    def login(body: DirectoryCredentials, request: Request):
        if not configured():
            raise HTTPException(503, 'Каталог не настроен')
        if not gate.acquire(blocking=False):
            raise HTTPException(429, 'Вход через каталог занят; повторите запрос')
        try:
            with accounts._lock:
                row = accounts.db.execute('SELECT user_id FROM directory_links WHERE directory_username=?', (body.username,)).fetchone()
                uid = row[0] if row else None
                target = accounts.get_user(uid) if uid else None
                failure = accounts.db.execute('SELECT locked_until FROM account_login_failures WHERE username=?', (body.username,)).fetchone()
                locked = failure and failure[0] > time.time()
            valid = bool(target and target['active'] and target['role'] != 'admin' and not locked)
            if valid:
                valid = authenticate(body.username, body.password)
            with accounts._lock, accounts.db:
                current = accounts.get_user(uid) if uid else None
                binding = accounts.db.execute('SELECT directory_username FROM directory_links WHERE user_id=?', (uid,)).fetchone() if uid else None
                denied = not valid or not current or not current['active'] or current['role'] == 'admin' or not binding or binding[0] != body.username
                if denied:
                    accounts._record_failure(body.username, uid)
                else:
                    accounts.db.execute('DELETE FROM account_login_failures WHERE username=?', (body.username,))
                    accounts._audit('directory.login.succeeded', uid)
                    request.state.audit_actor = {'id': uid, 'role': current['role']}
                    result = accounts.finish_login(uid)
            if denied:
                raise HTTPException(401, AUTH_ERROR)
            return result
        finally:
            gate.release()

    return api
