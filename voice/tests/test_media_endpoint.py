import base64

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app


class Runtime:
    def __init__(self):
        import asyncio
        from types import SimpleNamespace
        self.context = SimpleNamespace(stop=asyncio.Event())
        self.peer = None
        self.received = []
        self.errors = []

    def attach(self, role, peer):
        self.peer = peer

    def on_audio(self, role, data):
        self.received.append((role, data))

    def fail(self, *args):
        self.errors.append(args)


def test_media_handshake_correlation_binary_audio_and_xoff(tmp_path):
    cfg = Settings(_env_file=None, media_username="test", media_password="test-only",
                   outbox_dir=tmp_path / "outbox", recording_dir=tmp_path / "recordings")
    app = create_app(cfg)
    with TestClient(app) as client:
        # Exercise real route against a controlled peer, without a physical PBX.
        app.state.manager.settings.telephony_mode = "asterisk"
        runtime = Runtime()
        app.state.manager.media_bindings["known-channel"] = (runtime, "capture")
        headers = {"Authorization": "Basic " + base64.b64encode(b"test:test-only").decode()}
        with client.websocket_connect("/media", headers=headers, subprotocols=["media"]) as ws:
            ws.send_json({"event": "MEDIA_START", "format": "slin16", "optimal_frame_size": 640,
                          "ptime": 20, "channel_id": "known-channel"})
            ws.send_bytes(bytes(640))
            ws.send_json({"event": "MEDIA_XOFF"})
        assert runtime.received == [("capture", bytes(640))]
        assert not runtime.peer.writable.is_set()
        assert runtime.errors[0][0] == "media_disconnected"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/media"):
                pass


def test_media_rejects_wrong_format_before_attaching(tmp_path):
    cfg = Settings(_env_file=None, media_password="test-only", outbox_dir=tmp_path / "outbox",
                   recording_dir=tmp_path / "recordings")
    app = create_app(cfg)
    with TestClient(app) as client:
        app.state.manager.settings.telephony_mode = "asterisk"
        headers = {"Authorization": "Basic " + base64.b64encode(b"asterisk:test-only").decode()}
        with client.websocket_connect("/media", headers=headers) as ws:
            ws.send_json({"event": "MEDIA_START", "format": "ulaw"})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
