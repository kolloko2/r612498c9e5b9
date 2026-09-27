import asyncio
import json
import threading
from uuid import uuid4
import pytest
from app.backend.control_ws import ControlWS
from app.config import Settings
from app.domain.call import CallContext


@pytest.mark.asyncio
async def test_cancelled_emit_finishes_disk_write_before_terminal_event(tmp_path):
    cfg = Settings(_env_file=None, outbox_dir=tmp_path)
    async def on_message(event):pass
    control = ControlWS(cfg, CallContext(uuid4(), '201'), on_message, lambda *args: None)
    await control.start()
    started, release = threading.Event(), threading.Event()
    original = control._append
    def append(event):
        if event.seq == 1:
            started.set()
            assert release.wait(5)
        original(event)
    control._append = append
    task = asyncio.create_task(control.emit('caller.playback', {'status':'played'}))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    terminal = asyncio.create_task(control.emit('call.ended', {}))
    release.set()
    with pytest.raises(asyncio.CancelledError):await task
    await terminal
    rows = [json.loads(line) for line in control.journal.read_text(encoding='utf-8').splitlines()]
    assert [row['seq'] for row in rows] == [1, 2]
    assert [row['type'] for row in rows] == ['caller.playback', 'call.ended']
    await control.close()
