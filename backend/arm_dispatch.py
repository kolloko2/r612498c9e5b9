"""Bounded, synthetic VIS delivery and profile-scoped DDS incoming journal.

This module does not contact an external information system or an emergency
service.  It snapshots an already saved, assigned training card into a separate
journal so that the sending student and owning teacher can rehearse delivery and
receipt.  DDS profiles are curriculum metadata, not account roles or authority.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from curriculum import PROFILES


PROFILE_TITLES = {item["id"]: item["title"] for item in PROFILES}
ProfileId = Annotated[str, StringConstraints(pattern="^(general|fire|police|medical|gas|utilities)$")]
RecipientName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CreateDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: UUID
    informational_recipients: list[RecipientName] = Field(default_factory=list, max_length=20)


class OpenDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: ProfileId


class RecipientSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    recipients: list[RecipientName] = Field(default_factory=list,max_length=20)


def router(store, accounts, authorize) -> APIRouter:
    """Build the isolated ARM router; application assembly is intentionally external."""

    store.db.execute(
        "CREATE TABLE IF NOT EXISTS arm_vis_deliveries ("
        "id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL, "
        "student_id TEXT NOT NULL, teacher_id TEXT NOT NULL, assignment_id TEXT NOT NULL, "
        "group_id TEXT NOT NULL, target_profile TEXT NOT NULL, sent_at TEXT NOT NULL, "
        "opened_at TEXT, body TEXT NOT NULL, UNIQUE(session_id,message_id))"
    )
    store.db.execute(
        "CREATE TABLE IF NOT EXISTS arm_vis_audit ("
        "id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL, at TEXT NOT NULL, event TEXT NOT NULL, "
        "actor_id TEXT NOT NULL, actor_role TEXT NOT NULL, detail TEXT NOT NULL)"
    )
    store.db.commit()
    store.db.execute('CREATE TABLE IF NOT EXISTS arm_recipient_settings (teacher_id TEXT NOT NULL, profile TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(teacher_id,profile))')
    store.db.commit()
    mutation_lock = threading.RLock()

    combined = APIRouter()
    dependencies = [Depends(authorize)] if authorize else []
    student_api = APIRouter(prefix="/api/v1/student", dependencies=dependencies)
    instructor = APIRouter(prefix="/api/v1/instructor/dds", dependencies=dependencies)

    def current(x_user_session: str = Header("", alias="X-User-Session")) -> dict:
        return accounts.current(x_user_session)

    def require(role: str):
        def dependency(user: dict = Depends(current)) -> dict:
            if not user.get("active") or user.get("role") != role:
                raise HTTPException(403, "Недостаточно прав")
            return user

        return dependency

    student = require("student")
    teacher = require("teacher")

    def workspace(session_id: UUID | str) -> dict:
        row = store.db.execute("SELECT body FROM workspace WHERE id=?", (str(session_id),)).fetchone()
        if not row:
            raise HTTPException(404, "Занятие не найдено")
        return json.loads(row[0])

    def audit(delivery_id: str, event: str, user: dict, detail: dict | None = None) -> None:
        store.db.execute(
            "INSERT INTO arm_vis_audit (id,delivery_id,at,event,actor_id,actor_role,detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(uuid4()), delivery_id, now(), event, user["id"], user["role"],
             json.dumps(detail or {}, ensure_ascii=False)),
        )

    def decode(row, *, reveal_recipients: bool) -> dict:
        value = json.loads(row[10])
        informational = value.pop("informational_recipients", [])
        result = {
            "id": row[0], "message_id": row[1], "session_id": row[2], "student_id": row[3],
            "target_profile": row[7], "profile_title": PROFILE_TITLES[row[7]],
            "sent_at": row[8], "opened_at": row[9], "state": "opened" if row[9] else "delivered",
            "simulated": True, "card": value,
            "informational_recipient_count": len(informational),
            "informational_recipients_source": "teacher_training_configuration",
        }
        if reveal_recipients:
            result["informational_recipients"] = informational
        return result

    def delivery_row(delivery_id: UUID | str):
        return store.db.execute(
            "SELECT id,message_id,session_id,student_id,teacher_id,assignment_id,group_id,"
            "target_profile,sent_at,opened_at,body FROM arm_vis_deliveries WHERE id=?",
            (str(delivery_id),),
        ).fetchone()

    def snapshot(value: dict, informational_recipients: list[str]) -> dict:
        card = value.get("card") or {}
        return {
            "number": value.get("number"),
            "title": value.get("title", ""),
            "created_at": value.get("created_at"),
            "registered_at": value.get("registered_at"),
            "status": value.get("status"),
            "difficulty": value.get("difficulty", "basic"),
            "dds_profile": value.get("dds_profile", "general"),
            "learning_objectives": value.get("learning_objectives", ""),
            "registration": value.get("registration", {}),
            "caller_name": card.get("caller_name", ""),
            "phone": card.get("phone", ""),
            "region": card.get("region", ""),
            "city": card.get("city", ""),
            "district": card.get("district", ""),
            "area": card.get("area", ""),
            "street": card.get("street", ""),
            "house": card.get("house", ""),
            "building": card.get("building", ""),
            "structure": card.get("structure", ""),
            "apartment": card.get("apartment", ""),
            "address_note": card.get("address_note", ""),
            "description": card.get("description", ""),
            "incident_type": card.get("incident_type", ""),
            "classifier_features": card.get("classifier_features", []),
            "visible_response_services": card.get("services", []),
            # These labels are deliberately kept outside the response-service strip.
            "informational_recipients": informational_recipients,
        }

    @student_api.post("/sessions/{session_id}/vis-deliveries", status_code=201)
    async def create_delivery(session_id: UUID, body: CreateDelivery, user=Depends(student)):
        value = workspace(session_id)
        if value.get("student_id") != user["id"]:
            raise HTTPException(404, "Занятие не найдено")
        if not all(value.get(key) for key in ("teacher_id", "assignment_id", "group_id")):
            raise HTTPException(409, "VIS-доставка доступна только в назначеном учебном занятии")
        if value.get("revision", 0) < 1 or not value.get("registered_at"):
            raise HTTPException(409, "Сначала сохраните карточку")
        profile = value.get("dds_profile", "general")
        if profile not in PROFILE_TITLES:
            raise HTTPException(409, "Профиль ДДС в снимке занятия не поддерживается")
        if body.informational_recipients:
            raise HTTPException(403,'Информационных получателей настраивает преподаватель')
        settings=store.db.execute('SELECT body FROM arm_recipient_settings WHERE teacher_id=? AND profile=?',(value['teacher_id'],profile)).fetchone()
        recipients=json.loads(settings[0]) if settings else []
        visible = set((value.get("card") or {}).get("services", []))
        if any(name in visible for name in recipients):
            raise HTTPException(422, "Информационный получатель не должен дублировать службу реагирования")
        encoded = json.dumps(snapshot(value, recipients), ensure_ascii=False)
        message_id = str(body.message_id)
        with mutation_lock:
            existing = store.db.execute(
                "SELECT id,message_id,session_id,student_id,teacher_id,assignment_id,group_id,"
                "target_profile,sent_at,opened_at,body FROM arm_vis_deliveries "
                "WHERE session_id=? AND message_id=?", (str(session_id), message_id),
            ).fetchone()
            if existing:
                return decode(existing, reveal_recipients=False)
            count = store.db.execute(
                "SELECT count(*) FROM arm_vis_deliveries WHERE session_id=?", (str(session_id),)
            ).fetchone()[0]
            if count >= 20:
                raise HTTPException(409, "Для одной карточки допускается не более 20 учебных доставок")
            delivery_id, sent_at = str(uuid4()), now()
            with store.db:
                store.db.execute(
                    "INSERT INTO arm_vis_deliveries "
                    "(id,message_id,session_id,student_id,teacher_id,assignment_id,group_id,"
                    "target_profile,sent_at,opened_at,body) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (delivery_id, message_id, str(session_id), user["id"], value["teacher_id"],
                     value["assignment_id"], value["group_id"], profile, sent_at, None, encoded),
                )
                audit(delivery_id, "vis.delivery.simulated", user, {
                    "session_id": str(session_id), "profile": profile,
                    "informational_recipient_count": len(recipients),
                })
            return decode(delivery_row(delivery_id), reveal_recipients=False)

    @student_api.get("/sessions/{session_id}/vis-deliveries")
    async def student_deliveries(session_id: UUID, user=Depends(student)):
        value = workspace(session_id)
        if value.get("student_id") != user["id"]:
            raise HTTPException(404, "Занятие не найдено")
        rows = store.db.execute(
            "SELECT id,message_id,session_id,student_id,teacher_id,assignment_id,group_id,"
            "target_profile,sent_at,opened_at,body FROM arm_vis_deliveries "
            "WHERE session_id=? AND student_id=? ORDER BY sent_at DESC,id DESC",
            (str(session_id), user["id"]),
        ).fetchall()
        return [decode(row, reveal_recipients=False) for row in rows]

    @instructor.get("/profiles")
    async def profiles(user=Depends(teacher)):
        rows = store.db.execute(
            "SELECT target_profile,count(*) FROM arm_vis_deliveries WHERE teacher_id=? "
            "GROUP BY target_profile", (user["id"],),
        ).fetchall()
        counts = {row[0]: row[1] for row in rows}
        return [{**profile, "incoming_count": counts.get(profile["id"], 0)} for profile in PROFILES]

    @instructor.get("/incoming")
    async def incoming(profile: ProfileId = Query(...), limit: int = Query(100, ge=1, le=200),
                       offset: int = Query(0, ge=0), user=Depends(teacher)):
        rows = store.db.execute(
            "SELECT id,message_id,session_id,student_id,teacher_id,assignment_id,group_id,"
            "target_profile,sent_at,opened_at,body FROM arm_vis_deliveries "
            "WHERE teacher_id=? AND target_profile=? ORDER BY sent_at DESC,id DESC LIMIT ? OFFSET ?",
            (user["id"], profile, limit, offset),
        ).fetchall()
        # The journal intentionally exposes only a count for recipients hidden from
        # the card notification strip.  The owned detail endpoint reveals the labels.
        return [decode(row, reveal_recipients=False) for row in rows]

    @instructor.get('/profiles/{profile}/recipients')
    async def recipient_settings(profile:ProfileId,user=Depends(teacher)):
        row=store.db.execute('SELECT revision,body FROM arm_recipient_settings WHERE teacher_id=? AND profile=?',(user['id'],profile)).fetchone()
        return {'revision':row[0] if row else 0,'recipients':json.loads(row[1]) if row else []}

    @instructor.put('/profiles/{profile}/recipients')
    async def save_recipient_settings(profile:ProfileId,body:RecipientSettings,user=Depends(teacher)):
        with mutation_lock,store.db:
            previous=await recipient_settings(profile,user)
            if previous['revision']!=body.revision:raise HTTPException(409,'Настройки изменены. Перечитайте профиль.')
            values=list(dict.fromkeys(body.recipients))
            store.db.execute('INSERT INTO arm_recipient_settings VALUES (?,?,?,?) ON CONFLICT(teacher_id,profile) DO UPDATE SET revision=excluded.revision,body=excluded.body',
                             (user['id'],profile,body.revision+1,json.dumps(values,ensure_ascii=False)))
            audit('configuration','dds.recipients.updated',user,{'profile':profile,'count':len(values),'revision':body.revision+1})
        return {'revision':body.revision+1,'recipients':values}

    @instructor.get("/incoming/{delivery_id}")
    async def incoming_detail(delivery_id: UUID, profile: ProfileId = Query(...), user=Depends(teacher)):
        row = delivery_row(delivery_id)
        if not row or row[4] != user["id"] or row[7] != profile:
            raise HTTPException(404, "Учебная доставка не найдена")
        return decode(row, reveal_recipients=True)

    @instructor.post("/incoming/{delivery_id}/open")
    async def open_delivery(delivery_id: UUID, body: OpenDelivery, user=Depends(teacher)):
        with mutation_lock:
            row = delivery_row(delivery_id)
            if not row or row[4] != user["id"] or row[7] != body.profile:
                raise HTTPException(404, "Учебная доставка не найдена")
            if not row[9]:
                opened_at = now()
                with store.db:
                    store.db.execute(
                        "UPDATE arm_vis_deliveries SET opened_at=? WHERE id=? AND opened_at IS NULL",
                        (opened_at, str(delivery_id)),
                    )
                    audit(str(delivery_id), "dds.delivery.opened", user, {"profile": body.profile})
                row = delivery_row(delivery_id)
            return decode(row, reveal_recipients=True)

    combined.include_router(student_api)
    combined.include_router(instructor)
    return combined
