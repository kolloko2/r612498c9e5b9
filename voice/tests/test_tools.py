from pathlib import Path
import json

import numpy as np
import pytest

from tools.analyze_spike import analyze
from tools.render_asterisk_config import render


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
