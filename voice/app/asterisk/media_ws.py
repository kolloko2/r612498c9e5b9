import asyncio
import base64
import hmac
import json
from collections import deque

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.audio.formats import FRAME_BYTES, FRAME_MS, SILENCE, FramePacer

router = APIRouter()


class MediaPeer:
    def __init__(self, ws, role):
        self.ws, self.role = ws, role
        self.writable = asyncio.Event()
        self.writable.set()
        self.lock = asyncio.Lock()
        self.completions = {}
        self.closed = False

    async def command(self, command, **params):
        async with self.lock:
            await asyncio.wait_for(self.ws.send_text(json.dumps({"command": command, **params})), 5)

    async def send(self, pcm):
        if self.role != "playback":
            raise RuntimeError("Cannot inject audio into a capture socket")
        if len(pcm) > 64000:
            raise ValueError("Media message exceeds safe frame size")
        await asyncio.wait_for(self.writable.wait(), 10)
        async with self.lock:
            await asyncio.wait_for(self.ws.send_bytes(pcm), 5)

    def expect_completion(self, correlation_id):
        event = asyncio.Event()
        self.completions[correlation_id] = event
        return event

    def forget_completion(self, correlation_id):
        self.completions.pop(correlation_id, None)

    def control(self, event):
        kind = event.get("event")
        if kind == "MEDIA_XOFF":
            self.writable.clear()
        elif kind == "MEDIA_XON":
            self.writable.set()
        elif kind == "MEDIA_BUFFERING_COMPLETED":
            pending = self.completions.get(event.get("correlation_id"))
            if pending:
                pending.set()

    async def flush(self):
        await self.command("FLUSH_MEDIA")
        # Asterisk resets buffered/paused state and empties its queue.
        self.writable.set()

    async def close(self):
        if not self.closed:
            self.closed = True
            if (getattr(self.ws, 'client_state', None) == WebSocketState.DISCONNECTED
                    or getattr(self.ws, 'application_state', None) == WebSocketState.DISCONNECTED):
                return
            try:
                await asyncio.wait_for(self.ws.close(), 5)
            except WebSocketDisconnect:
                # ARI channel deletion can close the socket before this coroutine.
                pass


class MockPeer:
    """Timed fake PBX output. It never feeds synthesized audio into capture."""

    def __init__(self, on_monitor):
        self.on_monitor = on_monitor
        self.queue = asyncio.Queue(maxsize=250)
        self.commands = deque(maxlen=100)
        self.completions = {}
        self.task = asyncio.create_task(self._run(), name="mock-phone-output")

    async def _run(self):
        pacer = FramePacer()
        while True:
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                item = SILENCE
            if isinstance(item, str):
                self.completions.get(item, asyncio.Event()).set()
                continue
            self.on_monitor(item)
            await pacer.tick()

    async def send(self, pcm):
        for i in range(0, len(pcm), FRAME_BYTES):
            await self.queue.put(pcm[i:i + FRAME_BYTES])

    async def command(self, command, **params):
        self.commands.append(command)
        if command == "STOP_MEDIA_BUFFERING":
            await self.queue.put(params["correlation_id"])

    def expect_completion(self, correlation_id):
        event = asyncio.Event()
        self.completions[correlation_id] = event
        return event

    def forget_completion(self, correlation_id):
        self.completions.pop(correlation_id, None)

    async def flush(self):
        self.commands.append("FLUSH_MEDIA")
        while not self.queue.empty():
            self.queue.get_nowait()

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


@router.websocket("/media")
async def media_socket(ws: WebSocket):
    manager = ws.app.state.manager
    cfg = manager.settings
    expected = "Basic " + base64.b64encode(
        f"{cfg.media_username}:{cfg.media_password.get_secret_value()}".encode()).decode()
    if cfg.telephony_mode != "asterisk" or not hmac.compare_digest(
            ws.headers.get("authorization", ""), expected):
        await ws.close(code=1008)
        return
    protocols = ws.headers.get("sec-websocket-protocol", "").split(",")
    await ws.accept(subprotocol="media" if "media" in [p.strip() for p in protocols] else None)
    runtime = peer = None
    try:
        event = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
        if event.get("event") != "MEDIA_START" or event.get("format") != "slin16":
            raise ValueError("Expected MEDIA_START with slin16 JSON control")
        if event.get("optimal_frame_size") != FRAME_BYTES or event.get("ptime") != FRAME_MS:
            raise ValueError("Only 20ms / 16kHz mono PCM16 is supported")
        binding = manager.media_bindings.get(event.get("channel_id"))
        if not binding:
            raise ValueError("Unrecognized media channel id")
        runtime, role = binding
        peer = MediaPeer(ws, role)
        runtime.attach(role, peer)
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                data = message["bytes"]
                if len(data) > 65500 or len(data) % 2:
                    raise ValueError("Invalid PCM frame")
                runtime.on_audio(role, data)
            elif message.get("text") is not None:
                peer.control(json.loads(message["text"]))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        if runtime:
            runtime.fail("media_protocol", type(exc).__name__)
        await ws.close(code=1008)
    finally:
        if peer:
            peer.closed = True
        if runtime and not runtime.context.stop.is_set():
            runtime.fail("media_disconnected", "Media WebSocket disconnected")
