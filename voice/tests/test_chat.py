import asyncio
from uuid import uuid4
import numpy as np
from app.asterisk.call_manager import CallManager
from app.config import Settings
from app.domain.messages import CreateCall, CallerReply
from app.audio.formats import pcm16, SILENCE

async def until(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.01)

async def test_manual_transcript_completion_stop_and_persistence(tmp_path):
    cfg = Settings(_env_file=None, pipeline_mode='conversation', recording_dir=tmp_path/'rec', outbox_dir=tmp_path/'out')
    manager = CallManager(cfg)
    await manager.start()
    call = await manager.create(CreateCall(session_id=uuid4(), mode='manual'))
    cid = call['call_id'];rt = manager.calls[cid]
    await rt.context.ready.wait()
    try:
        await asyncio.sleep(.1)
        assert manager.chat.get(cid)['messages'] == []
        mid = uuid4()
        # Suppress PBX completion to prove sending audio is insufficient for 'played'.
        peer = rt.peers['playback'];command = peer.command
        async def delayed(name, **params):
            if name != 'STOP_MEDIA_BUFFERING':
                await command(name, **params)
        peer.command = delayed
        await rt.accept_reply(CallerReply(reply_id=mid, text='Hello'), 'operator')
        await until(lambda: manager.chat.get(cid)['messages'][0]['status'] == 'playing')
        for frame in [pcm16(np.full(320,.2))]*12 + [SILENCE]*25:
            rt.mock_input.put_nowait(frame)
        await until(lambda: any(m['status']=='recognized' for m in manager.chat.get(cid)['messages']))
        messages = manager.chat.get(cid)['messages']
        assert len([m for m in messages if m['role']=='me']) == 1
        assert messages[0]['status'] == 'playing'
        assert not any(m['role']=='bot' for m in messages)
        for done in peer.completions.values():done.set()
        await until(lambda: manager.chat.get(cid)['messages'][0]['status']=='played')
        await rt.accept_reply(CallerReply(reply_id=mid,text='Hello'), 'operator')
        assert len(manager.chat.get(cid)['messages'])==2
        second = uuid4()
        await rt.accept_reply(CallerReply(reply_id=second,text='Long reply '*20), 'operator')
        await until(lambda: manager.chat.get(cid)['messages'][-1]['status']=='playing')
        await rt.stop_playback()
        await until(lambda: manager.chat.get(cid)['messages'][-1]['status']=='interrupted')
        await manager.hangup(cid)
        assert manager.chat.get(cid)['status']=='ended'
    finally:
        await manager.close()
    restarted = CallManager(cfg)
    assert restarted.chat.get(cid)['messages'][0]['status']=='played'
    assert len(restarted.chat.get(cid)['messages'])==3
