class BridgeManager:
    def __init__(self, ari):
        self.ari = ari

    async def create(self, bridge_id, channels):
        # proxy_media prevents native/direct bridging from bypassing audiohooks.
        await self.ari.request("POST", f"bridges/{bridge_id}",
                               params={"type": "mixing,proxy_media"})
        await self.ari.request("POST", f"bridges/{bridge_id}/addChannel",
                               params={"channel": ",".join(channels)})
