"""TLS client helpers that add the private CA without replacing OS roots."""

import ssl
from urllib.parse import urlsplit


def httpx_verify(url: str, ca_file):
    if urlsplit(url).scheme.lower() != "https" or not ca_file:
        return True
    return client_context(ca_file)


def websocket_ssl_kwargs(url: str, ca_file) -> dict:
    scheme = urlsplit(url).scheme.lower()
    if scheme == "ws":
        return {}
    if scheme != "wss":
        raise ValueError("WebSocket URL must use ws or wss")
    return {"ssl": client_context(ca_file) if ca_file else ssl.create_default_context()}


def client_context(ca_file):
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(ca_file))
    return context
