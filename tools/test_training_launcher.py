import importlib.util
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('training_launcher', Path(__file__).parent / 'windows-training/training-control.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_start_fails_before_supervisor_when_stand_missing(monkeypatch, capsys):
    monkeypatch.setattr(launcher.sys, 'argv', ['training-control.py', 'start'])
    monkeypatch.setattr(launcher, 'preflight', lambda: ['Missing training stand'])
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Unexpected process')))
    assert launcher.main() == 2
    assert 'SIP was not started' in capsys.readouterr().out


def test_preflight_accepts_utf16_wsl_listing(monkeypatch, tmp_path):
    phone = tmp_path / 'MicroSIP.exe'
    phone.touch()
    phone.with_suffix('.ini').touch()
    monkeypatch.setattr(launcher, 'PHONE', phone)
    monkeypatch.setattr(launcher.shutil, 'which', lambda _: 'wsl.exe')
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='Ubuntu-24.04\r\n'.encode('utf-16-le')))
    assert launcher.preflight() == []
