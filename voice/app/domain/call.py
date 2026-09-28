import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4


class CallStatus(StrEnum):
    created = "created"
    calling = "calling"
    ringing = "ringing"
    active = "active"
    ended = "ended"
    failed = "failed"


@dataclass
class CallContext:
    session_id: UUID
    extension: str
    call_id: UUID = field(default_factory=uuid4)
    status: CallStatus = CallStatus.created
    created_at: float = field(default_factory=time.monotonic)
    answered: asyncio.Event = field(default_factory=asyncio.Event)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str = "hangup"
    failed: bool = False
    seq: int = 0
    seen_replies: set[str] = field(default_factory=set)
    caller_name: str | None = None
    caller_number: str | None = None

    def caller_id(self):
        """Caller ID для ARI: ``"Имя" <номер>``; без сведений — пусто."""
        name = (self.caller_name or "").strip()
        number = self.caller_number or ""
        if name and number:
            return f'"{name}" <{number}>'
        return f'"{name}"' if name else (f"<{number}>" if number else "")

    def elapsed_ms(self):
        return max(0, round((time.monotonic() - self.created_at) * 1000))

    def snapshot(self):
        result = {"call_id": str(self.call_id), "session_id": str(self.session_id),
                  "extension": self.extension, "status": self.status.value,
                  "elapsed_ms": self.elapsed_ms()}
        if self.status in (CallStatus.ended, CallStatus.failed):
            result["reason"] = self.stop_reason
        return result
