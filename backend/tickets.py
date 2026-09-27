"""Teacher-facing catalog of the supplied training tickets.

The catalog is a read-only import of `Датасет.zip` → `Билеты- задачи по C 112`
produced by `tools/import_tickets.py`. Nothing here contacts a model: drafts and
their reference rubrics are deterministic transformations of the transcribed
booklet, so the same ticket always previews identically.

Publishing copies a draft into the teacher's own scenario catalog under a fresh
identifier, exactly like an approved AI draft. It is the teacher's validation
step required by the task statement: a ticket is never issued to a student
straight from the booklet, and the imported wording stays editable afterwards.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from evaluation import Rubric

CATALOG = Path(__file__).with_name("data") / "tickets.json"
MAX_PUBLISH = 3  # Билет содержит ровно три вызова.


def load_catalog(path: Path = CATALOG) -> dict:
    """Read the imported catalog; an absent file disables the feature cleanly."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"source": {}, "drafts": [], "metadata": {"tickets": 0, "drafts": 0}}


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    draft_ids: list[str] = Field(min_length=1, max_length=MAX_PUBLISH)
    new_revision: bool = False


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def router(store, accounts, authorize, Scenario, catalog=None):
    api = APIRouter(prefix="/api/v1/instructor/tickets", dependencies=[Depends(authorize)])
    teacher = accounts.require("teacher")
    catalog = catalog if catalog is not None else load_catalog()
    drafts = {item["id"]: item for item in catalog.get("drafts", [])}
    with store.db:
        store.db.execute(
            """CREATE TABLE IF NOT EXISTS ticket_publications (
                   draft_id TEXT NOT NULL, teacher_id TEXT NOT NULL,
                   scenario_id TEXT NOT NULL, published_at TEXT NOT NULL,
                   PRIMARY KEY (draft_id, teacher_id))"""
        )

    def published_map(teacher_id: str) -> dict[str, str]:
        rows = store.db.execute(
            "SELECT draft_id, scenario_id FROM ticket_publications WHERE teacher_id=?",
            (teacher_id,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def summary(item: dict, published: dict[str, str]) -> dict:
        scenario = item["scenario"]
        return {
            "id": item["id"], "call": item["call"], "title": scenario["title"],
            "category_id": scenario["category_id"], "difficulty": scenario["difficulty"],
            "dds_profile": scenario["dds_profile"],
            "published_scenario_id": published.get(item["id"]),
        }

    @api.get("")
    async def listing(user=Depends(teacher)):
        published = published_map(user["id"])
        tickets: dict[int, dict] = {}
        for item in catalog.get("drafts", []):
            entry = tickets.setdefault(item["ticket"], {
                "number": item["ticket"], "page": item["source_page"], "calls": []})
            entry["calls"].append(summary(item, published))
        return {
            "source": catalog.get("source", {}),
            "phones": catalog.get("phones", "synthetic"),
            "metadata": catalog.get("metadata", {}),
            "tickets": [tickets[key] for key in sorted(tickets)],
        }

    @api.get("/{number}")
    async def detail(number: int, user=Depends(teacher)):
        items = [item for item in catalog.get("drafts", []) if item["ticket"] == number]
        if not items:
            raise HTTPException(404, "Билет не найден")
        published = published_map(user["id"])
        return {
            "number": number, "page": items[0]["source_page"],
            "source": catalog.get("source", {}),
            "calls": [{
                **summary(item, published),
                "classified_by": item["classified_by"],
                "scenario": item["scenario"], "rubric": item["rubric"],
            } for item in items],
        }

    @api.post("/publish", status_code=201)
    async def publish(body: PublishRequest, user=Depends(teacher)):
        unknown = [key for key in body.draft_ids if key not in drafts]
        if unknown:
            raise HTTPException(404, "Заготовка не найдена: " + ", ".join(sorted(unknown)))
        if len(set(body.draft_ids)) != len(body.draft_ids):
            raise HTTPException(422, "Заготовки в запросе повторяются")

        existing = published_map(user["id"])
        created: list[dict] = []
        with store.db:
            for key in body.draft_ids:
                item = drafts[key]
                same = False
                if key in existing and body.new_revision:
                    previous = store.scenario(existing[key])
                    expected = Scenario.model_validate({**item['scenario'], 'id': existing[key], 'enabled': True}).model_dump()
                    rubric_row = store.db.execute('SELECT body FROM rubrics WHERE scenario_id=?',
                        (f"{user['id']}:{existing[key]}",)).fetchone()
                    same = previous == expected and bool(rubric_row) and json.loads(rubric_row[0]) == Rubric.model_validate(item['rubric']).model_dump()
                if key in existing and (not body.new_revision or same):
                    # Повторная публикация не создаёт второй сценарий и не
                    # затирает правки преподавателя в уже опубликованном.
                    created.append({"draft_id": key, "scenario_id": existing[key], "created": False})
                    continue
                scenario_id = f"{key}-{uuid4().hex[:8]}"
                scenario = Scenario.model_validate({**item["scenario"], "id": scenario_id,
                                                    "enabled": True}).model_dump()
                rubric = Rubric.model_validate(item["rubric"]).model_dump()
                rubric_key = f"{user['id']}:{scenario_id}"
                encoded = json.dumps(rubric, ensure_ascii=False)
                now = timestamp()
                store.db.execute("INSERT INTO scenarios VALUES (?,?)",
                                 (scenario_id, json.dumps(scenario, ensure_ascii=False)))
                store.db.execute("INSERT INTO scenario_owners VALUES (?,?)", (scenario_id, user["id"]))
                store.db.execute("INSERT INTO rubrics VALUES (?,?,?)", (rubric_key, 1, encoded))
                store.db.execute("INSERT INTO rubric_history VALUES (?,?,?,?)",
                                 (rubric_key, 1, now, encoded))
                store.db.execute("INSERT INTO ticket_publications VALUES (?,?,?,?) ON CONFLICT (draft_id, teacher_id) DO UPDATE SET scenario_id=excluded.scenario_id, published_at=excluded.published_at",
                                 (key, user["id"], scenario_id, now))
                created.append({"draft_id": key, "scenario_id": scenario_id, "created": True})
        return {"published": created}

    return api
