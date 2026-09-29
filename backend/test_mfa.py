import mfa
from test_accounts import bootstrap, setup  # noqa: F401 - фикстура

PASSWORD = "correct horse battery"


def headers(token):
    return {"X-User-Session": token}


def current_code(secret, shift=0):
    return mfa.code_at(secret, mfa.current_step() + shift)


def test_rfc6238_reference_codes():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # «12345678901234567890» из RFC 6238
    assert mfa.code_at(secret, 59 // 30) == "287082"
    assert mfa.code_at(secret, 1111111109 // 30) == "081804"
    assert mfa.verify(secret, "287082", now=59) == 1
    assert mfa.verify(secret, "287082", last_step=1, now=59) is None  # повтор кода отклонён
    assert mfa.verify(secret, "12345", now=59) is None


def test_enrol_login_with_code_recovery_and_admin_reset(setup):  # noqa: F811
    accounts, client = setup
    token = bootstrap(client)["session_token"]
    assert client.get("/api/v1/auth/me", headers=headers(token)).json()["mfa"]["enabled"] is False

    setup_data = client.post("/api/v1/auth/mfa/setup", headers=headers(token)).json()
    secret = setup_data["secret"]
    assert setup_data["otpauth_uri"].startswith("otpauth://totp/")
    assert client.post("/api/v1/auth/mfa/enable", headers=headers(token), json={"code": "000000"}).status_code == 400
    enabled = client.post("/api/v1/auth/mfa/enable", headers=headers(token), json={"code": current_code(secret)}).json()
    assert enabled["enabled"] is True and len(enabled["recovery_codes"]) == 10
    stored = accounts.db.execute("SELECT recovery FROM account_mfa").fetchone()[0]
    assert enabled["recovery_codes"][0] not in stored  # хранятся только хеши

    # Пароль верный — сессии ещё нет, нужен код.
    first = client.post("/api/v1/auth/login", json={"username": "root.user", "password": PASSWORD}).json()
    assert first["mfa_required"] is True and "session_token" not in first
    wrong = client.post("/api/v1/auth/mfa-login", json={"mfa_token": first["mfa_token"], "code": "111111"})
    assert wrong.status_code == 401
    # Код текущего шага уже использован при включении — берём следующий.
    session = client.post("/api/v1/auth/mfa-login", json={"mfa_token": first["mfa_token"], "code": current_code(secret, 1)})
    assert session.status_code == 200 and session.json()["session_token"]
    # Билет одноразовый.
    assert client.post("/api/v1/auth/mfa-login", json={"mfa_token": first["mfa_token"], "code": current_code(secret, 1)}).status_code == 401

    # Резервный код срабатывает один раз.
    recovery = enabled["recovery_codes"][0]
    second = client.post("/api/v1/auth/login", json={"username": "root.user", "password": PASSWORD}).json()
    assert client.post("/api/v1/auth/mfa-login", json={"mfa_token": second["mfa_token"], "code": recovery.lower()}).status_code == 200
    third = client.post("/api/v1/auth/login", json={"username": "root.user", "password": PASSWORD}).json()
    assert client.post("/api/v1/auth/mfa-login", json={"mfa_token": third["mfa_token"], "code": recovery}).status_code == 401
    assert client.get("/api/v1/auth/mfa", headers=headers(token)).json()["recovery_codes_left"] == 9

    # Администратор снимает второй фактор другому пользователю.
    user = client.post("/api/v1/admin/users", headers=headers(token), json={
        "username": "teacher1", "password": PASSWORD, "display_name": "Преподаватель", "role": "teacher"}).json()
    teacher = client.post("/api/v1/auth/login", json={"username": "teacher1", "password": PASSWORD}).json()["session_token"]
    teacher_secret = client.post("/api/v1/auth/mfa/setup", headers=headers(teacher)).json()["secret"]
    client.post("/api/v1/auth/mfa/enable", headers=headers(teacher), json={"code": current_code(teacher_secret)})
    assert client.post("/api/v1/auth/login", json={"username": "teacher1", "password": PASSWORD}).json()["mfa_required"]
    assert client.post(f"/api/v1/admin/users/{user['id']}/mfa-reset", headers=headers(teacher)).status_code == 403
    assert client.post(f"/api/v1/admin/users/{user['id']}/mfa-reset", headers=headers(token)).json()["enabled"] is False
    assert "session_token" in client.post("/api/v1/auth/login", json={"username": "teacher1", "password": PASSWORD}).json()


def test_challenge_expires_after_too_many_wrong_codes(setup):  # noqa: F811
    _accounts, client = setup
    token = bootstrap(client)["session_token"]
    secret = client.post("/api/v1/auth/mfa/setup", headers=headers(token)).json()["secret"]
    client.post("/api/v1/auth/mfa/enable", headers=headers(token), json={"code": current_code(secret)})
    challenge = client.post("/api/v1/auth/login", json={"username": "root.user", "password": PASSWORD}).json()["mfa_token"]
    for _ in range(5):
        assert client.post("/api/v1/auth/mfa-login", json={"mfa_token": challenge, "code": "000000"}).status_code == 401
    # После пяти ошибок даже верный код по этому билету не принимается.
    late = client.post("/api/v1/auth/mfa-login", json={"mfa_token": challenge, "code": current_code(secret, 1)})
    assert late.status_code == 401 and "истекло" in late.json()["detail"]


def test_required_mfa_blocks_everything_but_setup_on_the_server():
    import sqlite3
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from accounts import Accounts

    class Store:
        db = sqlite3.connect(":memory:", check_same_thread=False)

    accounts = Accounts(Store())
    app = FastAPI()
    accounts.install_mfa_guard(app)
    app.include_router(accounts.router(lambda: None))

    @app.get("/api/v1/instructor/probe")
    def probe(user=Depends(accounts.require())):
        return {"ok": user["id"]}

    client = TestClient(app)
    token = bootstrap(client)["session_token"]
    assert client.get("/api/v1/instructor/probe", headers=headers(token)).status_code == 200
    policy = client.get("/api/v1/admin/policy", headers=headers(token)).json()
    client.put("/api/v1/admin/policy", headers=headers(token), json={**policy, "mfa_required_roles": ["admin"]})
    # Без второго фактора открыт только раздел входа.
    blocked = client.get("/api/v1/instructor/probe", headers=headers(token))
    assert blocked.status_code == 403 and "двухфакторный" in blocked.json()["detail"]
    assert client.get("/api/v1/auth/me", headers=headers(token)).json()["mfa"]["required"] is True
    secret = client.post("/api/v1/auth/mfa/setup", headers=headers(token)).json()["secret"]
    client.post("/api/v1/auth/mfa/enable", headers=headers(token), json={"code": current_code(secret)})
    assert client.get("/api/v1/instructor/probe", headers=headers(token)).status_code == 200
    # Обязательный второй фактор нельзя отключить самому.
    assert client.post("/api/v1/auth/mfa/disable", headers=headers(token), json={"code": current_code(secret, 1)}).status_code == 409
    users = client.get("/api/v1/admin/users", headers=headers(token)).json()
    assert users[0]["mfa_enabled"] is True
