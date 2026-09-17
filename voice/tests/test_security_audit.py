import json

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security_audit import VoiceAuditLog, VoiceAuditMiddleware


def events(folder):
    path = next(folder.glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_http_audit_uses_route_template_without_request_secrets(tmp_path):
    audit_dir = tmp_path / "audit"
    cfg = Settings(_env_file=None, recording_dir=tmp_path / "recordings",
                   outbox_dir=tmp_path / "outbox", security_audit_dir=audit_dir,
                   api_token="private-bearer-value")
    with TestClient(create_app(cfg)) as client:
        response = client.get("/api/v1/calls/11111111-1111-1111-1111-111111111111?secret=query",
                              headers={"Authorization": "Bearer private-bearer-value"})
        assert response.status_code == 404
    saved = events(audit_dir)
    finished = saved[-1]
    assert finished["route"] == "/api/v1/calls/{call_id}"
    serialized = json.dumps(saved)
    assert "private-bearer-value" not in serialized
    assert "secret=query" not in serialized


def test_audit_failure_blocks_http_before_mutation(tmp_path):
    unavailable = tmp_path / "not-a-directory"
    unavailable.write_text("occupied", encoding="utf-8")
    cfg = Settings(_env_file=None, recording_dir=tmp_path / "recordings",
                   outbox_dir=tmp_path / "outbox", security_audit_dir=unavailable)
    with TestClient(create_app(cfg)) as client:
        response = client.post("/api/v1/calls", json={
            "session_id": "11111111-1111-1111-1111-111111111111", "extension": "201"})
        assert response.status_code == 503
        assert not client.app.state.manager.calls


def test_websocket_audit_records_counts_not_frame_contents(tmp_path):
    audit_dir = tmp_path / "audit"
    app = FastAPI()

    @app.websocket("/probe/{channel}")
    async def probe(ws: WebSocket, channel: str):
        await ws.accept()
        await ws.receive_bytes()
        await ws.send_text("ok")
        await ws.close()

    app.add_middleware(VoiceAuditMiddleware, audit=VoiceAuditLog(audit_dir))
    secret_frame = b"RAW-PCM-PRIVATE-CONTENT"
    with TestClient(app) as client:
        with client.websocket_connect("/probe/media?credential=hidden") as ws:
            ws.send_bytes(secret_frame)
            assert ws.receive_text() == "ok"
    saved = events(audit_dir)
    finished = saved[-1]
    assert finished["route"] == "/probe/{channel}"
    assert finished["received_messages"] == 1
    assert finished["received_bytes"] == len(secret_frame)
    serialized = json.dumps(saved)
    assert secret_frame.decode() not in serialized
    assert "credential=hidden" not in serialized
