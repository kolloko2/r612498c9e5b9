"""Voice errors reach the student as actionable messages."""
import httpx
import pytest
from fastapi import HTTPException

import voice_client


@pytest.mark.asyncio
async def test_busy_extension_is_reported_as_busy_line(monkeypatch):
    monkeypatch.setenv('VOICE_API_TOKEN', 'token')
    monkeypatch.setenv('VOICE_URL', 'http://voice:8001')

    def handler(request):
        return httpx.Response(429, json={'detail': 'Training extension already has an active call'})
    real = httpx.AsyncClient
    monkeypatch.setattr(voice_client.httpx, 'AsyncClient',
                        lambda **kwargs: real(transport=httpx.MockTransport(handler)))
    with pytest.raises(HTTPException) as error:
        await voice_client.request('calls', 'POST', {})
    assert error.value.status_code == 409
    assert 'занят' in error.value.detail
