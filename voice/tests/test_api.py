import time
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_rest_lifecycle_validation_and_auth(tmp_path):
    cfg = Settings(_env_file=None, recording_dir=tmp_path / "recordings",
                   outbox_dir=tmp_path / "outbox", api_token="local-test-token")
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/v1/health").json()["status"] == "ok"
        body = {"session_id": str(uuid4()), "extension": "201"}
        assert client.post("/api/v1/calls", json=body).status_code == 401
        client.headers["Authorization"] = "Bearer local-test-token"
        assert client.post("/calls", json={**body, "extension": "112"}).status_code == 400
        assert client.post("/calls", json={**body, "session_id": "bad"}).status_code == 422
        result = client.post("/api/v1/calls", json=body)
        assert result.status_code == 201
        call_id = result.json()["call_id"]
        deadline = time.monotonic() + 5
        while client.get(f"/calls/{call_id}").json()["status"] != "active":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert client.post(f"/calls/{call_id}/mock/audio", content=b"x").status_code == 400
        ended = client.post(f"/calls/{call_id}/hangup")
        assert ended.json()["status"] == "ended"
        assert client.get("/api/v1/health").json()["active_calls"] == 0
