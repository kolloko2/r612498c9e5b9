"""Opt-in trust for private HTTPS service endpoints."""

import os
import ssl
from urllib.parse import urlsplit


def httpx_verify(url: str):
    """Return HTTPX verification config without changing plain HTTP behavior."""
    if urlsplit(url).scheme.lower() != "https":
        return True
    ca_file = os.getenv("INTERNAL_CA_FILE", "").strip()
    if not ca_file:
        return True
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=ca_file)
    return context
