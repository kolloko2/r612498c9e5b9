import asyncio

import numpy as np

from .formats import FRAME_SAMPLES, SAMPLE_RATE, pcm16, FramePacer


async def run_spike(peer):
    """Five seconds of known 997Hz PCM. No STT or TTS is instantiated."""
    correlation = "spike-tone"
    done = peer.expect_completion(correlation)
    pacer = FramePacer()
    try:
        await peer.command("START_MEDIA_BUFFERING")
        for index in range(250):
            t = (np.arange(FRAME_SAMPLES) + index * FRAME_SAMPLES) / SAMPLE_RATE
            await peer.send(pcm16(0.15 * np.sin(2 * np.pi * 997 * t)))
            # Limit application lead to one frame for interruption and recordings.
            # Asterisk still handles media framing and actual phone timing.
            await pacer.tick()
        await peer.command("STOP_MEDIA_BUFFERING", correlation_id=correlation)
        await asyncio.wait_for(done.wait(), 10)
    finally:
        peer.forget_completion(correlation)
