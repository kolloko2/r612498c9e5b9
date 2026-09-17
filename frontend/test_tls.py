import importlib.util
from pathlib import Path


def _load_server(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("server.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_internal_ca_extends_default_https_trust(monkeypatch):
    monkeypatch.setenv("INTERNAL_CA_FILE", "/run/tls/ca.cert.pem")
    module = _load_server("tls_frontend")

    class Context:
        loaded = []

        def load_verify_locations(self, *, cafile):
            self.loaded.append(cafile)

    context = Context()
    monkeypatch.setattr(module.ssl, "create_default_context", lambda: context)
    assert module.internal_http_verify("https://backend:8000") is context
    assert context.loaded == ["/run/tls/ca.cert.pem"]


def test_plain_http_ignores_internal_ca(monkeypatch):
    monkeypatch.setenv("INTERNAL_CA_FILE", "/missing/ca.pem")
    module = _load_server("plain_frontend")
    monkeypatch.setattr(module.ssl, "create_default_context", lambda: (_ for _ in ()).throw(AssertionError()))
    assert module.internal_http_verify("http://backend:8000") is True
