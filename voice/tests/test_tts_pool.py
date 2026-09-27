import asyncio
import struct
import pytest
from app.audio import tts


class Process:
    returncode = None
    def __init__(self, gate=None):
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(b'READY\r\n')
        self.stdin = self
        self.requests = []
        self.gate = gate
    def write(self, data):
        self.requests.append(data)
    async def drain(self):
        if self.gate:
            await self.gate.wait()
        self.stdout.feed_data(struct.pack('<II', 16000, 640) + bytes(640) + struct.pack('<II', 16000, 0))


@pytest.mark.asyncio
async def test_pool_parallelism_cache_and_slot_release(monkeypatch):
    spawned=[];gate=asyncio.Event()
    async def spawn(*args, **kwargs):
        process=Process(gate);spawned.append(process);return process
    async def stop(process):
        if process:process.returncode=-1
    monkeypatch.setattr(tts,'spawn_worker',spawn)
    monkeypatch.setattr(tts,'stop_process',stop)
    pool=tts.SileroPool('test',2,1)
    first=asyncio.create_task(pool.render('one','baya',1))
    second=asyncio.create_task(pool.render('two','baya',1))
    for _ in range(20):
        if len(spawned)==2 and all(p.requests for p in spawned):break
        await asyncio.sleep(.001)
    assert len(spawned)==2 and all(p.requests for p in spawned)
    gate.set()
    results=await asyncio.gather(first,second)
    assert all(results)
    assert pool.available.qsize()==2
    assert await pool.render('one','baya',1)==results[0]
    assert sum(len(p.requests) for p in spawned)==2
    await pool.close()
    assert all(p.returncode==-1 for p in spawned)


@pytest.mark.asyncio
async def test_cancel_only_kills_owned_worker(monkeypatch):
    spawned=[]
    async def spawn(*args, **kwargs):
        p=Process(asyncio.Event() if not spawned else None);spawned.append(p);return p
    async def stop(p):
        if p:p.returncode=-1
    monkeypatch.setattr(tts,'spawn_worker',spawn)
    monkeypatch.setattr(tts,'stop_process',stop)
    pool=tts.SileroPool('test',2,1)
    blocked=asyncio.create_task(pool.render('blocked','baya',1))
    while not spawned or not spawned[0].requests:await asyncio.sleep(.001)
    assert await pool.render('other','baya',1)
    blocked.cancel()
    with pytest.raises(asyncio.CancelledError):await blocked
    assert spawned[0].returncode==-1 and spawned[1].returncode is None
    assert pool.available.qsize()==2
    await pool.close()
