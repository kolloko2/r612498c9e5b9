import asyncio
import json
from uuid import uuid4

from app.backend.control_ws import ControlWS
from app.config import Settings
from app.domain.call import CallContext


class FakeSocket:
    def __init__(self, fail_send=False, auto_ack=True):
        self.sent = []
        self.fail_send = fail_send
        self.auto_ack = auto_ack
        self.incoming = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def send(self, text):
        if self.fail_send:
            raise ConnectionError("Injected disconnect")
        event = json.loads(text)
        self.sent.append(event)
        if self.auto_ack:
            self.incoming.put_nowait(json.dumps({
                "event_id": str(uuid4()), "seq": event["seq"],
                "session_id": event["session_id"], "type": "backend.ack",
                "elapsed_ms": event["elapsed_ms"],
                "payload": {"event_id": event["event_id"]},
            }))

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
                   backend_retries=2, backend_backoff_s=0.001,
                   backend_reconnect_budget_s=0)
    control = ControlWS(cfg, CallContext(uuid4(), "201"), handler, lambda *args: errors.append(args))
    await control._run()
    assert len(attempts) == 3
    assert errors[0][0] == "backend_unavailable"


async def test_event_remains_pending_until_matching_application_ack(tmp_path, monkeypatch):
    socket = FakeSocket(auto_ack=False)
    monkeypatch.setattr("app.backend.control_ws.connect", lambda *args, **kwargs: socket)

    cfg = Settings(_env_file=None, backend_mode="websocket", outbox_dir=tmp_path,
                   backend_reconnect_budget_s=0)
    context = CallContext(uuid4(), "201")

    async def handler(event):
        pass

    control = ControlWS(cfg, context, handler, lambda *args: None)
    await control.start()
    try:
        event = await control.emit("call.connected", {"call_id": str(context.call_id)})
        while not socket.sent:
            await asyncio.sleep(0)
        assert control.pending == event
        socket.incoming.put_nowait(json.dumps({
            "event_id": str(uuid4()), "seq": event.seq,
            "session_id": str(event.session_id), "type": "backend.ack",
            "elapsed_ms": event.elapsed_ms, "payload": {"event_id": str(event.event_id)},
        }))
        await asyncio.wait_for(control.queue.join(), 1)
        assert control.pending is None
        ack = json.loads(control.ack_journal.read_text(encoding="utf-8").strip())
        assert ack["event_id"] == str(event.event_id)
    finally:
        await control.close()
