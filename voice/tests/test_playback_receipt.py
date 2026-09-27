from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from app.asterisk.call_manager import CallRuntime


@pytest.mark.asyncio
async def test_only_terminal_auto_playback_emits_receipt():
    runtime = object.__new__(CallRuntime)
    runtime.manager = SimpleNamespace(chat=SimpleNamespace(message=Mock()))
    runtime.context = SimpleNamespace(call_id=uuid4())
    runtime.settings = SimpleNamespace(echo_guard_ms=100)
    runtime.backend = SimpleNamespace(emit=AsyncMock())
    runtime.mode = 'auto'
    runtime.capture_blocked_until = 0
    reply = str(uuid4())
    await runtime.playback_event(reply, 'playing')
    runtime.backend.emit.assert_not_called()
    await runtime.playback_event(reply, 'played')
    runtime.backend.emit.assert_awaited_once_with('caller.playback', {'reply_id': reply, 'status': 'played'})
