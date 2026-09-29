"""Fail-closed Voice transport audit without headers, queries or media content."""
import asyncio
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from starlette.responses import JSONResponse

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")



def route_template(scope) -> str:
    """Шаблон маршрута без значений параметров. Новые FastAPI хранят во вложенном
    роутере путь без префикса подключения; префикс восстанавливается по сегментам пути."""
    template = getattr(scope.get("route"), "path", None)
    if not template:
        return "[unmatched]"
    parts = scope.get("path", "").rstrip("/").split("/")
    keep = len(parts) - template.rstrip("/").count("/")
    return "/".join(parts[:keep]) + template if keep > 1 else template

class VoiceAuditLog:
    minimum_retention_days = 183

    def __init__(self, folder):
        self.folder = Path(folder) if folder else None
        self.lock = threading.RLock()
        self.failed = False

    def append(self, event):
        if self.folder is None:
            return
        with self.lock:
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                target = self.folder / (datetime.now(timezone.utc).date().isoformat() + ".jsonl")
                with target.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                self.failed = False
            except OSError:
                self.failed = True
                raise

    def archive(self):
        if not self.folder or not self.folder.exists():
            return
        today = datetime.now(timezone.utc).date().isoformat()
        with self.lock:
            for source in self.folder.glob("*.jsonl"):
                if not DAY.fullmatch(source.stem) or source.stem >= today:
                    continue
                target = source.with_suffix(".jsonl.gz")
                if target.exists():
                    continue
                temporary = target.with_name(target.name + ".tmp-" + uuid4().hex)
                with source.open("rb") as original, temporary.open("xb") as raw:
                    with gzip.GzipFile(fileobj=raw, mode="wb") as compressed:
                        for chunk in iter(lambda: original.read(65536), b""):
                            compressed.write(chunk)
                    raw.flush()
                    os.fsync(raw.fileno())
                temporary.replace(target)


class VoiceAuditMiddleware:
    def __init__(self, app, audit):
        self.app, self.audit = app, audit

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            return await self._websocket(scope, receive, send)
        if scope["type"] != "http" or self.audit.folder is None:
            return await self.app(scope, receive, send)
        request_id, started = str(uuid4()), time.monotonic()
        method = scope.get("method", "OTHER")
        base = {"request_id": request_id, "method": method, "actor": "service"}
        try:
            await asyncio.to_thread(self.audit.append, {**base, "at": time.time(), "phase": "started"})
        except OSError:
            return await JSONResponse({"detail": "Voice audit unavailable"}, status_code=503)(scope, receive, send)
        status = 500

        async def observed(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, observed)
        finally:
            event = {**base, "at": time.time(), "phase": "finished", "status": status,
                     "route": route_template(scope),
                     "elapsed_ms": round((time.monotonic() - started) * 1000)}
            try:
                await asyncio.to_thread(self.audit.append, event)
            except OSError:
                print("VOICE_AUDIT_WRITE_FAILED", flush=True)

    async def _websocket(self, scope, receive, send):
        if self.audit.folder is None:
            return await self.app(scope, receive, send)
        request_id, started = str(uuid4()), time.monotonic()
        received_messages = sent_messages = received_bytes = sent_bytes = 0
        status = 1006
        try:
            await asyncio.to_thread(self.audit.append, {"request_id": request_id, "at": time.time(),
                "method": "WS", "phase": "started", "actor": "service"})
        except OSError:
            await send({"type": "websocket.close", "code": 1011})
            return

        async def audited_receive():
            nonlocal received_messages, received_bytes, status
            message = await receive()
            if message["type"] == "websocket.receive":
                received_messages += 1
                received_bytes += len(message.get("bytes") or (message.get("text") or "").encode())
            elif message["type"] == "websocket.disconnect":
                status = message.get("code", 1006)
            return message

        async def audited_send(message):
            nonlocal sent_messages, sent_bytes, status
            if message["type"] == "websocket.send":
                sent_messages += 1
                sent_bytes += len(message.get("bytes") or (message.get("text") or "").encode())
            elif message["type"] == "websocket.accept":
                status = 101
            elif message["type"] == "websocket.close":
                status = message.get("code", 1000)
            await send(message)

        try:
            await self.app(scope, audited_receive, audited_send)
        finally:
            event = {"request_id": request_id, "at": time.time(), "method": "WS",
                     "phase": "finished", "actor": "service", "status": status,
                     "route": route_template(scope),
                     "received_messages": received_messages, "sent_messages": sent_messages,
                     "received_bytes": received_bytes, "sent_bytes": sent_bytes,
                     "elapsed_ms": round((time.monotonic() - started) * 1000)}
            try:
                await asyncio.to_thread(self.audit.append, event)
            except OSError:
                print("VOICE_AUDIT_WRITE_FAILED", flush=True)
