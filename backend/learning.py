"""Classroom groups, assignments, and role-scoped session discovery."""
from communication import summary as communication_summary
import json
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from service_workflow import incident_status
from curriculum import metadata


def now():
    return datetime.now(timezone.utc).isoformat()


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class CreateGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Name


class AddMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: str = Field(min_length=1, max_length=100)


class CreateAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str = Field(min_length=1, max_length=100)
    scenario_id: str = Field(min_length=1, max_length=100)
    title: Name
    practice_with_hints: bool = False


class UpdateAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool
    practice_with_hints: bool | None = None


class Learning:
    def __init__(self, store, accounts):
        self.store = store
        self.accounts = accounts
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS learning_groups ("
            "id TEXT PRIMARY KEY, title TEXT NOT NULL, teacher_id TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS group_members ("
            "group_id TEXT NOT NULL, student_id TEXT NOT NULL, "
            "PRIMARY KEY (group_id, student_id))"
        )
        store.db.execute(
            "CREATE TABLE IF NOT EXISTS assignments ("
            "id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL, group_id TEXT NOT NULL, "
            "scenario_id TEXT NOT NULL, title TEXT NOT NULL, active INTEGER NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        store.db.commit()
        if store.db.is_postgres:
            store.db.execute('ALTER TABLE assignments ADD COLUMN IF NOT EXISTS practice_with_hints INTEGER NOT NULL DEFAULT 0')
        elif 'practice_with_hints' not in {row[1] for row in store.db.execute('PRAGMA table_info(assignments)')}:
            store.db.execute('ALTER TABLE assignments ADD COLUMN practice_with_hints INTEGER NOT NULL DEFAULT 0')
        store.db.commit()

    @staticmethod
    def require_role(user, role):
        if not user or not user.get("active") or user.get("role") != role:
            raise HTTPException(403, "Недостаточно прав")
        return user

    def _assignment(self, assignment_id):
        row = self.store.db.execute(
            "SELECT id, teacher_id, group_id, scenario_id, title, active, created_at, practice_with_hints "
            "FROM assignments WHERE id=?",
            (str(assignment_id),),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "teacher_id": row[1], "group_id": row[2],
            "scenario_id": row[3], "title": row[4], "active": bool(row[5]),
            "created_at": row[6],
            "practice_with_hints": bool(row[7]),
        }

    def assignment_for_student(self, assignment_id, scenario_id, user):
        self.require_role(user, "student")
        assignment = self._assignment(assignment_id)
        if not assignment or assignment["scenario_id"] != scenario_id:
            raise HTTPException(404, "Назначение не найдено")
        scenario = self.store.scenario(assignment["scenario_id"])
        if not scenario or not scenario.get("enabled"):
            raise HTTPException(404, "Сценарий недоступен")
        if not assignment["active"]:
            raise HTTPException(403, "Назначение неактивно")
        member = self.store.db.execute(
            "SELECT 1 FROM group_members WHERE group_id=? AND student_id=?",
            (assignment["group_id"], user["id"]),
        ).fetchone()
        if not member:
            raise HTTPException(403, "Назначение недоступно")
        return assignment

    def visible_scenarios(self, user):
        self.require_role(user, "student")
        rows = self.store.db.execute(
            "SELECT DISTINCT a.scenario_id FROM assignments a "
            "JOIN group_members gm ON gm.group_id=a.group_id "
            "WHERE gm.student_id=? AND a.active=1 ORDER BY a.created_at, a.id",
            (user["id"],),
        ).fetchall()
        result = []
        for (scenario_id,) in rows:
            scenario = self.store.scenario(scenario_id)
            if scenario and scenario.get("enabled"):
                result.append({"id": scenario["id"], "title": scenario["title"], **metadata(scenario)})
        return result

    @staticmethod
    def owns_session(user, value):
        if not user or not user.get("active"):
            return False
        if user.get("role") == "teacher":
            return value.get("teacher_id") == user.get("id")
        if user.get("role") == "student":
            return value.get("student_id") == user.get("id")
        return False

    def _group(self, group_id, teacher_id=None):
        if teacher_id is None:
            row = self.store.db.execute(
                "SELECT id, title, teacher_id, created_at FROM learning_groups WHERE id=?", (str(group_id),)
            ).fetchone()
        else:
            row = self.store.db.execute(
                "SELECT id, title, teacher_id, created_at FROM learning_groups WHERE id=? AND teacher_id=?",
                (str(group_id), teacher_id),
            ).fetchone()
        if not row:
            return None
        member_ids = [item[0] for item in self.store.db.execute(
            "SELECT student_id FROM group_members WHERE group_id=? ORDER BY student_id", (row[0],)
        ).fetchall()]
        students = []
        for student_id in member_ids:
            student = self.accounts.get_user(student_id)
            if student:
                students.append({key: student[key] for key in ("id", "display_name", "username")})
        return {"id": row[0], "title": row[1], "teacher_id": row[2], "created_at": row[3],
                "member_ids": member_ids, "students": students}

    def router(self, authorize):
        dependencies = [Depends(authorize)] if authorize else []
        instructor = APIRouter(prefix="/api/v1/instructor", dependencies=dependencies)
        student_api = APIRouter(prefix="/api/v1/student", dependencies=dependencies)

        def current(x_user_session: str = Header("", alias="X-User-Session")):
            user = self.accounts.current(x_user_session)
            if not user:
                raise HTTPException(401, "Неверная пользовательская сессия")
            return user

        def teacher(user=Depends(current)):
            return self.require_role(user, "teacher")

        def student(user=Depends(current)):
            return self.require_role(user, "student")

        @instructor.get("/students")
        async def students(user=Depends(teacher)):
            del user
            return [
                {key: account[key] for key in ("id", "display_name", "username")}
                for account in self.accounts.list_users()
                if account.get("active") and account.get("role") == "student"
            ]

        @instructor.get("/groups")
        async def groups(user=Depends(teacher)):
            ids = self.store.db.execute(
                "SELECT id FROM learning_groups WHERE teacher_id=? ORDER BY created_at, id", (user["id"],)
            ).fetchall()
            return [self._group(row[0], user["id"]) for row in ids]

        @instructor.post("/groups", status_code=201)
        async def create_group(body: CreateGroup, user=Depends(teacher)):
            group_id = str(uuid4())
            created_at = now()
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO learning_groups (id,title,teacher_id,created_at) VALUES (?,?,?,?)",
                    (group_id, body.title, user["id"], created_at),
                )
            return self._group(group_id, user["id"])

        @instructor.post("/groups/{group_id}/members")
        async def add_member(group_id: str, body: AddMember, user=Depends(teacher)):
            group = self._group(group_id, user["id"])
            if not group:
                raise HTTPException(404, "Группа не найдена")
            target = self.accounts.get_user(body.student_id)
            if not target or not target.get("active") or target.get("role") != "student":
                raise HTTPException(404, "Ученик не найден")
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO group_members (group_id,student_id) VALUES (?,?) ON CONFLICT (group_id,student_id) DO NOTHING",
                    (group_id, body.student_id),
                )
            return self._group(group_id, user["id"])

        @instructor.get("/assignments")
        async def assignments(user=Depends(teacher)):
            rows = self.store.db.execute(
                "SELECT id FROM assignments WHERE teacher_id=? ORDER BY created_at DESC, id", (user["id"],)
            ).fetchall()
            return [self._assignment(row[0]) for row in rows]

        @instructor.post("/assignments", status_code=201)
        async def create_assignment(body: CreateAssignment, user=Depends(teacher)):
            if not self._group(body.group_id, user["id"]):
                raise HTTPException(404, "Группа не найдена")
            scenario = self.store.scenario(body.scenario_id)
            if not scenario or not scenario.get("enabled"):
                raise HTTPException(404, "Сценарий недоступен")
            owner = self.store.db.execute(
                "SELECT teacher_id FROM scenario_owners WHERE scenario_id=?", (body.scenario_id,)
            ).fetchone()
            if owner and owner[0] != user["id"]:
                raise HTTPException(404, "Сценарий недоступен")
            assignment_id = str(uuid4())
            created_at = now()
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO assignments (id,teacher_id,group_id,scenario_id,title,active,created_at,practice_with_hints) "
                    "VALUES (?,?,?,?,?,1,?,?)",
                    (assignment_id, user["id"], body.group_id, body.scenario_id, body.title, created_at, int(body.practice_with_hints)),
                )
            return self._assignment(assignment_id)

        @instructor.patch("/assignments/{assignment_id}")
        async def update_assignment(assignment_id: str, body: UpdateAssignment, user=Depends(teacher)):
            assignment = self._assignment(assignment_id)
            if not assignment or assignment["teacher_id"] != user["id"]:
                raise HTTPException(404, "Назначение не найдено")
            with self.store.db:
                self.store.db.execute(
                    "UPDATE assignments SET active=?, practice_with_hints=? WHERE id=? AND teacher_id=?",
                    (int(body.active), int(assignment['practice_with_hints'] if body.practice_with_hints is None else body.practice_with_hints), assignment_id, user["id"]),
                )
            return self._assignment(assignment_id)

        @instructor.get("/sessions")
        async def instructor_sessions(user=Depends(teacher)):
            rows = self.store.db.execute(
                "SELECT body FROM workspace WHERE json_text(body,'teacher_id')=? "
                "ORDER BY json_text(body,'created_at') DESC,id DESC LIMIT 100", (user["id"],)
            ).fetchall()
            result = []
            for (encoded,) in rows:
                value = json.loads(encoded)
                if not self.owns_session(user, value):
                    continue
                scenario = self.store.scenario(value.get("scenario_id"))
                evaluation = value.get("dds_review") or value.get("evaluation") or {}
                result.append({
                    "id": value.get("id"), "number": value.get("number"),
                    "student_id": value.get("student_id"), "assignment_id": value.get("assignment_id"),
                    "student_name": (self.accounts.get_user(value.get('student_id')) or {}).get('display_name', 'Студент'),
                    "status": value.get("status"), "created_at": value.get("created_at"),
                    "score_percent": evaluation.get("score_percent"),
                    "communication": communication_summary(self.store, value),
                    "attempt_number": value.get('attempt_number', 1),
                    "restarted_from": value.get('restarted_from'), "restarted_to": value.get('restarted_to'),
                    "attempt_outcome": value.get('attempt_outcome'),
                    "scenario_title": scenario.get("title") if scenario else value.get("title"),
                    **metadata(value),
                })
            return result

        @instructor.get("/sessions/{session_id}")
        async def instructor_session(session_id: str, user=Depends(teacher)):
            row = self.store.db.execute("SELECT body FROM workspace WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Занятие не найдено")
            value = json.loads(row[0])
            if not self.owns_session(user, value):
                raise HTTPException(403, "Занятие недоступно")
            state = self.store.load(session_id)
            result = {**value, 'incident_status': incident_status(value), "messages": state.get("messages", []),
                      'communication': communication_summary(self.store, value),
                      "provider_error": state.get("provider_error")}
            result.pop("scenario", None)
            result.pop("evaluation_rubric", None)
            if value.get("status") != "Завершена":
                result.pop("evaluation", None)
                result.pop("ai_review", None)
            return result

        @student_api.get("/assignments")
        async def student_assignments(user=Depends(student)):
            rows = self.store.db.execute(
                "SELECT a.id FROM assignments a JOIN group_members gm ON gm.group_id=a.group_id "
                "WHERE gm.student_id=? AND a.active=1 ORDER BY a.created_at, a.id",
                (user["id"],),
            ).fetchall()
            result = []
            for (assignment_id,) in rows:
                assignment = self._assignment(assignment_id)
                scenario = self.store.scenario(assignment["scenario_id"])
                if scenario and scenario.get("enabled"):
                    result.append({**assignment, "scenario_title": scenario["title"], **metadata(scenario)})
            return result

        combined = APIRouter()
        combined.include_router(instructor)
        combined.include_router(student_api)
        return combined


def router(store, accounts, authorize):
    """Construct the classroom service and its HTTP routes."""
    learning = Learning(store, accounts)
    return learning.router(authorize)
