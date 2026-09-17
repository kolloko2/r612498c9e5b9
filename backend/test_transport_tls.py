import ssl

from transport_tls import httpx_verify


class FakeContext:
    def __init__(self):
        self.loaded = []

    def load_verify_locations(self, *, cafile):
        self.loaded.append(cafile)


def test_internal_ca_extends_default_https_trust(monkeypatch):
    context = FakeContext()
    monkeypatch.setenv("INTERNAL_CA_FILE", "/run/tls/ca.cert.pem")
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)
    assert httpx_verify("https://voice:8001") is context
    assert context.loaded == ["/run/tls/ca.cert.pem"]


def test_plain_http_does_not_load_internal_ca(monkeypatch):
    monkeypatch.setenv("INTERNAL_CA_FILE", "/missing/ca.pem")
    monkeypatch.setattr(ssl, "create_default_context", lambda: (_ for _ in ()).throw(AssertionError()))
    assert httpx_verify("http://voice:8001") is True
