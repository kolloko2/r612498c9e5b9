import asyncio
import json
import os
import time
from uuid import UUID
from uuid import uuid4

from websockets.asyncio.client import connect

from app.audio.latency import log_event
from app.domain.messages import EventEnvelope
from app.tls import websocket_ssl_kwargs


class ControlWS:
    """Application-ACK delivery with bounded reconnect and a durable journal.

    Only one event is in flight, so call.ended cannot pass an earlier event. Backend
    deduplicates event_id and answers with backend.ack after durable processing.
    A socket send alone never removes an event from the unacknowledged set.
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
        self.ack_journal = self.journal.with_suffix(".acked.jsonl")
        self.ack_received = asyncio.Event()
        self.last_ack_at = 0.0
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

    def _append_ack(self, event_id):
        with open(self.ack_journal, "a", encoding="utf-8") as file:
            file.write(json.dumps({"event_id": str(event_id)}, separators=(",", ":")) + "\n")
            file.flush()
            os.fsync(file.fileno())

    async def emit(self, kind, payload):
        async with self.send_lock:
            event = EventEnvelope(seq=self.context.seq + 1, session_id=self.context.session_id,
                                  type=kind, elapsed_ms=self.context.elapsed_ms(), payload=payload)
            write = asyncio.create_task(asyncio.to_thread(self._append, event))
            cancelled = False
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                # Do not let a cancelled playback task release the send lock while
                # its disk write is still pending behind call.ended/recording.ready.
                await write
                cancelled = True
            self.context.seq = event.seq
            if self.settings.backend_mode == "mock":
                if not cancelled:
                    await self._mock(event)
            else:
                self.queue.put_nowait(event)
            if cancelled:
                raise asyncio.CancelledError
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
            self.ack_received.clear()
            await ws.send(self.pending.model_dump_json())
            await self.ack_received.wait()

    async def _acknowledge(self, event):
        try:
            event_id = UUID(str(event.payload["event_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid backend.ack payload") from exc
        if self.pending is None or event_id != self.pending.event_id:
            log_event("backend.unexpected_ack", call_id=str(self.context.call_id),
                      event_id=str(event_id))
            return
        await asyncio.to_thread(self._append_ack, event_id)
        self.last_ack_at = time.monotonic()
        self.pending = None
        self.queue.task_done()
        self.ack_received.set()

    async def _read(self, ws):
        async for data in ws:
            if not isinstance(data, str):
                raise ValueError("Backend control must be JSON text")
            event = EventEnvelope.model_validate_json(data)
            if event.session_id != self.context.session_id:
                raise ValueError("Backend session mismatch")
            if event.type == "backend.ack":
                await self._acknowledge(event)
            else:
                await self.on_message(event)

    async def _run(self):
        cfg = self.settings
        attempt = 0
        outage_started = None
        while True:
            sender = reader = None
            connected_at = None
            ack_before = self.last_ack_at
            try:
                headers = {}
                if cfg.backend_token.get_secret_value():
                    headers["Authorization"] = "Bearer " + cfg.backend_token.get_secret_value()
                url = cfg.backend_url.format(session_id=self.context.session_id)
                async with connect(url,
                                   additional_headers=headers, max_size=256000,
                                   open_timeout=10, close_timeout=2,
                                   **websocket_ssl_kwargs(url, cfg.internal_ca_file)) as ws:
                    self.socket = ws
                    connected_at = time.monotonic()
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
                now = time.monotonic()
                connection_was_healthy = (self.last_ack_at > ack_before or
                                           (connected_at is not None and now - connected_at >= 1))
                if outage_started is None or connection_was_healthy:
                    outage_started = now
                    attempt = 0
                log_event("backend.disconnected", call_id=str(self.context.call_id),
                          attempt=attempt, error=type(exc).__name__)
            finally:
                self.connected.clear()
                self.socket = None
                for task in (sender, reader):
                    if task:
                        task.cancel()
                await asyncio.gather(*(t for t in (sender, reader) if t), return_exceptions=True)
            elapsed = 0 if outage_started is None else time.monotonic() - outage_started
            if attempt >= cfg.backend_retries and elapsed >= cfg.backend_reconnect_budget_s:
                break
            delay = min(8, cfg.backend_backoff_s * 2 ** attempt)
            if elapsed < cfg.backend_reconnect_budget_s:
                delay = min(delay, cfg.backend_reconnect_budget_s - elapsed)
            await asyncio.sleep(max(0, delay))
            attempt += 1
        self.on_fatal("backend_unavailable", "Reconnect budget exhausted; events retained in journal")

    async def close(self):
        if self.task:
            if not self.task.done():
                try:
                    await asyncio.wait_for(
                        self.queue.join(), max(3, self.settings.backend_reconnect_budget_s + 5))
                except TimeoutError:
                    log_event("backend.pending_events", call_id=str(self.context.call_id),
                              journal=str(self.journal), count=self.queue.qsize() + bool(self.pending))
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
