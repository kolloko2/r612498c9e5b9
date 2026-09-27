"""Local user accounts, role checks, and opaque browser sessions."""

from __future__ import annotations

import hashlib
import logging
import hmac
import re
import secrets
import threading
import time
from typing import Literal
from uuid import uuid4
from security_audit import AUDIT_ACTOR

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator


Role = Literal["admin", "teacher", "student"]
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,39}$")
AUTH_ERROR = "Неверные учетные данные"
SESSION_ERROR = "Требуется вход"
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SESSION_SECONDS = 8 * 60 * 60
LOCK_SECONDS = 60
FAILURE_LIMIT = 5


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def valid_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not USERNAME_RE.fullmatch(normalized):
            raise ValueError("username must be 3-40 safe ASCII characters")
        return normalized


class BootstrapRequest(Credentials):
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("display_name must not be blank")
        return value


class CreateUserRequest(BootstrapRequest):
    role: Literal["admin", "teacher", "student"]


class UserRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "teacher", "student"]


class AccessPolicy(BaseModel):
    """Политика доступа и журналирования, которую настраивает администратор."""
    model_config = ConfigDict(extra="forbid")
    session_hours: int = Field(SESSION_SECONDS // 3600, ge=1, le=24)
    failure_limit: int = Field(FAILURE_LIMIT, ge=3, le=10)
    lock_seconds: int = Field(LOCK_SECONDS, ge=30, le=3600)
    audit_retention_days: int = Field(365, ge=7, le=3650)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class UserActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


class Accounts:
    """Account storage using the application's configured SQL connection."""

    def __init__(self, store):
        self.store = store
        self.db = store.db
        self._lock = threading.RLock()
        self._dummy_salt = b"\0" * 16
        with self._lock, self.db:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS account_users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin','teacher','student')),
                    active INTEGER NOT NULL CHECK (active IN (0,1)),
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at INTEGER NOT NULL
                )"""
            )
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS account_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )"""
            )
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS account_login_failures (
                    username TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL,
                    locked_until INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )"""
            )
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS account_audit (
                    id TEXT PRIMARY KEY,
                    at INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    user_id TEXT
                )"""
            )
            self.db.execute("CREATE INDEX IF NOT EXISTS account_sessions_user ON account_sessions(user_id)")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS account_policy (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
            )

    def policy(self) -> AccessPolicy:
        # Политика хранится в общей базе: обе реплики Backend читают одно значение.
        row = self.db.execute("SELECT body FROM account_policy WHERE id=1").fetchone()
        policy = AccessPolicy.model_validate_json(row[0]) if row else AccessPolicy()
        logging.getLogger().setLevel(policy.log_level)
        return policy

    def set_policy(self, policy: AccessPolicy, actor_id: str) -> dict:
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO account_policy VALUES (1,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body",
                (policy.model_dump_json(),),
            )
            self.db.execute("DELETE FROM account_audit WHERE at < ?",
                            (int(time.time()) - policy.audit_retention_days * 86400,))
            self._audit("policy.updated", actor_id)
        logging.getLogger().setLevel(policy.log_level)
        return policy.model_dump()

    def set_role(self, uid: str, role: str, actor_id: str) -> dict:
        with self._lock, self.db:
            row = self._user_row(uid=uid)
            if not row:
                raise HTTPException(404, "Пользователь не найден")
            if uid == actor_id:
                raise HTTPException(409, "Свою роль изменить нельзя")
            if row[3] == "admin" and role != "admin" and self._other_admins(uid) == 0:
                raise HTTPException(409, "В системе должен остаться хотя бы один администратор")
            self.db.execute("UPDATE account_users SET role=? WHERE id=?", (role, uid))
            # Права меняются сразу: действующие сессии со старой ролью закрываются.
            self.db.execute("DELETE FROM account_sessions WHERE user_id=?", (uid,))
            self._audit("account.role." + role, uid)
        return self.get_user(uid)

    def _other_admins(self, uid: str) -> int:
        return self.db.execute(
            "SELECT count(*) FROM account_users WHERE role='admin' AND active=1 AND id<>?", (uid,)
        ).fetchone()[0]

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
            dklen=32, maxmem=64 * 1024 * 1024,
        )

    @staticmethod
    def _public(row) -> dict:
        return {
            "id": row[0],
            "username": row[1],
            "display_name": row[2],
            "role": row[3],
            "active": bool(row[4]),
        }

    def _user_row(self, *, username: str | None = None, uid: str | None = None):
        fields = "id,username,display_name,role,active,password_salt,password_hash"
        if username is not None:
            return self.db.execute(f"SELECT {fields} FROM account_users WHERE username=?", (username,)).fetchone()
        return self.db.execute(f"SELECT {fields} FROM account_users WHERE id=?", (uid,)).fetchone()

    def _audit(self, event: str, user_id: str | None) -> None:
        self.db.execute(
            "INSERT INTO account_audit VALUES (?,?,?,?)",
            (str(uuid4()), int(time.time()), event, user_id),
        )

    def bootstrap_required(self) -> bool:
        with self._lock:
            return self.db.execute("SELECT 1 FROM account_users LIMIT 1").fetchone() is None

    def bootstrap(self, username: str, password: str, display_name: str) -> dict:
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt)
        uid = str(uuid4())
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                if self.db.execute("SELECT 1 FROM account_users LIMIT 1").fetchone():
                    raise HTTPException(409, "Первичная настройка уже выполнена")
                self.db.execute(
                    "INSERT INTO account_users VALUES (?,?,?,?,?,?,?,?)",
                    (uid, username, display_name, "admin", 1, salt, password_hash, int(time.time())),
                )
                self._audit("account.bootstrap", uid)
                result = self._issue_session(uid)
                self.db.commit()
                return result
            except Exception:
                self.db.rollback()
                raise

    def _issue_session(self, uid: str) -> dict:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = int(time.time())
        self.db.execute(
            "INSERT INTO account_sessions VALUES (?,?,?,?)",
            (token_hash, uid, now + self.policy().session_hours * 3600, now),
        )
        row = self._user_row(uid=uid)
        return {"user": self._public(row), "session_token": token}

    def _record_failure(self, username: str, uid: str | None) -> None:
        now = int(time.time())
        row = self.db.execute(
            "SELECT failures,locked_until FROM account_login_failures WHERE username=?", (username,)
        ).fetchone()
        failures = (row[0] if row and (row[1] == 0 or row[1] > now) else 0) + 1
        policy = self.policy()
        locked_until = now + policy.lock_seconds if failures >= policy.failure_limit else 0
        self.db.execute(
            "INSERT INTO account_login_failures VALUES (?,?,?,?) "
            "ON CONFLICT (username) DO UPDATE SET failures=excluded.failures, "
            "locked_until=excluded.locked_until, updated_at=excluded.updated_at",
            (username, failures, locked_until, now),
        )
        self._audit("login.failed", uid)
        self.db.execute(
            "DELETE FROM account_login_failures WHERE updated_at < ?", (now - 24 * 60 * 60,)
        )
        excess = self.db.execute("SELECT count(*) - 1000 FROM account_login_failures").fetchone()[0]
        if excess > 0:
            self.db.execute(
                "DELETE FROM account_login_failures WHERE username IN "
                "(SELECT username FROM account_login_failures ORDER BY updated_at LIMIT ?)",
                (excess,),
            )

    def login(self, username: str, password: str) -> dict:
        with self._lock:
            row = self._user_row(username=username)
            salt = bytes(row[5]) if row else self._dummy_salt
            expected = bytes(row[6]) if row else b"\0" * 32
            supplied = self._password_hash(password, salt)
            failure = self.db.execute(
                "SELECT locked_until FROM account_login_failures WHERE username=?", (username,)
            ).fetchone()
            locked = bool(failure and failure[0] > int(time.time()))
            valid = bool(row and row[4] and hmac.compare_digest(supplied, expected))
            if locked or not valid:
                with self.db:
                    self._record_failure(username, row[0] if row else None)
                raise HTTPException(401, AUTH_ERROR)
            with self.db:
                self.db.execute("DELETE FROM account_login_failures WHERE username=?", (username,))
                self._audit("login.succeeded", row[0])
                return self._issue_session(row[0])

    def current(self, token: str) -> dict:
        if not token:
            raise HTTPException(401, SESSION_ERROR)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = int(time.time())
        with self._lock:
            row = self.db.execute(
                """SELECT u.id,u.username,u.display_name,u.role,u.active,s.expires_at
                   FROM account_sessions s JOIN account_users u ON u.id=s.user_id
                   WHERE s.token_hash=?""",
                (token_hash,),
            ).fetchone()
            if not row or not row[4] or row[5] <= now:
                with self.db:
                    self.db.execute("DELETE FROM account_sessions WHERE token_hash=?", (token_hash,))
                raise HTTPException(401, SESSION_ERROR)
            user = self._public(row)
            actor = AUDIT_ACTOR.get()
            if actor is not None:
                actor.update(id=user['id'], role=user['role'])
            return user

    def require(self, *roles: Role):
        def dependency(request: Request, x_user_session: str = Header("", alias="X-User-Session")) -> dict:
            user = self.current(x_user_session)
            request.state.audit_actor = {'id': user['id'], 'role': user['role']}
            if roles and user["role"] not in roles:
                raise HTTPException(403, "Недостаточно прав")
            return user

        return dependency

    def logout(self, token: str) -> None:
        user = self.current(token)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock, self.db:
            self.db.execute("DELETE FROM account_sessions WHERE token_hash=?", (token_hash,))
            self._audit("logout", user["id"])

    def get_user(self, uid: str) -> dict | None:
        with self._lock:
            row = self._user_row(uid=uid)
            return self._public(row) if row else None

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT id,username,display_name,role,active FROM account_users ORDER BY username"
            ).fetchall()
            return [self._public(row) for row in rows]

    def create_user(self, username: str, password: str, display_name: str, role: str) -> dict:
        if role not in ("admin", "teacher", "student"):
            raise HTTPException(422, "Допустима роль администратора, преподавателя или студента")
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt)
        uid = str(uuid4())
        with self._lock:
            try:
                with self.db:
                    self.db.execute(
                        "INSERT INTO account_users VALUES (?,?,?,?,?,?,?,?)",
                        (uid, username, display_name, role, 1, salt, password_hash, int(time.time())),
                    )
                    self._audit("account.created", uid)
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise HTTPException(409, "Имя пользователя уже занято") from None
                raise
        return self.get_user(uid)

    def set_active(self, uid: str, active: bool, actor_id: str | None = None) -> dict:
        with self._lock, self.db:
            row = self._user_row(uid=uid)
            if not row:
                raise HTTPException(404, "Пользователь не найден")
            if row[3] == "admin" and not active and (uid == actor_id or self._other_admins(uid) == 0):
                raise HTTPException(409, "Нельзя заблокировать себя или последнего администратора")
            self.db.execute("UPDATE account_users SET active=? WHERE id=?", (int(active), uid))
            if not active:
                self.db.execute("DELETE FROM account_sessions WHERE user_id=?", (uid,))
            self._audit("account.activated" if active else "account.blocked", uid)
        return self.get_user(uid)

    def router(self, authorize) -> APIRouter:
        combined = APIRouter()
        auth = APIRouter(prefix="/api/v1/auth", dependencies=[Depends(authorize)])
        admin = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(authorize)])

        @auth.get("/status")
        def status():
            return {"bootstrap_required": self.bootstrap_required()}

        @auth.post("/bootstrap", status_code=201)
        def bootstrap(body: BootstrapRequest):
            return self.bootstrap(body.username, body.password, body.display_name)

        @auth.post("/login")
        def login(body: Credentials):
            return self.login(body.username, body.password)

        @auth.get("/me")
        def me(user: dict = Depends(self.require())):
            return user

        @auth.post("/logout", status_code=204)
        def logout(x_user_session: str = Header("", alias="X-User-Session")):
            self.logout(x_user_session)

        @admin.get("/users")
        def users(_user: dict = Depends(self.require("admin"))):
            return self.list_users()

        @admin.post("/users", status_code=201)
        def create(body: CreateUserRequest, _user: dict = Depends(self.require("admin"))):
            return self.create_user(body.username, body.password, body.display_name, body.role)

        @admin.patch("/users/{uid}")
        def change_active(uid: str, body: UserActiveRequest, user: dict = Depends(self.require("admin"))):
            return self.set_active(uid, body.active, user["id"])

        @admin.patch("/users/{uid}/role")
        def change_role(uid: str, body: UserRoleRequest, user: dict = Depends(self.require("admin"))):
            return self.set_role(uid, body.role, user["id"])

        @admin.get("/policy")
        def get_policy(_user: dict = Depends(self.require("admin"))):
            with self._lock:
                return self.policy().model_dump()

        @admin.put("/policy")
        def put_policy(body: AccessPolicy, user: dict = Depends(self.require("admin"))):
            return self.set_policy(body, user["id"])

        combined.include_router(auth)
        combined.include_router(admin)
        return combined


def router(accounts: Accounts, authorize) -> APIRouter:
    """Compatibility helper for application assembly."""
    return accounts.router(authorize)
