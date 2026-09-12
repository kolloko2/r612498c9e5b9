import asyncio
import json
from uuid import uuid4

from app.backend.control_ws import ControlWS
from app.config import Settings
from app.domain.call import CallContext


class FakeSocket:
    def __init__(self, fail_send=False):
        self.sent = []
        self.fail_send = fail_send
        self.incoming = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def send(self, text):
        if self.fail_send:
            raise ConnectionError("Injected disconnect")
        self.sent.append(json.loads(text))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()


async def test_reconnect_retries_same_event_id_and_keeps_seq(tmp_path, monkeypatch):
    sockets = [FakeSocket(fail_send=True), FakeSocket()]
    attempts = []

    def connect(url, **kwargs):
        attempts.append(url)
        return sockets[len(attempts) - 1]

    monkeypatch.setattr("app.backend.control_ws.connect", connect)
    received, errors = [], []

    async def handler(event):
        received.append(event)

    cfg = Settings(_env_file=None, backend_mode="websocket", outbox_dir=tmp_path,
                   backend_backoff_s=0.001)
    context = CallContext(uuid4(), "201")
    control = ControlWS(cfg, context, handler, lambda *args: errors.append(args))
    await control.start()
    try:
        first = await control.emit("call.connected", {"call_id": str(context.call_id)})
        second = await control.emit("operator.utterance", {"text": "test"})
        await asyncio.wait_for(control.queue.join(), 3)
        assert len(attempts) == 2
        assert attempts[0] == attempts[1]
        assert [e["event_id"] for e in sockets[1].sent] == [str(first.event_id), str(second.event_id)]
        assert [e["seq"] for e in sockets[1].sent] == [1, 2]
        assert len(control.journal.read_text(encoding="utf-8").splitlines()) == 2
        assert not errors
    finally:
        await control.close()


async def test_backend_reconnect_budget_is_bounded(tmp_path, monkeypatch):
    attempts, errors = [], []

    class FailingConnection:
        async def __aenter__(self):
            raise ConnectionError("offline")

        async def __aexit__(self, *args):
            pass

    def connect(*args, **kwargs):
        attempts.append(1)
        return FailingConnection()

    monkeypatch.setattr("app.backend.control_ws.connect", connect)

    async def handler(event):
        pass

    cfg = Settings(_env_file=None, backend_mode="websocket", outbox_dir=tmp_path,
                   backend_retries=2, backend_backoff_s=0.001)
    control = ControlWS(cfg, CallContext(uuid4(), "201"), handler, lambda *args: errors.append(args))
    await control._run()
    assert len(attempts) == 3
    assert errors[0][0] == "backend_unavailable"
