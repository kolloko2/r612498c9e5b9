from uuid import uuid4

import pytest

from app.asterisk.topology import Topology
from app.config import Settings


class FakeARI:
    def __init__(self, fail_at=None):
        self.requests = []
        self.deleted = []
        self.waited = []
        self.forgotten = []
        self.fail_at = fail_at

    async def request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        if len(self.requests) == self.fail_at:
            raise RuntimeError("Injected ARI failure")
        return {}

    async def delete(self, path):
        self.deleted.append(path)

    async def wait_channel(self, channel_id, timeout):
        self.waited.append((channel_id, timeout))

    def forget_channel(self, channel_id):
        self.forgotten.append(channel_id)


async def test_directional_topology_has_no_shared_capture_bridge():
    ari = FakeARI()
    topology = Topology(ari, Settings(_env_file=None), uuid4())
    await topology.originate("201")
    await topology.build()
    snoops = [r for r in ari.requests if "/snoop/" in r[1]]
    assert {r[2]["params"]["spy"] for r in snoops} == {"in", "out"}
    assert all(r[2]["params"]["whisper"] == "none" for r in snoops)
    media = [r[2]["params"] for r in ari.requests if r[1] == "channels/externalMedia"]
    assert len(media) == 3
    assert all(m["format"] == "slin16" and m["transport"] == "websocket"
               and m["encapsulation"] == "none" for m in media)
    assert [m["transport_data"] for m in media].count("f(json)d(in)") == 2
    bridges = {r[1]: r[2]["params"]["channel"].split(",")
               for r in ari.requests if r[1].endswith("/addChannel")}
    capture = bridges[f"bridges/{topology.bridges['capture']}/addChannel"]
    assert topology.phone not in capture
    assert topology.media["playback"] not in capture
    assert capture == [topology.snoops["capture"], topology.media["capture"]]
    assert [channel_id for channel_id, _ in ari.waited] == [
        topology.snoops["capture"], topology.snoops["monitor"],
        topology.media["capture"], topology.media["playback"], topology.media["monitor"],
    ]


async def test_partial_topology_failure_can_be_cleaned():
    ari = FakeARI(fail_at=4)
    topology = Topology(ari, Settings(_env_file=None), uuid4())
    with pytest.raises(RuntimeError):
        await topology.build()
    await topology.close()
    assert set(ari.deleted) == ({f"channels/{c}" for c in topology.channel_ids}
                                | {f"bridges/{b}" for b in topology.bridges.values()})
    assert set(ari.forgotten) == set(topology.channel_ids)
