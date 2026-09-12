"""Make one authorized training call and collect its recording metadata."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx


async def main(base, output):
    token = os.environ.get("API_TOKEN", "")
    headers = {"Authorization": "Bearer " + token} if token else {}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=65) as client:
        health = (await client.get("/api/v1/health")).json()
        if health.get("pipeline_mode") != "spike":
            raise RuntimeError("Set PIPELINE_MODE=spike before testing topology")
        print("Answer extension 201 with a headset. Speak during the five-second tone.")
        response = await client.post("/api/v1/calls", json={"session_id": str(uuid4()), "extension": "201"})
        response.raise_for_status()
        call_id = response.json()["call_id"]
        try:
            async with asyncio.timeout(60):
                while True:
                    state = (await client.get(f"/api/v1/calls/{call_id}")).json()
                    if state["status"] == "active":
                        break
                    if state["status"] in ("ended", "failed"):
                        raise RuntimeError(f"Call failed before media became ready: {state['status']}")
                    await asyncio.sleep(0.2)
            await asyncio.sleep(7)
        finally:
            response = await client.post(f"/api/v1/calls/{call_id}/hangup")
            response.raise_for_status()
            output.mkdir(parents=True, exist_ok=True)
            result = response.json() | {"telephony_mode": health["telephony_mode"],
                                       "live_topology_verified": False,
                                       "next": "Analyze WAV and confirm audible probe + no self-loop manually"}
            (output / f"{call_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--out", type=Path, default=Path("spike-results"))
    args = parser.parse_args()
    asyncio.run(main(args.base, args.out))
