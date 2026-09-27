import hashlib
import sqlite3

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from accounts import Accounts


class Store:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)


@pytest.fixture
def setup():
    accounts = Accounts(Store())
    app = FastAPI()
    app.include_router(accounts.router(lambda: None))
    return accounts, TestClient(app)


def bootstrap(client):
    response = client.post("/api/v1/auth/bootstrap", json={
        "username": "Root.User", "password": "correct horse battery", "display_name": " Admin "
    })
    assert response.status_code == 201
    return response.json()


def test_bootstrap_is_one_time_and_password_and_token_are_hashed(setup):
    accounts, client = setup
    assert client.get("/api/v1/auth/status").json() == {"bootstrap_required": True}
    result = bootstrap(client)
    assert result["user"] == {
        "id": result["user"]["id"], "username": "root.user", "display_name": "Admin",
        "role": "admin", "active": True,
    }
    assert client.post("/api/v1/auth/bootstrap", json={
        "username": "other", "password": "another secure password", "display_name": "Other"
    }).status_code == 409
    salt, stored = accounts.db.execute(
        "SELECT password_salt,password_hash FROM account_users WHERE id=?", (result["user"]["id"],)
    ).fetchone()
    assert bytes(stored) != b"correct horse battery"
    assert bytes(stored) == hashlib.scrypt(
        b"correct horse battery", salt=bytes(salt), n=16384, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    (token_hash,) = accounts.db.execute("SELECT token_hash FROM account_sessions").fetchone()
    assert result["session_token"] not in token_hash
    assert token_hash == hashlib.sha256(result["session_token"].encode("ascii")).hexdigest()
    assert "password" not in str(accounts.list_users())


def test_login_failure_lock_logout_and_generic_errors(setup):
    accounts, client = setup
    bootstrap(client)
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={
            "username": "ROOT.USER", "password": "wrong-password"
        })
        assert response.status_code == 401
        assert response.json()["detail"] == "Неверные учетные данные"
    locked = client.post("/api/v1/auth/login", json={
        "username": "root.user", "password": "correct horse battery"
    })
    assert locked.status_code == 401
    accounts.db.execute("UPDATE account_login_failures SET locked_until=0")
    accounts.db.commit()
    logged_in = client.post("/api/v1/auth/login", json={
        "username": "root.user", "password": "correct horse battery"
    }).json()
    headers = {"X-User-Session": logged_in["session_token"]}
    assert client.get("/api/v1/auth/me", headers=headers).json()["role"] == "admin"
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    missing = client.post("/api/v1/auth/login", json={
        "username": "nobody", "password": "wrong-password"
    })
    assert missing.json()["detail"] == "Неверные учетные данные"


def test_admin_management_student_denied_and_inactive_session_invalidated(setup):
    accounts, client = setup
    root = bootstrap(client)
    admin_headers = {"X-User-Session": root["session_token"]}
    created = client.post("/api/v1/admin/users", headers=admin_headers, json={
        "username": "student1", "password": "student-password", "display_name": " Student ",
        "role": "student",
    })
    assert created.status_code == 201
    student = created.json()
    assert set(student) == {"id", "username", "display_name", "role", "active"}
    session = client.post("/api/v1/auth/login", json={
        "username": "student1", "password": "student-password"
    }).json()["session_token"]
    student_headers = {"X-User-Session": session}
    assert client.get("/api/v1/admin/users", headers=student_headers).status_code == 403
    assert client.patch(
        f"/api/v1/admin/users/{student['id']}", headers=admin_headers, json={"active": False}
    ).json()["active"] is False
    assert client.get("/api/v1/auth/me", headers=student_headers).status_code == 401
    assert client.post("/api/v1/auth/login", json={
        "username": "student1", "password": "student-password"
    }).status_code == 401
    assert client.patch(
        f"/api/v1/admin/users/{root['user']['id']}", headers=admin_headers, json={"active": False}
    ).status_code == 409


def test_validation_and_missing_session_have_no_fallback(setup):
    _accounts, client = setup
    root = bootstrap(client)
    headers = {"X-User-Session": root["session_token"]}
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/admin/users", headers=headers, json={
        "username": "bad name", "password": "long-enough-password", "display_name": "Name", "role": "student"
    }).status_code == 422
    assert client.post("/api/v1/admin/users", headers=headers, json={
        "username": "teacher1", "password": "long-enough-password", "display_name": "Name", "role": "root"
    }).status_code == 422
    with pytest.raises(HTTPException) as exc:
        _accounts.current("")
    assert exc.value.status_code == 401


def test_admin_manages_roles_admins_and_access_policy(setup):
    accounts, client = setup
    root = bootstrap(client)
    h = {"X-User-Session": root["session_token"]}
    created = client.post("/api/v1/admin/users", headers=h, json={
        "username": "second.admin", "password": "another secure password",
        "display_name": "Второй", "role": "admin"})
    assert created.status_code == 201 and created.json()["role"] == "admin"
    uid = created.json()["id"]
    # Нельзя сменить свою роль; другого администратора можно понизить, пока остаётся root.
    assert client.patch(f"/api/v1/admin/users/{root['user']['id']}/role", headers=h,
                        json={"role": "teacher"}).status_code == 409
    changed = client.patch(f"/api/v1/admin/users/{uid}/role", headers=h, json={"role": "teacher"})
    assert changed.status_code == 200 and changed.json()["role"] == "teacher"
    assert client.patch(f"/api/v1/admin/users/{root['user']['id']}", headers=h,
                        json={"active": False}).status_code == 409
    policy = client.get("/api/v1/admin/policy", headers=h).json()
    assert policy["failure_limit"] == 5 and policy["session_hours"] == 8
    saved = client.put("/api/v1/admin/policy", headers=h, json={**policy, "failure_limit": 3,
                                                                 "log_level": "WARNING"})
    assert saved.status_code == 200 and accounts.policy().failure_limit == 3
    assert client.put("/api/v1/admin/policy", headers=h, json={**policy, "session_hours": 48}).status_code == 422
    for _ in range(3):
        client.post("/api/v1/auth/login", json={"username": "second.admin", "password": "wrong password here"})
    # После трёх ошибок по новой политике вход заблокирован даже с верным паролем.
    assert client.post("/api/v1/auth/login", json={"username": "second.admin",
                                                   "password": "another secure password"}).status_code == 401
