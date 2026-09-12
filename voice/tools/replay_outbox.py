"""Explicit event replay only; never originates calls. Backend must deduplicate event_id."""
import argparse
import asyncio
import os
from pathlib import Path

from websockets.asyncio.client import connect

from app.domain.messages import EventEnvelope


async def main(path, url):
    events = [EventEnvelope.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if not events or len({event.session_id for event in events}) != 1:
        raise ValueError("Journal must contain exactly one session")
    token = os.environ.get("BACKEND_TOKEN", "")
    headers = {"Authorization": "Bearer " + token} if token else {}
    async with connect(url.format(session_id=events[0].session_id), additional_headers=headers) as ws:
        for event in events:
            await ws.send(event.model_dump_json())
    print(f"Sent {len(events)} events. Delivery to the socket is not a Backend application ACK.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("journal", type=Path)
    parser.add_argument("--url", default=os.environ.get("BACKEND_URL", "ws://backend:8000/ws/v1/voice/sessions/{session_id}"))
    args = parser.parse_args()
    asyncio.run(main(args.journal, args.url))
