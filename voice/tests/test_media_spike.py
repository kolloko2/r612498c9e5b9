import asyncio
import json
import wave
from uuid import uuid4

import numpy as np
import pytest

from app.asterisk.media_ws import MediaPeer, MockPeer
from app.audio.formats import FRAME_BYTES, SILENCE, Framer
from app.audio.recorder import Recorder
from starlette.websockets import WebSocketDisconnect, WebSocketState


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def send_bytes(self, data):
        self.sent.append(data)


async def test_flow_control_does_not_block_flush():
    ws = FakeWS()
    peer = MediaPeer(ws, "playback")
    peer.control({"event": "MEDIA_XOFF"})
    send = asyncio.create_task(peer.send(SILENCE))
    await asyncio.sleep(0)
    assert not send.done()
    send.cancel()
    await asyncio.gather(send, return_exceptions=True)
    await peer.flush()
    assert ws.sent == [{"command": "FLUSH_MEDIA"}]
    assert peer.writable.is_set()


async def test_capture_socket_cannot_send_tts():
    with pytest.raises(RuntimeError):
        await MediaPeer(FakeWS(), "capture").send(SILENCE)


async def test_mock_output_is_monitored():
    monitor = []
    peer = MockPeer(monitor.append)
    tone = np.full(320, 5000, dtype="<i2").tobytes()
    await peer.send(tone)
    await asyncio.sleep(0.03)
    await peer.close()
    assert tone in monitor


async def test_recording_finalization_tracks_and_clipped_mix(tmp_path):
    rec = Recorder(tmp_path, uuid4(), uuid4())
    await rec.start()
    sample = np.full(320, 20000, dtype="<i2").tobytes()
    rec.put("operator", sample)
    rec.put("caller", sample)
    paths = await rec.finalize()
    assert not list(rec.directory.glob("*.tmp"))
    for name, path in paths.items():
        with wave.open(path, "rb") as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
            assert wav.getnframes() == 320
            values = np.frombuffer(wav.readframes(320), "<i2")
            assert np.all(values == (32767 if name == "mixed" else 20000))


def test_framer_handles_arbitrary_provider_chunks():
    framer = Framer()
    assert list(framer.feed(bytes(300))) == []
    assert len(list(framer.feed(bytes(1000)))) == 2
    assert len(framer.finish()) == FRAME_BYTES


async def test_ari_hangup_racing_websocket_close_is_idempotent():
    class ClosedByARI(FakeWS):
        client_state = WebSocketState.CONNECTED
        application_state = WebSocketState.CONNECTED

        async def close(self):
            raise WebSocketDisconnect(1000)

    peer = MediaPeer(ClosedByARI(), 'capture')
    await peer.close()
    await peer.close()
    assert peer.closed
