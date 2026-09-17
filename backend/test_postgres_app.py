"""Opt-in application smoke test against an isolated PostgreSQL schema."""

import os
import base64
import importlib.util
from pathlib import Path
import sys
from uuid import uuid4

import pytest


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured")
def test_postgres_bootstrap_workspace_material_and_assessment(monkeypatch):
    import psycopg
    from psycopg import sql

    url = os.environ["TEST_DATABASE_URL"]
    schema = "codex_app_" + uuid4().hex
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(sql.SQL("CREATE SCHEMA {}") .format(sql.Identifier(schema)))
    separator = "&" if "?" in url else "?"
    scoped_url = url + separator + "options=-csearch_path%3D" + schema
    monkeypatch.setenv("DATABASE_URL", scoped_url)
    monkeypatch.setenv("DIALOGUE_TOKEN", "postgres-smoke-service")
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    module_name = "postgres_server_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).with_name("server.py"))
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    from fastapi.testclient import TestClient

    service = {"Authorization": "Bearer postgres-smoke-service"}
    password = "postgres-smoke-password"
    try:
        with TestClient(server.app) as client:
            response = client.post("/api/v1/auth/bootstrap", headers=service, json={
                "username": "admin", "password": password, "display_name": "Администратор",
            })
            assert response.status_code == 201, response.text
            admin_headers = {**service, "X-User-Session": response.json()["session_token"]}

            users = {}
            for username, role in (("teacher", "teacher"), ("student", "student")):
                response = client.post("/api/v1/admin/users", headers=admin_headers, json={
                    "username": username, "password": password,
                    "display_name": username.title(), "role": role,
                })
                assert response.status_code == 201, response.text
                users[username] = response.json()
            tokens = {}
            for username in users:
                response = client.post("/api/v1/auth/login", headers=service, json={
                    "username": username, "password": password,
                })
                assert response.status_code == 200, response.text
                tokens[username] = {**service, "X-User-Session": response.json()["session_token"]}

            scenario_id = server.store.first_enabled()["id"]
            group = client.post("/api/v1/instructor/groups", headers=tokens["teacher"], json={"title": "PG group"}).json()
            assert client.post(
                f"/api/v1/instructor/groups/{group['id']}/members",
                headers=tokens["teacher"], json={"student_id": users["student"]["id"]},
            ).status_code == 200
            assignment = client.post("/api/v1/instructor/assignments", headers=tokens["teacher"], json={
                "group_id": group["id"], "scenario_id": scenario_id, "title": "PG assignment",
            })
            assert assignment.status_code == 201, assignment.text
            session = client.post("/api/v1/student/sessions", headers=tokens["student"], json={
                "scenario_id": scenario_id, "assignment_id": assignment.json()["id"],
            })
            assert session.status_code == 201, session.text
            session_value = session.json()
            session_url = f"/api/v1/student/sessions/{session_value['id']}"
            card = session_value["card"]
            card["description"] = "Saved in PostgreSQL"
            saved = client.put(
                session_url + "/card", headers=tokens["student"],
                json={"revision": session_value["revision"], "card": card},
            )
            assert saved.status_code == 200, saved.text
            assert client.get(session_url, headers=tokens["student"]).json()["card"]["description"] == "Saved in PostgreSQL"

            material = client.post("/api/v1/instructor/materials", headers=tokens["teacher"], json={
                "title": "PostgreSQL material", "body": "Synthetic training text",
                "group_ids": [group["id"]], "published": True,
                "difficulty": "basic", "dds_profile": "general",
                "filename": "training.pdf",
                "file_base64": base64.b64encode(b"%PDF-1.4\nsynthetic").decode(),
            })
            assert material.status_code == 201, material.text
            material_detail = client.get(
                f"/api/v1/student/materials/{material.json()['id']}", headers=tokens["student"]
            )
            assert base64.b64decode(material_detail.json()["file_base64"]) == b"%PDF-1.4\nsynthetic"

            policy = client.put(
                f"/api/v1/instructor/scenarios/{scenario_id}/assessment-policy",
                headers=tokens["teacher"],
                json={"revision": 0, "policy": {"pass_score_percent": 50}},
            )
            assert policy.status_code == 200, policy.text
            finished = client.post(session_url + "/finish", headers=tokens["student"])
            assert finished.status_code == 200, finished.text
            assert client.get(session_url, headers=tokens["student"]).json()["finished_at"]
    finally:
        server.store.db.close()
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE") .format(sql.Identifier(schema)))
        admin.close()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured")
def test_sqlite_to_postgres_migration_roundtrip(tmp_path, monkeypatch):
    import psycopg
    from psycopg import sql
    import server
    from accounts import Accounts
    from learning import Learning
    from workspace import router as workspace_router
    from generation import router as generation_router
    from materials import router as materials_router
    from assessment import router as assessment_router
    from group_insights import router as insights_router

    url = os.environ["TEST_DATABASE_URL"]
    schema = "codex_migration_" + uuid4().hex
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(sql.SQL("CREATE SCHEMA {}") .format(sql.Identifier(schema)))
    separator = "&" if "?" in url else "?"
    scoped_url = url + separator + "options=-csearch_path%3D" + schema

    def initialize(target):
        accounts = Accounts(target)
        learning = Learning(target, accounts)
        workspace_router(target, server.Engine(target), lambda: None, accounts, learning)
        generation_router(target, accounts, lambda: None, server.Scenario)
        materials_router(target, accounts, learning, lambda: None)
        assessment_router(target, accounts, learning, lambda: None)
        insights_router(target, accounts, learning, lambda: None)

    source_path = tmp_path / "source.sqlite3"
    source = server.Store(str(source_path))
    initialize(source)
    source.save("migration-session", {"step": 2, "messages": [], "replies": {}})
    with source.db:
        source.db.execute(
            "INSERT INTO materials VALUES (?,?,?,?)",
            ("migration-material", "teacher", '{"title":"Synthetic"}', b"payload"),
        )
    source.db.close()

    target = server.Store(scoped_url)
    initialize(target)
    target.db.close()
    try:
        tool_path = Path(__file__).parents[1] / "tools" / "migrate_sqlite_to_postgres.py"
        spec = importlib.util.spec_from_file_location("sqlite_pg_migration_test", tool_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        monkeypatch.setenv("DATABASE_URL", scoped_url)
        monkeypatch.setattr(sys, "argv", [str(tool_path), str(source_path), "--apply"])
        migration.main()

        check = psycopg.connect(scoped_url, autocommit=True)
        try:
            assert check.execute("SELECT body FROM sessions WHERE id=%s", ("migration-session",)).fetchone()[0]
            assert check.execute("SELECT attachment FROM materials WHERE id=%s", ("migration-material",)).fetchone()[0] == b"payload"
        finally:
            check.close()
    finally:
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE") .format(sql.Identifier(schema)))
        admin.close()
