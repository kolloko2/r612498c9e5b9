import ssl

from app.tls import httpx_verify, websocket_ssl_kwargs


class FakeContext:
    def __init__(self):
        self.loaded = []

    def load_verify_locations(self, *, cafile):
        self.loaded.append(cafile)


def test_https_and_wss_extend_default_trust(monkeypatch):
    contexts = []

    def create():
        context = FakeContext()
        contexts.append(context)
        return context

    monkeypatch.setattr(ssl, "create_default_context", create)
    assert httpx_verify("https://asterisk:8089/ari", "/run/tls/ca.cert.pem") is contexts[0]
    kwargs = websocket_ssl_kwargs("wss://backend:8000/ws/v1/test", "/run/tls/ca.cert.pem")
    assert kwargs == {"ssl": contexts[1]}
    assert [item.loaded for item in contexts] == [
        ["/run/tls/ca.cert.pem"], ["/run/tls/ca.cert.pem"]
    ]


def test_http_and_ws_transport_remain_plain(monkeypatch):
    monkeypatch.setattr(ssl, "create_default_context", lambda: (_ for _ in ()).throw(AssertionError()))
    assert httpx_verify("http://asterisk:8088/ari", "/missing/ca.pem") is True
    assert websocket_ssl_kwargs("ws://backend:8000/ws/v1/test", "/missing/ca.pem") == {}
