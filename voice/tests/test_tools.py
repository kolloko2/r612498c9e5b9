from pathlib import Path
import json

import numpy as np
import pytest

from tools.analyze_spike import analyze
from tools.render_asterisk_config import render
from tools.replay_outbox import acknowledged, record_ack


def test_spike_diagnostic_detects_intentionally_injected_self_loop():
    time = np.arange(80000) / 16000
    caller = 0.15 * np.sin(2 * np.pi * 997 * time)
    operator = 0.1 * np.sin(2 * np.pi * 220 * time)
    clean = analyze(operator, caller)
    leaky = analyze(operator + caller * 0.5, caller)
    assert clean["probe_present"] and clean["input_energy_present"]
    assert not clean["suspected_leak"]
    assert leaky["suspected_leak"]
    assert leaky["result"] == "requires_real_phone_listening"
    assert json.loads(json.dumps(clean))["probe_present"] is True


def test_config_render_uses_explicit_secrets_and_does_not_overwrite(tmp_path):
    source = Path(__file__).resolve().parents[1] / "asterisk"
    env = {"ASTERISK_SIP_BIND": "0.0.0.0", "ASTERISK_HTTP_BIND": "127.0.0.1",
           "SIP201_PASSWORD": "test-only", "ARI_USERNAME": "voice", "ARI_PASSWORD": "test-only",
           "VOICE_MEDIA_URL": "ws://voice:8001/media", "MEDIA_USERNAME": "test", "MEDIA_PASSWORD": "test-only"}
    assert len(render(source, tmp_path / "configs", env)) == 6
    for file in (tmp_path / "configs").iterdir():
        assert "${" not in file.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        render(source, tmp_path / "configs", env)
    with pytest.raises(ValueError):
        render(source, tmp_path / "missing", {**env, "ARI_PASSWORD": ""})


def test_replay_ack_sidecar_is_durable_and_filterable(tmp_path):
    journal = tmp_path / "call.jsonl"
    event_id = __import__("uuid").uuid4()
    record_ack(journal, event_id)
    assert acknowledged(journal) == {event_id}


def test_sherpa_provider_selected_without_vosk():
    """На Windows Vosk не открывает кириллический путь; sherpa работает один."""
    from app.audio.stt import SherpaSTT, make_stt

    class Settings:
        stt_provider = 'sherpa'
        stt_model = '/models/vosk-small'
        stt_final_model = '/models/gigaam'

    provider = make_stt(Settings())
    assert isinstance(provider, SherpaSTT)
    assert provider.model == '/models/gigaam'

    # Без отдельной точной модели используется основной путь.
    Settings.stt_final_model = ''
    assert make_stt(Settings()).model == '/models/vosk-small'


def test_ready_handshake_accepts_crlf():
    """Воркер на Windows печатает READY\r\n; рукопожатие не должно падать."""
    import asyncio
    from app.audio.stt import VoskSTT

    class Stdout:
        def __init__(self, line): self.line = line
        async def readline(self): return self.line

    class Process:
        def __init__(self, line): self.stdout = Stdout(line)

    async def check(line):
        stt = VoskSTT('/models/any')
        async def spawn(kind, model): return Process(line)
        import app.audio.stt as module
        original = module.spawn_worker
        module.spawn_worker = spawn
        try:
            await stt.open_stream('call', 16000)
            return True
        except RuntimeError:
            return False
        finally:
            module.spawn_worker = original

    assert asyncio.run(check(b'READY\r\n')) is True
    assert asyncio.run(check(b'READY\n')) is True
    assert asyncio.run(check(b'BROKEN\n')) is False
