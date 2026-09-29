import sqlite3

import pytest
from fastapi import HTTPException

import reset_admin
from accounts import Accounts


class Store:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)


def test_reset_creates_admin_when_missing():
    accounts = Accounts(Store())
    user = accounts.reset_admin("Tech.Admin", "first-password-123", "Тех. админ")
    assert user["username"] == "tech.admin" and user["role"] == "admin" and user["active"]
    assert accounts.login("tech.admin", "first-password-123")["session_token"]


def test_reset_restores_access_to_blocked_demoted_account():
    accounts = Accounts(Store())
    admin = accounts.bootstrap("root", "old-password-1234", "Root")
    teacher = accounts.create_user("tech.admin", "teacher-password-1", "Тех", "teacher")
    accounts.set_active(teacher["id"], False, admin["user"]["id"])
    for _ in range(6):
        with pytest.raises(HTTPException):
            accounts.login("tech.admin", "wrong-password-000")
    accounts.db.execute("INSERT INTO account_mfa VALUES (?,?,?,?,?,?)", (teacher["id"], "S", 1, None, "[]", 0))
    user = accounts.reset_admin("tech.admin", "new-password-12345", "ignored")
    assert user["id"] == teacher["id"] and user["role"] == "admin" and user["active"]
    assert not accounts.mfa_enabled(teacher["id"])
    assert accounts.login("tech.admin", "new-password-12345")["session_token"]
    with pytest.raises(HTTPException):
        accounts.login("tech.admin", "teacher-password-1")
    events = [row[0] for row in accounts.db.execute("SELECT event FROM account_audit")]
    assert "account.admin_reset" in events


def test_cli_prints_generated_password_once(tmp_path, monkeypatch, capsys):
    db = tmp_path / "t.sqlite3"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RESET_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("DIALOGUE_DB", str(db))
    assert reset_admin.main(["--username", "jury.admin"]) == 0
    out = capsys.readouterr().out
    password = out.split("Новый пароль: ")[1].split()[0]
    assert len(password) == 23 and not set("0O1lI") & set(password)
    store = reset_admin._Store(str(db))
    assert Accounts(store).login("jury.admin", password)["user"]["role"] == "admin"
