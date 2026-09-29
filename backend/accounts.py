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
import json
import mfa
from contextvars import ContextVar

# Путь текущего запроса: пока обязательный второй фактор не настроен, открыт
# только раздел настройки входа. Заполняется middleware из install_mfa_guard.
REQUEST_PATH = ContextVar('request_path', default='')
MFA_SETUP_PATHS = ('/api/v1/auth/',)

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
MFA_CHALLENGE_SECONDS = 300
MFA_CHALLENGE_ATTEMPTS = 5
MFA_ERROR = "Неверный код подтверждения"


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
    # Журнал входов и учётных записей; не меньше 6 месяцев по ТЗ. Основной
    # журнал безопасности хранится без автоматического удаления.
    audit_retention_days: int = Field(365, ge=183, le=3650)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Роли, которым второй фактор обязателен: без него кабинет открывает только
    # настройку приложения-аутентификатора.
    mfa_required_roles: list[Literal["admin", "teacher", "student"]] = Field(default_factory=list, max_length=3)


class MfaCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=6, max_length=20)


class MfaLogin(MfaCode):
    mfa_token: str = Field(min_length=20, max_length=100)


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
            # Второй фактор: секрет TOTP, признак включения, последний принятый шаг
            # (защита от повторного ввода кода) и хеши одноразовых резервных кодов.
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS account_mfa (
                    user_id TEXT PRIMARY KEY,
                    secret TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    last_step INTEGER,
                    recovery TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )"""
            )
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS account_mfa_challenges (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL
                )"""
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
                return self.finish_login(row[0])

    # ---- Второй фактор (TOTP) ----

    def _mfa_row(self, uid: str):
        return self.db.execute(
            "SELECT secret,enabled,last_step,recovery FROM account_mfa WHERE user_id=?", (uid,)
        ).fetchone()

    def mfa_enabled(self, uid: str) -> bool:
        row = self._mfa_row(uid)
        return bool(row and row[1])

    def finish_login(self, uid: str) -> dict:
        """Пароль принят: сессия сразу или сначала код из приложения-аутентификатора."""
        if not self.mfa_enabled(uid):
            return self._issue_session(uid)
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        self.db.execute("DELETE FROM account_mfa_challenges WHERE expires_at < ?", (now,))
        self.db.execute(
            "INSERT INTO account_mfa_challenges VALUES (?,?,?,?)",
            (hashlib.sha256(token.encode("ascii")).hexdigest(), uid, now + MFA_CHALLENGE_SECONDS, 0),
        )
        self._audit("login.mfa_challenge", uid)
        return {"mfa_required": True, "mfa_token": token, "expires_in": MFA_CHALLENGE_SECONDS}

    def _check_second_factor(self, uid: str, code: str) -> bool:
        """TOTP или неиспользованный резервный код; при успехе состояние обновляется."""
        row = self._mfa_row(uid)
        if not row or not row[1]:
            return False
        step = mfa.verify(row[0], code, row[2])
        if step is not None:
            self.db.execute("UPDATE account_mfa SET last_step=? WHERE user_id=?", (step, uid))
            return True
        codes = json.loads(row[3])
        digest = mfa.hash_recovery(code)
        if digest in codes:
            codes.remove(digest)
            self.db.execute("UPDATE account_mfa SET recovery=? WHERE user_id=?", (json.dumps(codes), uid))
            self._audit("mfa.recovery_used", uid)
            return True
        return False

    def login_mfa(self, token: str, code: str) -> dict:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = int(time.time())
        with self._lock:
            with self.db:
                row = self.db.execute(
                    "SELECT user_id,expires_at,attempts FROM account_mfa_challenges WHERE token_hash=?", (token_hash,)
                ).fetchone()
                expired = not row or row[1] <= now or row[2] >= MFA_CHALLENGE_ATTEMPTS
                if expired:
                    self.db.execute("DELETE FROM account_mfa_challenges WHERE token_hash=?", (token_hash,))
                else:
                    user = self.get_user(row[0])
                    accepted = bool(user and user["active"] and self._check_second_factor(row[0], code))
                    if accepted:
                        self.db.execute("DELETE FROM account_mfa_challenges WHERE token_hash=?", (token_hash,))
                        self._audit("login.mfa_succeeded", row[0])
                        return self._issue_session(row[0])
                    # Неудачная попытка фиксируется до ответа: транзакция не откатывается.
                    self.db.execute("UPDATE account_mfa_challenges SET attempts=attempts+1 WHERE token_hash=?", (token_hash,))
                    self._audit("login.mfa_failed", row[0])
            if expired:
                raise HTTPException(401, "Время на ввод кода истекло. Войдите заново.")
            raise HTTPException(401, MFA_ERROR)

    def _mfa_setup_pending(self, user: dict) -> bool:
        required = self.policy().mfa_required_roles
        return bool(required) and user["role"] in required and not self.mfa_enabled(user["id"])

    def install_mfa_guard(self, app) -> None:
        @app.middleware("http")
        async def remember_path(request, call_next):
            token = REQUEST_PATH.set(request.url.path)
            try:
                return await call_next(request)
            finally:
                REQUEST_PATH.reset(token)

    def mfa_status(self, user: dict) -> dict:
        row = self._mfa_row(user["id"])
        return {"enabled": bool(row and row[1]),
                "required": user["role"] in self.policy().mfa_required_roles,
                "recovery_codes_left": len(json.loads(row[3])) if row and row[1] else 0}

    def mfa_setup(self, user: dict) -> dict:
        with self._lock, self.db:
            if self.mfa_enabled(user["id"]):
                raise HTTPException(409, "Двухфакторный вход уже включён. Чтобы сменить устройство, сначала отключите его.")
            secret = mfa.new_secret()
            self.db.execute(
                "INSERT INTO account_mfa VALUES (?,?,?,?,?,?) ON CONFLICT (user_id) DO UPDATE SET "
                "secret=excluded.secret, enabled=0, last_step=NULL, recovery='[]', created_at=excluded.created_at",
                (user["id"], secret, 0, None, "[]", int(time.time())),
            )
        uri = mfa.otpauth_uri(secret, user["username"])
        return {"secret": secret, "otpauth_uri": uri, "qr_svg": mfa.qr_svg(uri),
                "digits": mfa.DIGITS, "period": mfa.STEP_SECONDS}

    def mfa_enable(self, user: dict, code: str) -> dict:
        with self._lock, self.db:
            row = self._mfa_row(user["id"])
            if not row:
                raise HTTPException(409, "Сначала получите ключ для приложения")
            if row[1]:
                raise HTTPException(409, "Двухфакторный вход уже включён")
            step = mfa.verify(row[0], code)
            if step is None:
                raise HTTPException(400, "Код не подошёл. Проверьте время на телефоне и введите новый код.")
            codes = mfa.recovery_codes()
            self.db.execute(
                "UPDATE account_mfa SET enabled=1, last_step=?, recovery=? WHERE user_id=?",
                (step, json.dumps([mfa.hash_recovery(c) for c in codes]), user["id"]),
            )
            self._audit("mfa.enabled", user["id"])
        return {"enabled": True, "recovery_codes": codes}

    def mfa_disable(self, user: dict, code: str) -> dict:
        with self._lock, self.db:
            if not self._check_second_factor(user["id"], code):
                raise HTTPException(400, MFA_ERROR)
            self.db.execute("DELETE FROM account_mfa WHERE user_id=?", (user["id"],))
            self._audit("mfa.disabled", user["id"])
        return {"enabled": False}

    def mfa_reset(self, uid: str, actor_id: str) -> dict:
        """Администратор снимает второй фактор, если пользователь потерял телефон."""
        with self._lock, self.db:
            if not self._user_row(uid=uid):
                raise HTTPException(404, "Пользователь не найден")
            self.db.execute("DELETE FROM account_mfa WHERE user_id=?", (uid,))
            self.db.execute("DELETE FROM account_mfa_challenges WHERE user_id=?", (uid,))
            self._audit("mfa.reset_by_admin", uid)
            self._audit("mfa.reset_performed", actor_id)
        return {"user_id": uid, "enabled": False}

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
            if not REQUEST_PATH.get().startswith(MFA_SETUP_PATHS) and self._mfa_setup_pending(user):
                raise HTTPException(403, "Для вашей роли обязателен двухфакторный вход. Настройте его в кабинете.")
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
            enabled = {row[0] for row in self.db.execute("SELECT user_id FROM account_mfa WHERE enabled=1")}
            return [{**self._public(row), "mfa_enabled": row[0] in enabled} for row in rows]

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

    def reset_admin(self, username: str, password: str, display_name: str) -> dict:
        """Серверный сброс доступа администратора (reset_admin.py): создаёт учётную
        запись или задаёт ей новый пароль, включает её и снимает блокировку, второй
        фактор и прежние сессии. Через API не вызывается."""
        username = username.strip().lower()
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt)
        with self._lock, self.db:
            row = self._user_row(username=username)
            if row:
                uid = row[0]
                self.db.execute(
                    "UPDATE account_users SET role='admin', active=1, password_salt=?, password_hash=? WHERE id=?",
                    (salt, password_hash, uid),
                )
                self._audit("account.admin_reset", uid)
            else:
                uid = str(uuid4())
                self.db.execute(
                    "INSERT INTO account_users VALUES (?,?,?,?,?,?,?,?)",
                    (uid, username, display_name, "admin", 1, salt, password_hash, int(time.time())),
                )
                self._audit("account.admin_created", uid)
            self.db.execute("DELETE FROM account_sessions WHERE user_id=?", (uid,))
            self.db.execute("DELETE FROM account_login_failures WHERE username=?", (username,))
            self.db.execute("DELETE FROM account_mfa WHERE user_id=?", (uid,))
            self.db.execute("DELETE FROM account_mfa_challenges WHERE user_id=?", (uid,))
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

        @auth.post("/mfa-login")
        def login_mfa(body: MfaLogin):
            return self.login_mfa(body.mfa_token, body.code)

        @auth.get("/me")
        def me(user: dict = Depends(self.require())):
            return {**user, "mfa": self.mfa_status(user)}

        @auth.get("/mfa")
        def mfa_status(user: dict = Depends(self.require())):
            return self.mfa_status(user)

        @auth.post("/mfa/setup")
        def mfa_setup(user: dict = Depends(self.require())):
            return self.mfa_setup(user)

        @auth.post("/mfa/enable")
        def mfa_enable(body: MfaCode, user: dict = Depends(self.require())):
            return self.mfa_enable(user, body.code)

        @auth.post("/mfa/disable")
        def mfa_disable(body: MfaCode, user: dict = Depends(self.require())):
            if self.mfa_status(user)["required"]:
                raise HTTPException(409, "Для вашей роли двухфакторный вход обязателен")
            return self.mfa_disable(user, body.code)

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

        @admin.get("/users/{uid}/mfa")
        def user_mfa(uid: str, _user: dict = Depends(self.require("admin"))):
            return {"user_id": uid, "enabled": self.mfa_enabled(uid)}

        @admin.post("/users/{uid}/mfa-reset")
        def reset_mfa(uid: str, user: dict = Depends(self.require("admin"))):
            return self.mfa_reset(uid, user["id"])

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
