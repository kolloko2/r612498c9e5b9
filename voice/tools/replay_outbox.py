"""Explicit ACK-aware replay only; never originates calls."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID

from websockets.asyncio.client import connect

from app.domain.messages import EventEnvelope


def acknowledged(path):
    ack_path = path.with_suffix(".acked.jsonl")
    if not ack_path.exists():
        return set()
    return {UUID(str(json.loads(line)["event_id"]))
            for line in ack_path.read_text(encoding="utf-8").splitlines() if line.strip()}


def record_ack(path, event_id):
    with open(path.with_suffix(".acked.jsonl"), "a", encoding="utf-8") as file:
        file.write(json.dumps({"event_id": str(event_id)}, separators=(",", ":")) + "\n")
        file.flush()
        os.fsync(file.fileno())


async def main(path, url):
    events = [EventEnvelope.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if not events or len({event.session_id for event in events}) != 1:
        raise ValueError("Journal must contain exactly one session")
    token = os.environ.get("BACKEND_TOKEN", "")
    headers = {"Authorization": "Bearer " + token} if token else {}
    already_acked = acknowledged(path)
    events = [event for event in events if event.event_id not in already_acked]
    if not events:
        print("No unacknowledged events to replay.")
        return
    async with connect(url.format(session_id=events[0].session_id), additional_headers=headers) as ws:
        for event in events:
            await ws.send(event.model_dump_json())
            while True:
                reply = EventEnvelope.model_validate_json(await ws.recv())
                if (reply.type == "backend.ack" and
                        UUID(str(reply.payload.get("event_id"))) == event.event_id):
                    record_ack(path, event.event_id)
                    break
    print(f"Backend acknowledged {len(events)} replayed events.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("journal", type=Path)
    parser.add_argument("--url", default=os.environ.get("BACKEND_URL", "ws://backend:8000/ws/v1/voice/sessions/{session_id}"))
    args = parser.parse_args()
    asyncio.run(main(args.journal, args.url))
