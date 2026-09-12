import asyncio
import base64
import json
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect


class ARIClient:
    """One ARI event WS plus HTTP control; never used for binary media."""

    def __init__(self, settings, on_event, on_disconnect):
        self.settings, self.on_event, self.on_disconnect = settings, on_event, on_disconnect
        self.http = httpx.AsyncClient(
            base_url=settings.ari_url.rstrip("/") + "/",
            auth=(settings.ari_username, settings.ari_password.get_secret_value()),
            timeout=10,
        )
        self.ready = asyncio.Event()
        self.task = None

    async def request(self, method, path, *, params=None, json_body=None):
        response = await self.http.request(method, path.lstrip("/"), params=params, json=json_body)
        response.raise_for_status()
        return response.json() if response.content else None

    async def delete(self, path):
        try:
            await self.request("DELETE", path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

    async def start(self):
        info = await self.request("GET", "asterisk/info")
        version = info["system"]["version"]
        parts = version.split(".")
        if int(parts[0]) != 22 or int(parts[1]) < 8:
            raise RuntimeError(f"Expected Asterisk 22.8+ in the 22.x branch, got {version}")
        self.task = asyncio.create_task(self._events(), name="ari-events")
        await asyncio.wait_for(self.ready.wait(), 15)

    async def _events(self):
        cfg = self.settings
        url = cfg.ari_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        url = url.rstrip("/") + "/events?" + urlencode({"app": cfg.ari_app})
        credentials = base64.b64encode(
            f"{cfg.ari_username}:{cfg.ari_password.get_secret_value()}".encode()).decode()
        try:
            async with connect(url, additional_headers={"Authorization": f"Basic {credentials}"},
                               max_size=1024 * 1024, open_timeout=10) as ws:
                self.ready.set()
                async for message in ws:
                    await self.on_event(json.loads(message))
        except asyncio.CancelledError:
            raise
        finally:
            self.ready.clear()
            await self.on_disconnect()

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.http.aclose()
