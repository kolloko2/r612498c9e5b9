"""Exercise HTTP -> mock phone -> VAD/STT -> mock Backend -> TTS -> recording."""
import argparse
import asyncio
import json
import os
from uuid import uuid4

import httpx
import numpy as np


async def main(base):
    headers = {"Authorization": "Bearer " + os.environ["API_TOKEN"]} if os.getenv("API_TOKEN") else {}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=65) as client:
        health = (await client.get("/api/v1/health")).json()
        if health.get("telephony_mode") != "mock" or health.get("pipeline_mode") != "conversation":
            raise RuntimeError("Demo requires TELEPHONY_MODE=mock and PIPELINE_MODE=conversation")
        response = await client.post("/api/v1/calls", json={"session_id": str(uuid4()), "extension": "201"})
        response.raise_for_status()
        call_id = response.json()["call_id"]
        try:
            async with asyncio.timeout(10):
                while (await client.get(f"/api/v1/calls/{call_id}")).json()["status"] != "active":
                    await asyncio.sleep(0.1)
            t = np.arange(16000) / 16000
            pcm = (0.2 * np.sin(2 * np.pi * 220 * t) * 32768).astype("<i2").tobytes()
            response = await client.post(f"/api/v1/calls/{call_id}/mock/audio", content=pcm)
            response.raise_for_status()
            await asyncio.sleep(3)
        finally:
            response = await client.post(f"/api/v1/calls/{call_id}/hangup")
            response.raise_for_status()
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    asyncio.run(main(parser.parse_args().base))
