import asyncio
from uuid import uuid4

from .bridge_manager import BridgeManager


class Topology:
    """phone <-> playback bridge; independent spy-in and spy-out recording taps.

    Only spy-in reaches VAD/STT. The phone never joins the capture bridges.
    Acoustic echo from a physical speaker is outside this routing guarantee.
    """

    def __init__(self, ari, settings, call_id):
        self.ari, self.settings = ari, settings
        self.phone = str(call_id)
        self.media = {role: str(uuid4()) for role in ("capture", "playback", "monitor")}
        self.snoops = {role: str(uuid4()) for role in ("capture", "monitor")}
        self.bridges = {role: str(uuid4()) for role in self.media}

    @property
    def channel_ids(self):
        return [self.phone, *self.media.values(), *self.snoops.values()]

    async def originate(self, extension):
        await self.ari.request("POST", "channels", params={
            "endpoint": f"PJSIP/{extension}", "app": self.settings.ari_app,
            "appArgs": "phone", "channelId": self.phone,
            "timeout": int(self.settings.connect_timeout_s),
        })

    async def build(self):
        cfg = self.settings
        for role, spy in (("capture", "in"), ("monitor", "out")):
            await self.ari.request("POST", f"channels/{self.phone}/snoop/{self.snoops[role]}",
                                   params={"app": cfg.ari_app, "spy": spy, "whisper": "none"})
            await self._wait_channel(self.snoops[role])
        for role, channel_id in self.media.items():
            # d() is from the application perspective, NOT the phone perspective.
            direction = "out" if role == "playback" else "in"
            await self.ari.request("POST", "channels/externalMedia", params={
                "channelId": channel_id, "app": cfg.ari_app,
                "external_host": cfg.media_connection,
                "transport": "websocket", "encapsulation": "none", "format": "slin16",
                "connection_type": "client", "direction": "both",
                "transport_data": f"f(json)d({direction})",
            })
            await self._wait_channel(channel_id)
        bridges = BridgeManager(self.ari)
        await bridges.create(self.bridges["playback"], [self.phone, self.media["playback"]])
        for role in ("capture", "monitor"):
            await bridges.create(self.bridges[role], [self.snoops[role], self.media[role]])

    async def _wait_channel(self, channel_id):
        # ARI channel creation may return before the channel has entered Stasis;
        # adding it to a bridge in that window intermittently returns HTTP 422.
        wait_channel = getattr(self.ari, "wait_channel", None)
        if wait_channel:
            await wait_channel(channel_id, min(self.settings.connect_timeout_s, 10))

    async def close(self):
        # Delete even ids from partially failed creation: ARI 404 is idempotent.
        results = await asyncio.gather(
            *(self.ari.delete(f"channels/{cid}") for cid in self.channel_ids),
            return_exceptions=True,
        )
        results += await asyncio.gather(
            *(self.ari.delete(f"bridges/{bid}") for bid in self.bridges.values()),
            return_exceptions=True,
        )
        errors = [type(r).__name__ for r in results if isinstance(r, Exception)]
        forget_channel = getattr(self.ari, "forget_channel", None)
        if forget_channel:
            for channel_id in self.channel_ids:
                forget_channel(channel_id)
        if errors:
            raise RuntimeError("ARI cleanup failed: " + ", ".join(errors))
