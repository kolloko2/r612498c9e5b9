import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.asterisk.call_manager import CallManager
from app.config import Settings
from app.domain.messages import CreateCall
from app.main import create_app


def test_rest_lifecycle_validation_and_auth(tmp_path):
    cfg = Settings(_env_file=None, recording_dir=tmp_path / "recordings",
                   outbox_dir=tmp_path / "outbox", api_token="local-test-token")
    with TestClient(create_app(cfg)) as client:
        health = client.get("/api/v1/health").json()
        assert health["status"] == "ok"
        assert health["backend_control"]["delivery"] == "in_process_mock"
        assert health["speech"]["stt_model_configured"] is False
        assert health["speech"]["stt_model_available"] is False
        assert health["security_audit"]["enabled"] is False
        assert health["security_audit"]["minimum_retention_days"] == 183
        body = {"session_id": str(uuid4()), "extension": "201"}
        assert client.post("/api/v1/calls", json=body).status_code == 401
        client.headers["Authorization"] = "Bearer local-test-token"
        assert client.post("/calls", json={**body, "extension": "112"}).status_code == 400
        assert client.post("/calls", json={**body, "session_id": "bad"}).status_code == 422
        result = client.post("/api/v1/calls", json=body)
        assert result.status_code == 201
        call_id = result.json()["call_id"]
        duplicate = client.post('/api/v1/calls', json=body)
        assert duplicate.json()['call_id'] == call_id
        busy = client.post('/api/v1/calls', json={**body, 'session_id': str(uuid4())})
        assert busy.status_code == 429
        deadline = time.monotonic() + 5
        while client.get(f"/calls/{call_id}").json()["status"] != "active":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert client.post(f"/calls/{call_id}/mock/audio", content=b"x").status_code == 400
        ended = client.post(f"/calls/{call_id}/hangup")
        assert ended.json()["status"] == "ended"
        assert ended.json()["reason"] == "api_hangup"
        assert client.get("/api/v1/health").json()["active_calls"] == 0


async def test_unverified_conversation_only_allows_explicit_probe_extension(tmp_path):
    cfg = Settings(_env_file=None, telephony_mode="asterisk", pipeline_mode="conversation",
                   topology_verified=False, topology_probe_extension="220",
                   allowed_extensions="201,220", ari_password="x", media_password="x",
                   api_token="x", outbox_dir=tmp_path)
    manager = CallManager(cfg)
    with pytest.raises(ValueError, match="probe extension"):
        await manager.create(CreateCall(session_id=uuid4(), extension="201"))


def test_health_reports_missing_speech_library(monkeypatch):
    """Смонтированной модели мало: без библиотеки разговор оборвётся.

    Образ Voice собирается без vosk/sherpa/torch, когда
    INSTALL_LOCAL_PROVIDERS=false. Раньше health в этом случае сообщал
    готовность, и отказ обнаруживался только на первой реплике звонка.
    """
    from app.api import health as health_module

    assert health_module._library_ready("mock") is None
    monkeypatch.setattr(health_module.importlib.util, "find_spec", lambda name: None)
    assert health_module._library_ready("vosk") is False
    assert health_module._library_ready("hybrid") is False
    assert health_module._library_ready("silero") is False
    assert health_module._missing("hybrid") is True
    monkeypatch.setattr(health_module.importlib.util, "find_spec", lambda name: object())
    assert health_module._library_ready("hybrid") is True
    assert health_module._missing("hybrid") is False


def test_health_is_unavailable_when_speech_model_files_are_missing(tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app

    settings = Settings(_env_file=None, stt_provider="mock", tts_provider="silero",
                        tts_voice=str(tmp_path / "missing.pt"),
                        recording_dir=tmp_path / "rec", outbox_dir=tmp_path / "out")
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 503 and response.json()["status"] == "degraded"
