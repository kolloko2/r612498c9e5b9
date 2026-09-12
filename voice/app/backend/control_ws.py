import asyncio
import json
import os
from uuid import uuid4

from websockets.asyncio.client import connect

from app.audio.latency import log_event
from app.domain.messages import EventEnvelope


class ControlWS:
    """Bounded reconnect with an append-only durable event journal.

    Backend must deduplicate event_id. WebSocket send is NOT an application ACK.
    Journals retain all events for explicit replay/reconciliation after failure.
    """

    def __init__(self, settings, context, on_message, on_fatal):
        self.settings, self.context = settings, context
        self.on_message, self.on_fatal = on_message, on_fatal
        self.queue = asyncio.Queue(maxsize=1000)
        self.send_lock = asyncio.Lock()
        self.connected = asyncio.Event()
        self.task = self.socket = None
        self.pending = None
        self.journal = settings.outbox_dir / str(context.session_id) / f"{context.call_id}.jsonl"
        self.mock_seq = 0
        self.manual = False

    async def start(self):
        await asyncio.to_thread(self.journal.parent.mkdir, parents=True, exist_ok=True)
        if self.settings.backend_mode == "mock":
            self.connected.set()
        else:
            self.task = asyncio.create_task(self._run(), name="backend-control")
            await asyncio.wait_for(self.connected.wait(), self.settings.connect_timeout_s)

    def _append(self, event):
        with open(self.journal, "a", encoding="utf-8") as file:
            file.write(event.model_dump_json() + "\n")
            file.flush()
            os.fsync(file.fileno())

    async def emit(self, kind, payload):
        async with self.send_lock:
            self.context.seq += 1
            event = EventEnvelope(seq=self.context.seq, session_id=self.context.session_id,
                                  type=kind, elapsed_ms=self.context.elapsed_ms(), payload=payload)
            await asyncio.to_thread(self._append, event)
            if self.settings.backend_mode == "mock":
                await self._mock(event)
            else:
                self.queue.put_nowait(event)
        return event

    async def _mock(self, event):
        if self.manual or event.type not in ("call.connected", "operator.utterance"):
            return
        self.mock_seq += 1
        payload = {"reply_id": str(uuid4()), "text": "Учебный звонок. Начинайте."}
        if event.type == "operator.utterance":
            payload.update(text="Принято: " + event.payload["text"],
                           utterance_id=event.payload["utterance_id"])
        reply = EventEnvelope(seq=self.mock_seq, session_id=self.context.session_id,
                              type="caller.reply", elapsed_ms=self.context.elapsed_ms(), payload=payload)
        await self.on_message(reply)

    async def _send(self, ws):
        while True:
            if self.pending is None:
                self.pending = await self.queue.get()
            await ws.send(self.pending.model_dump_json())
            self.queue.task_done()
            self.pending = None

    async def _read(self, ws):
        async for data in ws:
            if not isinstance(data, str):
                raise ValueError("Backend control must be JSON text")
            event = EventEnvelope.model_validate_json(data)
            if event.session_id != self.context.session_id:
                raise ValueError("Backend session mismatch")
            await self.on_message(event)

    async def _run(self):
        cfg = self.settings
        for attempt in range(cfg.backend_retries + 1):
            sender = reader = None
            try:
                headers = {}
                if cfg.backend_token.get_secret_value():
                    headers["Authorization"] = "Bearer " + cfg.backend_token.get_secret_value()
                async with connect(cfg.backend_url.format(session_id=self.context.session_id),
                                   additional_headers=headers, max_size=256000,
                                   open_timeout=10, close_timeout=2) as ws:
                    self.socket = ws
                    self.connected.set()
                    sender = asyncio.create_task(self._send(ws))
                    reader = asyncio.create_task(self._read(ws))
                    done, _ = await asyncio.wait([sender, reader], return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                    raise ConnectionError("Backend closed control connection")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_event("backend.disconnected", call_id=str(self.context.call_id),
                          attempt=attempt, error=type(exc).__name__)
            finally:
                self.connected.clear()
                self.socket = None
                for task in (sender, reader):
                    if task:
                        task.cancel()
                await asyncio.gather(*(t for t in (sender, reader) if t), return_exceptions=True)
            if attempt < cfg.backend_retries:
                await asyncio.sleep(min(8, cfg.backend_backoff_s * 2 ** attempt))
        self.on_fatal("backend_unavailable", "Reconnect budget exhausted; events retained in journal")

    async def close(self):
        if self.task:
            try:
                await asyncio.wait_for(self.queue.join(), 3)
            except TimeoutError:
                log_event("backend.pending_events", call_id=str(self.context.call_id),
                          journal=str(self.journal), count=self.queue.qsize() + bool(self.pending))
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
