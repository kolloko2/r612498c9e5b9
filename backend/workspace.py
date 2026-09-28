"""Student workspace: persisted cards, optimistic locking and an auditable timeline."""
import asyncio
import json
import os
import random
import re
from practice_plan import require_approved, hint as practice_hint, approved as practice_approved
from contextlib import asynccontextmanager, AsyncExitStack
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import httpx
from transport_tls import httpx_verify
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from classifier import get_catalog, resolve
from routing import preview, service_catalog
from evaluation import (DEFAULT_RESPONSE_LIMIT_SECONDS, DEFAULT_TIME_LIMIT_SECONDS,
                        Rubric, evaluate)
from grammar import analyze as grammar_report
from semantic_grading import review as semantic_review, review_dds as semantic_review_dds
from dds_review import review as dds_decision_review, unfinished as unfinished_dds
from voice_client import caller as caller_id, request as voice_request
from adaptive import attempt_view, recommend
from briefing import correction_reveal
from communication import summary as communication_summary
from ai_review import review as review_card, review_dds as review_dds_actions
from llm import configuration
from field_dialogue import report_context
from teacher_guidance import examples as guidance_examples, initialize as initialize_guidance
from materials import material_context
from categories import CATEGORIES, CategoryId
from curriculum import Difficulty, DdsProfile, metadata, matches
from assessment import initialize as initialize_assessment, policy_for, evaluate_policy
from service_workflow import allowed_statuses, validate_transition, incident_status, STATUS_ALIASES, no_brigade_completion, NO_BRIGADE_COMMENT
from cluster import Coordinator, ClusterUnavailable, LockUnavailable


def now():
    return datetime.now(timezone.utc).isoformat()


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    caller_name: str = Field("", max_length=160)
    caller_status: str = Field("", max_length=80)
    phone: str = Field("", max_length=40)
    supplied_phone: str = Field("", max_length=40)
    scene_phone: str = Field("", max_length=40)
    country: str = Field("Россия", max_length=100)
    region: str = Field("Москва", max_length=100)
    city: str = Field("Москва", max_length=100)
    district: str = Field("", max_length=100)
    area: str = Field("", max_length=100)
    object: str = Field("", max_length=200)
    street: str = Field("", max_length=200)
    house: str = Field("", max_length=40)
    building: str = Field("", max_length=40)
    structure: str = Field("", max_length=40)
    apartment: str = Field("", max_length=40)
    entrance: str = Field("", max_length=40)
    floor: str = Field("", max_length=40)
    code: str = Field("", max_length=40)
    address_note: str = Field("", max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    emergency: bool = False
    important: bool = False
    bookmarked: bool = False
    description: str = Field("", max_length=1999)
    incident_type: str = Field("", max_length=1000)
    classifier_id: str = Field("", max_length=100)
    classifier_version: str = Field("", max_length=100)
    classifier_group: str = Field("", max_length=100)
    classifier_features: list[Annotated[str, StringConstraints(strip_whitespace=False, max_length=1000)]] = Field(default_factory=list, max_length=3)
    place: str = Field("", max_length=80)
    sign: str = Field("", max_length=100)
    detail: str = Field("", max_length=100)
    injured: bool = False
    no_access: bool = False
    threat_to_people: bool = False
    offense: bool = False
    injured_offsite: bool = False
    gasification: bool = False
    medical_help: bool = False
    evacuation: bool = False
    fsb_special: bool = False
    traffic_blocked: bool = False
    tunnel: bool = False
    pedestrian_structure: bool = False
    vehicle_structure: bool = False
    telecom_object: bool = False
    construction_site: bool = False
    culture_listed_object: bool = False
    territorial_oiv: bool = False
    territorial_oiv_tinao: bool = False
    territorial_roads_moscow: bool = False
    rhbz_polygon: bool = False
    rhbz_moscow: bool = False
    refused: bool = False
    no_contact: bool = False
    interrupted: bool = False
    services: list[Annotated[str, StringConstraints(min_length=1, max_length=160)]] = Field(default_factory=list, max_length=100)
    service_phones: dict[str, Annotated[str, StringConstraints(max_length=40)]] = Field(default_factory=dict)
    recipient_affiliations: dict[Literal['area', 'district', 'department'], Annotated[str, StringConstraints(min_length=1, max_length=160)]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def coordinate_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError('Укажите обе координаты или очистите обе')
        for scope in ('area', 'district'):
            if self.recipient_affiliations.get(scope) and not getattr(self, scope):
                raise ValueError('Для получателя территории заполните поле ' + scope)
        return self


def prefilled_from_scenario(scenario: dict) -> dict:
    """Build a DDS card from explicit teacher data and safe scenario facts."""
    supplied = scenario.get('prefilled_card') or {}
    card = Card.model_validate({
        'caller_name': scenario['victim_name'],
        'address_note': scenario['location'],
        'description': scenario['incident'],
        'incident_type': scenario['title'],
        **supplied,
    }).model_dump()
    owner = (scenario.get('owner_service') or '').strip()
    if owner and owner not in card['services']:
        card['services'].append(owner)
    routing = preview(card)
    card['services'] = list(dict.fromkeys([*card['services'],
        *(item['service'] for item in routing['suggestions'])]))
    unknown_phones = set(card['service_phones']) - set(card['services'])
    if unknown_phones:
        raise ValueError('Номера связи допустимы только для служб из входящей карточки: '
                         + ', '.join(sorted(unknown_phones)))
    result = Card.model_validate(card).model_dump()
    return Card.model_validate(result).model_dump()


class CreateLesson(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    group_id: str
    scenario_ids: list[str] = Field(default_factory=list, max_length=20)
    category_ids: list[CategoryId] = Field(default_factory=list, max_length=6)
    difficulty: Difficulty | None = None
    dds_profile: DdsProfile | None = None
    prefilled_scenario_ids: list[str] = Field(default_factory=list, max_length=20)
    mode: Literal['fill', 'actions', 'mixed'] = 'fill'
    source_session_ids: list[UUID] = Field(default_factory=list, max_length=20)
    cards_per_student: int | None = Field(default=3, ge=1, le=200)
    # Диспетчер в реальной смене ведёт несколько происшествий сразу, поэтому
    # преподаватель задаёт, сколько карточек может быть открыто одновременно.
    parallel_cards: int = Field(default=1, ge=1, le=10)
    # Адаптация уровня по результатам обучающегося; адресное задание её
    # перекрывает, потому что оно — прямое решение преподавателя.
    adaptive_difficulty: bool = False
    # Полный цикл 112 (приём вызова) против работы диспетчера ДДС с готовой
    # карточкой задаёт преподаватель. Переключение разрешается отдельно: без
    # него обучающийся работает только в выбранном режиме.
    allow_mode_switch: bool = False
    practice_with_hints: bool = False
    transport: Literal['text', 'sip'] = 'text'
    sip_extensions: dict[str, Annotated[str, StringConstraints(pattern=r'^[0-9]{1,8}$')]] = Field(default_factory=dict, max_length=100)
    # Номер рабочего места закрепляется за обучающимся: преподаватель и отчёт
    # называют место, а не только фамилию.
    workstations: dict[str, Annotated[str, StringConstraints(min_length=1, max_length=80)]] = Field(default_factory=dict, max_length=100)
    # Адресное задание: конкретному месту — конкретный сценарий. Без записи
    # обучающийся получает случайный сценарий занятия, как раньше.
    student_scenarios: dict[str, str] = Field(default_factory=dict, max_length=100)


class GuidedStep(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # null убирает подсказку с экранов; иначе номер шага вводного курса.
    step: int | None = Field(None, ge=1, le=50)


class PracticePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    practice_with_hints: bool


# Номер рабочего места, введённый оператором при входе. В реальной Системе 112
# он вводится на экране авторизации вместе с логином и паролем. Назначение
# преподавателя приоритетнее: оно и есть контроль, кто за каким местом сидит.
Workstation = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]


class NextCard(BaseModel):
    model_config = ConfigDict(extra='forbid')
    after_session_id: UUID | None = None
    mode: Literal['fill', 'actions'] | None = None
    workstation: Workstation = ''


class UnproductiveCall(BaseModel):
    """Нерезультативный вызов: нет контакта с заявителем или срыв звонка.

    Инструкция оператора описывает это как отдельный быстрый путь: карточка
    сохраняется пустой, сразу уходит в завершённый статус и помечается
    проверенной — оператор не заполняет её и освобождает линию. Проверка здесь
    техническая (оператор подтвердил, что заполнять нечего), а не методическая
    оценка преподавателя.
    """
    model_config = ConfigDict(extra='forbid')
    message_id: UUID
    kind: Literal['no_contact', 'interrupted']


class Reminder(BaseModel):
    """Напоминание по карточке («будильник») с временем срабатывания."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message_id: UUID
    text: str = Field(min_length=1, max_length=500)
    at: datetime


class TeacherFinish(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=1000)


class VisService(BaseModel):
    """Служба, добавленная в карточку внешней информационной системой.

    Инструкция оператора: «В карточку, созданную оператором Службы 112, из
    внешней системы по решению оператора службы могут быть добавлены
    дополнительные службы для реагирования. В этом случае добавленная служба
    будет отображаться с пометкой ВИС». В тренажёре сторону службы играет
    преподаватель: реальная интеграция отсутствует, и доставка ничего не
    отправляет за пределы стенда.
    """
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message_id: UUID
    service: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1000)


class TeacherFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message_id: UUID
    text: str = Field(min_length=1, max_length=2000)


class CreateSession(BaseModel):
    scenario_id: str
    assignment_id: str | None = None
    transport: Literal["text", "sip"] = "text"
    workstation: Workstation = ''


class LessonPhones(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sip_extensions: dict[str, Annotated[str, StringConstraints(pattern=r'^[0-9]{1,8}$')]] = Field(min_length=1, max_length=100)


class SaveCard(BaseModel):
    revision: int = Field(ge=0)
    card: Card


class SaveRubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    rubric: Rubric | None


class Message(BaseModel):
    message_id: UUID
    text: str = Field(min_length=1, max_length=4000)


class ServiceAction(BaseModel):
    service: str = Field(min_length=1, max_length=160)
    status: str = Field(min_length=1, max_length=80)
    order_number: str = Field(default='', max_length=80)
    comment: str = Field(default='', max_length=1000)
    message_id: UUID | None = None


class AssignCrew(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message_id: UUID
    crew_id: str = Field(min_length=1, max_length=80)
    decision_by: Literal['dispatcher', 'leadership']
    decision_note: str = Field('', max_length=500)


class NotificationAction(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message_id: UUID
    service: str = Field(min_length=1, max_length=160)
    destination: str = Field(min_length=1, max_length=160)
    phone: str = Field(default='', max_length=40)
    recipient: str = Field(min_length=1, max_length=160)
    comment: str = Field(min_length=1, max_length=1000)


CORRECTABLE_FIELDS = ('city', 'district', 'area', 'object', 'street', 'house', 'building',
                      'structure', 'apartment', 'entrance', 'address_note', 'description',
                      'incident_type')
# Поля карточки, которые ДДС ведёт сама. Остальное заполняет Служба 112, и
# ДДС вправе только сообщить ей об ошибке.
DDS_EDITABLE_FIELDS = frozenset({'bookmarked'})


class ErrorReport(BaseModel):
    """Звонок ДДС в Службу 112: в карточке ошибка, правильное значение такое-то."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message_id: UUID
    field: Literal[CORRECTABLE_FIELDS]
    correct_value: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=160)
    recipient: str = Field(min_length=1, max_length=160)
    comment: str = Field('', max_length=1000)


def correction_evidence_visible(value: dict) -> bool:
    """Правильные сведения ДДС узнаёт из звонка бригады с места.

    Уточнение становится доступно только после полученного доклада бригады:
    начатый, но не прослушанный звонок и запрос хода работ без нового
    доклада его не открывают. Если докладов в сценарии нет, источник —
    ответ старшего на запрос обстановки; без бригады — вводная после приёма.
    """
    if value.get('exercise_mode') != 'actions':
        return True
    events = value.get('events', [])
    if correction_reveal(value) is not None:
        return True
    if value.get('planned_unlocks'):
        return False
    if value.get('crew_options'):
        return any(event.get('type') == 'progress.requested' for event in events)
    return bool(value.get('accepted_at'))


class ForwardCard(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message_id: UUID
    service: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1000)


class LinkCard(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_id: UUID


class RoutingMapping(BaseModel):
    cell: str
    incident_type: str


class RoutingSuggestion(BaseModel):
    service: str
    mappings: list[RoutingMapping]
    # Основная служба для типа происшествия: в реальном АРМ она подчёркнута
    # двойной линией, чтобы оператор видел, кто отвечает за происшествие.
    primary: bool = False


class RoutingExclusion(BaseModel):
    service: str
    cell: str
    reason: str


class RoutingUnresolved(RoutingExclusion):
    source_value: str


class RoutingPreview(BaseModel):
    rules_version: str
    classifier_id: str
    classifier_version: str
    source_row: int | None
    flags: dict[str, bool]
    primary_services: list[str] = Field(default_factory=list)
    suggestions: list[RoutingSuggestion]
    excluded: list[RoutingExclusion]
    warnings: list[str]
    unresolved: list[RoutingUnresolved] = Field(default_factory=list)


class GrammarPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    card: Card
    comment: str = Field(default='', max_length=1000)
    text: str | None = Field(default=None, max_length=100000)


# Статусы хода работ: их открывают оперативные вводные от реагирующей стороны.
PROGRESS_STATUSES = ('Начало реагирования', 'Прибытие', 'Проведение работ', 'Работы завершены')


def sequential(value: dict, service: str, allowed: list[str]) -> list[str]:
    """Своя ДДС проходит статусы хода работ по порядку, без пропусков.

    Карточка считается отработанной, только когда активированы все статусы,
    а вернуться к пропущенному статусу нельзя.
    Завершение 103 без бригады — отдельный явный путь источника.
    """
    if value.get('exercise_mode') != 'actions' or service != value.get('owner_service'):
        return allowed
    current = value['service_states'].get(service, {}).get('status')
    progress = [status for status in allowed if status in PROGRESS_STATUSES
                and not (not value.get('assigned_crew')
                         and no_brigade_completion(service, current, status, NO_BRIGADE_COMMENT))]
    return [status for status in allowed if status not in progress[1:]]


def visible_statuses(value: dict) -> dict[str, list[str]]:
    """Что рабочее место вправе предложить по каждой службе.

    Чужая служба не предлагает ничего: её состояние приходит от неё самой.
    Статусы хода работ по своей службе появляются по мере поступления
    оперативных вводных, а не все сразу.
    """
    owner = value.get('owner_service')
    gated = unlocked_statuses(value)
    result: dict[str, list[str]] = {}
    for service in value['card']['services']:
        if owner and service != owner:
            result[service] = []
            continue
        allowed = sequential(value, service, allowed_statuses(
            service, value['service_states'].get(service, {}).get('status')))
        if gated is not None:
            allowed = [status for status in allowed
                       if status not in PROGRESS_STATUSES or status in gated
                       or (not value.get('assigned_crew') and no_brigade_completion(
                           service, value['service_states'].get(service, {}).get('status'),
                           status, NO_BRIGADE_COMMENT))]
        result[service] = allowed
    return result


def unlocked_statuses(value: dict) -> set[str] | None:
    """Статусы, подтверждённые пришедшими вводными.

    ``None`` — сценарий не описывает оперативных вводных, и тогда ограничение
    не применяется: старые сценарии и занятия продолжают работать как раньше.
    """
    planned = value.get('planned_unlocks')
    if not planned:
        return None
    delivered = {event['detail'].get('id') for event in value['events']
                 if event['type'] == 'situation.update'}
    return {status for update_id, status in planned.items() if update_id in delivered}


def update_elapsed(value: dict, item: dict) -> float:
    """New crew reports start at dispatch; legacy attempts retain their clock."""
    anchor = value['created_at']
    if value.get('updates_anchor') in ('crew_assigned', 'crew_briefed') and item.get('unlocks_status'):
        crew = value.get('assigned_crew') or {}
        anchor = next((event['at'] for event in value.get('events', [])
                       if event['type'] == 'notification.recorded'
                       and event.get('detail', {}).get('source') == 'briefing'
                       and event['detail'].get('counterpart') == 'crew'
                       and event['detail'].get('crew_id') == crew.get('id')
                       and event['at'] >= crew.get('at', '')), None) if crew else None
        if not anchor:
            return -1
    return (datetime.now(timezone.utc) - datetime.fromisoformat(anchor)).total_seconds()


REPORT_GAP_SECONDS = 20


def report_due(value: dict, item: dict, planned: list[dict]) -> bool:
    """Доклад бригады поступает по сценарию, но не раньше, чем отражён предыдущий.

    Следующий доклад приходит не раньше REPORT_GAP_SECONDS после того, как
    диспетчер поставил статус по предыдущему: доклады не наваливаются подряд.
    """
    if update_elapsed(value, item) < item['after_seconds']:
        return False
    if value.get('exercise_mode') != 'actions':
        # У оператора 112 статусы служб приходят сами (§11.1 инструкции АРМ-112):
        # их никто не «отражает», поэтому следующий не ждёт предыдущего.
        return True
    owner = value.get('owner_service')
    if not item.get('unlocks_status') or not owner:
        return True
    earlier = [other for other in planned if other.get('unlocks_status')
               and other['after_seconds'] < item['after_seconds']]
    if not earlier:
        return True
    previous = max(earlier, key=lambda other: other['after_seconds'])
    events = value.get('events', [])
    delivered = next((event['at'] for event in events if event['type'] == 'situation.update'
                      and event['detail'].get('id') == previous['id']), None)
    if not delivered:
        return False
    reflected = next((event['at'] for event in events if event['type'] == 'service.updated'
                      and event['detail'].get('service') == owner
                      and event['detail'].get('status') == previous['unlocks_status']
                      and event['at'] >= delivered), None)
    if not reflected:
        return False
    return (datetime.now(timezone.utc) - datetime.fromisoformat(reflected)).total_seconds() >= REPORT_GAP_SECONDS


def apply_default_norms(value: dict) -> None:
    """Нормативы времени для готовой карточки ДДС.

    30 секунд — от появления карточки в строке
    сообщений до её открытия; 3 минуты — до первой записи своей службы
    (статус и текст). Остальные сроки не нормируются: работы могут идти часами,
    поэтому общего лимита обработки нет. ``limit_seconds``/``within_limit``
    в этом режиме относятся к первой записи, а не ко всему времени занятия.
    """
    if value.get('exercise_mode') != 'actions':
        return
    timing = value.get('evaluation', {}).get('timing')
    if not timing:
        return
    response_limit = value.get('time_limit_seconds') or DEFAULT_RESPONSE_LIMIT_SECONDS
    response_seconds = timing.get('response_seconds')
    timing['response_limit_seconds'] = response_limit
    timing['response_within_limit'] = (None if response_seconds is None
                                       else response_seconds <= response_limit)
    if value.get('owner_service'):
        first_record = value.get('first_record_seconds')
        timing['first_record_seconds'] = first_record
        timing['limit_seconds'] = DEFAULT_TIME_LIMIT_SECONDS
        timing['within_limit'] = None if first_record is None else first_record <= DEFAULT_TIME_LIMIT_SECONDS
        return
    timing['limit_seconds'] = DEFAULT_TIME_LIMIT_SECONDS
    timing['within_limit'] = timing['elapsed_seconds'] <= DEFAULT_TIME_LIMIT_SECONDS


def router(store, engine, authorize, accounts=None, learning=None, coordinator=None):
    initialize_guidance(store)
    coordinator = coordinator or Coordinator.from_database(store.db)
    store.db.execute('CREATE TABLE IF NOT EXISTS lessons (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    store.db.commit()

    initialize_assessment(store)
    actor = ContextVar('workspace_actor', default=None)

    @asynccontextmanager
    async def coordinated(namespace, resource):
        try:
            async with coordinator.hold(namespace, str(resource)):
                yield
        except (LockUnavailable, ClusterUnavailable):
            raise HTTPException(503, 'Операция занята или кластерная координация недоступна') from None

    async def serialize_mutation(sid: UUID):
        async with coordinated('workspace-session', sid):
            yield

    async def student_identity(x_user_session: str = Header('')):
        user = accounts.current(x_user_session) if accounts else None
        if user and user['role'] != 'student':
            raise HTTPException(403, 'Требуется роль обучающегося')
        token = actor.set(user)
        try:
            yield user
        finally:
            actor.reset(token)

    async def teacher_identity(x_user_session: str = Header('')):
        user = accounts.current(x_user_session) if accounts else None
        if user and user['role'] != 'teacher':
            raise HTTPException(403, 'Требуется роль преподавателя')
        token = actor.set(user)
        try:
            yield user
        finally:
            actor.reset(token)

    api = APIRouter(prefix="/api/v1/student", dependencies=[Depends(authorize), Depends(student_identity)])
    store.db.execute("CREATE TABLE IF NOT EXISTS workspace (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
    store.db.execute("CREATE TABLE IF NOT EXISTS rubrics (scenario_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)")
    store.db.execute("CREATE TABLE IF NOT EXISTS rubric_history (scenario_id TEXT NOT NULL, revision INTEGER NOT NULL, at TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (scenario_id, revision))")
    store.db.commit()

    def rubric_for(scenario_id, teacher_id=None):
        key = f'{teacher_id}:{scenario_id}' if teacher_id else scenario_id
        row = store.db.execute("SELECT revision, body FROM rubrics WHERE scenario_id=?", (key,)).fetchone()
        return {"revision": row[0], "rubric": json.loads(row[1])} if row else {"revision": 0, "rubric": None}

    def check_scenario_visibility(scenario_id):
        if actor.get():
            owner = store.db.execute('SELECT teacher_id FROM scenario_owners WHERE scenario_id=?', (scenario_id,)).fetchone()
            if owner and owner[0] != actor.get()['id']:
                raise HTTPException(404, 'Сценарий не найден')

    instructor = APIRouter(prefix="/api/v1/instructor", dependencies=[Depends(authorize), Depends(teacher_identity)])

    @instructor.get("/scenarios/{scenario_id}/rubric")
    async def get_rubric(scenario_id: str):
        check_scenario_visibility(scenario_id)
        if not store.scenario(scenario_id):
            raise HTTPException(404, "Сценарий не найден")
        return rubric_for(scenario_id, actor.get()['id'] if actor.get() else None)

    @instructor.put("/scenarios/{scenario_id}/rubric")
    async def save_rubric(scenario_id: str, body: SaveRubric):
        check_scenario_visibility(scenario_id)
        if not store.scenario(scenario_id):
            raise HTTPException(404, "Сценарий не найден")
        teacher_id = actor.get()['id'] if actor.get() else None
        key = f'{teacher_id}:{scenario_id}' if teacher_id else scenario_id
        current = rubric_for(scenario_id, teacher_id)
        if current["revision"] != body.revision:
            raise HTTPException(409, "Эталон изменён в другом окне. Откройте его заново.")
        revision = current["revision"] + 1
        encoded = body.rubric.model_dump_json() if body.rubric else "null"
        with store.db:
            store.db.execute("INSERT INTO rubrics VALUES (?,?,?) ON CONFLICT (scenario_id) DO UPDATE SET revision=excluded.revision,body=excluded.body", (key, revision, encoded))
            store.db.execute("INSERT INTO rubric_history VALUES (?,?,?,?)", (key, revision, now(), encoded))
        return {"revision": revision, "rubric": json.loads(encoded)}

    def load(sid):
        row = store.db.execute("SELECT body FROM workspace WHERE id=?", (str(sid),)).fetchone()
        if not row:
            raise HTTPException(404, "Занятие не найдено")
        value = json.loads(row[0])
        user = actor.get()
        owner_key = 'teacher_id' if user and user['role'] == 'teacher' else 'student_id'
        if user and value.get(owner_key) != user['id']:
            raise HTTPException(404, "Занятие не найдено")
        return value

    # В полном цикле 112 сохраняется время первого действия обучающегося.
    # Для готовой карточки ДДС 30 секунд считаются отдельно — до открытия
    # входящей строки, а решение службы оценивается другим критерием.
    STUDENT_ACTIONS = {'card.saved', 'service.updated', 'notification.recorded',
                       'card.processed', 'card.linked', 'card.forwarded', 'operator.utterance',
                       'card.error_reported',
                       # Закрытие нерезультативного вызова — тоже действие
                       # оператора, и норматив реакции к нему применим.
                       'card.unproductive'}

    def persist(value, kind, detail=None):
        if kind in STUDENT_ACTIONS and not value.get('first_action_at'):
            value['first_action_at'] = now()
        value["events"].append({"seq": len(value["events"]) + 1, "at": now(), "type": kind, "detail": detail or {}})
        with store.db:
            store.db.execute("INSERT INTO workspace VALUES (?,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body", (value["id"], json.dumps(value, ensure_ascii=False)))

    def student_view(value):
        result = dict(value)
        result['practice_with_hints'] = bool(value.get('practice_with_hints', False))
        if not actor.get() or actor.get()['role'] != 'teacher':
            for key in ('dds_expectation', 'planned_unlocks'):
                result.pop(key, None)
        result['correction_evidence'] = (
            list((value.get('dds_expectation') or {}).get('correction_evidence', {}).values())
            if correction_evidence_visible(value) else [])
        result['dds_editable_fields'] = sorted(DDS_EDITABLE_FIELDS)
        owner_state = value.get('service_states', {}).get(value.get('owner_service'), {})
        result['card_locked'] = (value.get('status') == 'Завершена' or
                                value.get('exercise_mode') == 'actions' and
                                owner_state.get('status') in ('Работы завершены', 'Отказ от выполнения работ'))
        result['dds_assessment_enabled'] = bool(value.get('dds_expectation'))
        snapshot = store.load(value['id']).get('scenario') or {}
        result['practice_with_hints'] = result['practice_with_hints'] and practice_approved(snapshot)
        result['practice_hint'] = practice_hint(snapshot, result)
        if value.get('text_input_allowed') is False and (actor.get() or {}).get('role') != 'teacher':
            # In a voice-only lesson the spoken report is the evidence. Do not
            # hand its transcript to the student's browser through card state.
            result['situation_updates'] = [
                {key: update[key] for key in ('id', 'at', 'unlocks_status', 'transport') if key in update}
                for update in value.get('situation_updates', [])]
            result['events'] = [
                {**event, 'detail': {key: event['detail'][key]
                                     for key in ('id', 'unlocks_status', 'transport', 'call_id')
                                     if key in event.get('detail', {})}}
                if event['type'] == 'situation.update' else event
                for event in value.get('events', [])]
        result['completion_missing'] = (unfinished_dds(value, value.get('dds_expectation'))
                                        if value.get('exercise_mode') == 'actions' and value['status'] != 'Завершена' else [])
        return result

    def public(value):
        state = store.load(value["id"])
        pending_reports = []
        if (value.get('exercise_mode') == 'actions' and value.get('sip_extension')
                and value['status'] != 'Завершена'):
            delivered = {event['detail'].get('id') for event in value['events']
                         if event['type'] == 'situation.update'}
            pending_reports = [{'id': item['id'], 'source': item['source'],
                                'call_id': (value.get('field_report_calls') or {}).get(item['id'], {}).get('call_id')}
                               for item in (state.get('scenario') or {}).get('updates', [])
                               if item['id'] not in delivered
                               and report_due(value, item, (state.get('scenario') or {}).get('updates', []))]
            if value.get('text_input_allowed') is False and (actor.get() or {}).get('role') != 'teacher':
                pending_reports = [{key: report[key] for key in ('id', 'call_id')}
                                   for report in pending_reports]
        saves = [event['at'] for event in value.get('events', []) if event['type'] == 'card.saved']
        repeat_calls = [{'id': key, 'call_id': call['call_id'], 'ended': bool(store.load(call['session_id']).get('ended')),
                         'recorded': any(at > call['started_at'] for at in saves)}
                        for key, call in (value.get('repeat_calls') or {}).items()]
        return {**student_view(value), 'incident_status': incident_status(value),
                'repeat_call_status': repeat_calls,
                'allowed_service_statuses': visible_statuses(value),
                'owner_service': value.get('owner_service', ''),
                'pending_phone_reports': pending_reports,
                "messages": state["messages"], "provider_error": state.get("provider_error")}

    @instructor.post('/sessions/{sid}/feedback', dependencies=[Depends(serialize_mutation)])
    async def teacher_feedback(sid: UUID, body: TeacherFeedback):
        user = actor.get()
        if not user or user['role'] != 'teacher':
            raise HTTPException(403, 'Требуется преподаватель')
        value = load(sid)
        notes = value.setdefault('teacher_feedback', [])
        for note in notes:
            if note['id'] == str(body.message_id):
                if note['text'] != body.text:
                    raise HTTPException(409, 'Идентификатор сообщения уже использован')
                return note
        if len(notes) >= 100:
            raise HTTPException(409, 'Достигнут лимит 100 замечаний на занятие')
        note = {'id': str(body.message_id), 'text': body.text, 'at': now(),
                'teacher_id': user['id'], 'teacher_name': user['display_name'],
                'phase': 'completed' if value['status'] == 'Завершена' else 'active'}
        notes.append(note)
        persist(value, 'teacher.feedback', {'message_id': note['id'], 'teacher_id': user['id']})
        return note

    @instructor.post('/sessions/{sid}/vis-service', dependencies=[Depends(serialize_mutation)])
    async def vis_service(sid: UUID, body: VisService):
        """Добавить службу в карточку от имени внешней системы.

        Служба появляется в полосе оповещения с пометкой ВИС и не может быть
        снята обучающимся, как и любая сохранённая служба. Это не интеграция:
        данные не покидают стенд.
        """
        user = actor.get()
        if not user or user['role'] != 'teacher':
            raise HTTPException(403, 'Требуется преподаватель')
        value = load(sid)
        if value.get('teacher_id') != user['id']:
            raise HTTPException(404, 'Занятие не найдено')
        payload = body.model_dump(mode='json')
        for existing in value['events']:
            if existing['type'] == 'service.vis_added' and existing['detail'].get('message_id') == str(body.message_id):
                if existing['detail'] != payload:
                    raise HTTPException(409, 'Идентификатор уже использован')
                return public(value)
        if value['status'] == 'Завершена':
            raise HTTPException(409, 'Занятие завершено')
        if not value.get('revision'):
            raise HTTPException(409, 'Карточка ещё не сохранена обучающимся')
        if body.service in value['card']['services']:
            raise HTTPException(422, 'Служба уже есть в карточке')
        if len(value['card']['services']) >= 100:
            raise HTTPException(422, 'В карточке допускается не более 100 служб')
        value['card']['services'].append(body.service)
        value['service_states'][body.service] = {
            'status': 'Добавлена', 'comment': body.reason, 'at': now(), 'added_at': now(),
            # Источник отличает службу внешней системы от выбранной оператором.
            'source': 'vis',
        }
        value['revision'] += 1
        persist(value, 'service.vis_added', payload)
        return public(value)

    def event(sid, kind, **payload):
        return {"event_id": str(uuid4()), "session_id": str(sid), "type": kind,
                "payload": {"mode": "auto", **payload}}

    def editable(value):
        if value["status"] == "Завершена":
            raise HTTPException(409, "Занятие завершено; карточка доступна только для просмотра")
        if value.get('lesson_id'):
            lesson = lesson_load(value['lesson_id'])
            if lesson['state'] != 'running':
                raise HTTPException(409, 'Преподаватель завершает занятие; новые действия недоступны')

    @api.get("/scenarios")
    async def scenarios():
        # A student never receives hidden scenario facts or the caller's location.
        if actor.get():
            return learning.visible_scenarios(actor.get())
        return [{"id": s["id"], "title": s["title"]} for s in store.list_scenarios() if s["enabled"]]

    @api.get("/classifier")
    async def classifier_catalog():
        return get_catalog()

    @api.get('/routing/catalog')
    async def routing_catalog():
        return service_catalog()

    @instructor.get('/routing/catalog')
    async def instructor_routing_catalog():
        """Тот же каталог служб преподавателю: нужен для добавления службы от ВИС."""
        return service_catalog()

    @api.post("/routing/preview", response_model=RoutingPreview)
    async def routing_preview(body: Card):
        try:
            return preview(body.model_dump())
        except ValueError:
            raise HTTPException(422, "Выберите актуальную запись классификатора и соответствующие ей признаки")

    @api.post('/grammar/preview')
    @instructor.post('/grammar/preview')
    async def grammar_preview(body: GrammarPreview):
        # No hidden rubric or expected answer is exposed to the student.
        card = body.card.model_dump()
        if body.text is not None:
            card['description'] = body.text
        return grammar_report(card, comments=[body.comment])

    @api.get("/sessions")
    async def sessions(limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0)):
        if actor.get():
            rows = store.db.execute("SELECT body FROM workspace WHERE json_text(body,'student_id')=? ORDER BY json_text(body,'created_at') DESC,id DESC LIMIT ? OFFSET ?", (actor.get()['id'], limit, offset))
        else:
            rows = store.db.execute("SELECT body FROM workspace ORDER BY json_text(body,'created_at') DESC,id DESC LIMIT ? OFFSET ?", (limit, offset))
        return [{**student_view(v), 'incident_status': incident_status(v)} for v in (json.loads(row[0]) for row in rows)]

    @api.post("/sessions", status_code=201)
    async def create(body: CreateSession):
        return await create_card(body)

    async def create_card(body, lesson_assignment=None, template=None, scenario_snapshot=None, rubric_snapshot=None, restart_source=None):
        assignment = None
        if lesson_assignment:
            assignment = lesson_assignment
        elif actor.get():
            if not body.assignment_id:
                raise HTTPException(422, 'Выберите назначенное задание')
            assignment = learning.assignment_for_student(body.assignment_id, body.scenario_id, actor.get())
        scenario = template['scenario'] if template else scenario_snapshot or store.scenario(body.scenario_id)
        if not scenario or not scenario["enabled"]:
            raise HTTPException(404, "Сценарий недоступен")
        if (assignment or {}).get('practice_with_hints'):
            require_approved(scenario)
        sid = str(uuid5(NAMESPACE_URL, 'trainer112:restart:' + restart_source['id'])) if restart_source else str(uuid4())
        if restart_source and store.db.execute('SELECT 1 FROM workspace WHERE id=?', (sid,)).fetchone():
            return public(load(sid))
        count = store.db.execute("SELECT count(*) FROM workspace").fetchone()[0]
        value = {"id": sid, "number": 910001 + count, "scenario_id": body.scenario_id,
                 "title": scenario["title"], "transport": body.transport, "created_at": now(),
                 "status": "Новая", "revision": 0, "card": Card().model_dump(), "events": [],
                 "service_states": {}, "call_id": None}
        value.update(metadata(scenario))
        value['practice_with_hints'] = bool((assignment or {}).get('practice_with_hints', False))
        if restart_source:
            value.update(restarted_from=restart_source['id'], attempt_number=restart_source.get('attempt_number', 1)+1)
            for key in ('lesson_position', 'lesson_title', 'previous_session_id', 'sip_extension'):
                if key in restart_source:
                    value[key] = restart_source[key]
        value['text_input_allowed'] = scenario.get('text_input_allowed', True)
        if not value['text_input_allowed'] and not template and body.transport != 'sip':
            raise HTTPException(409, 'В этом сценарии разрешён только голосовой ввод через IP-телефон')
        assigned_place = (lesson_assignment or {}).get('workstations', {}).get(
            actor.get()['id']) if actor.get() else None
        value['registration'] = {
            'operator': actor.get()['display_name'] if actor.get() else 'Учебный оператор',
            'workstation': assigned_place or body.workstation
            or os.getenv('TRAINING_WORKSTATION_ID', 'Учебное АРМ')[:80],
            # Преподаватель видит, чей это номер: назначенный им или введённый
            # самим обучающимся при входе.
            'workstation_source': 'teacher' if assigned_place else 'operator' if body.workstation else 'default',
        }
        if assignment:
            value.update(student_id=actor.get()['id'], teacher_id=assignment['teacher_id'], assignment_id=assignment['id'], group_id=assignment['group_id'])
            if lesson_assignment:
                value['lesson_id'] = assignment['id']
                extension = assignment.get('sip_extensions', {}).get(actor.get()['id'])
                if extension:
                    value['sip_extension'] = extension
        # Freeze facts before either text input or SIP connects.
        state = store.load(sid)
        state["scenario"] = scenario
        # Reference answers are private and frozen before the student starts.
        assessment = {'revision': 0, 'rubric': None} if template else rubric_snapshot or rubric_for(body.scenario_id, assignment['teacher_id'] if assignment else None)
        state["evaluation_rubric"] = assessment
        if template and 'assessment_policy' in template:
            state['assessment_policy'] = template['assessment_policy']
        elif rubric_snapshot and 'assessment_policy' in rubric_snapshot:
            state['assessment_policy'] = rubric_snapshot['assessment_policy']
        else:
            state['assessment_policy'] = policy_for(store, body.scenario_id, assignment['teacher_id'] if assignment else None)
        value["assessment_enabled"] = assessment["rubric"] is not None
        value["time_limit_seconds"] = assessment["rubric"]["time_limit_seconds"] if assessment["rubric"] else None
        if template:
            value.update(card=template['card'], revision=1, status='В работе', exercise_mode='actions',
                         initial_card=template['card'], routing=preview(template['card']),
                         saved_at=now())
            if template['card'].get('classifier_id'):
                record = resolve(template['card']['classifier_id'], template['card']['classifier_version'])
                value['classification'] = {'id': record['id'],
                                           'version': template['card']['classifier_version'],
                                           'source_row': record['source_row'],
                                           'main_service': record['main_service']}
            value['service_states'] = {service: {'status': 'Добавлена',
                                                'comment': '', 'at': value['created_at']}
                                       for service in template['card']['services']}
            value['source_kind'] = template.get('source_kind', 'student_card')
            value['time_limit_seconds'] = 30
            value['crew_options'] = scenario.get('crew_options') or []
            if value['crew_options']:
                value['updates_anchor'] = 'crew_briefed'
            if not value['text_input_allowed'] and not value.get('sip_extension'):
                raise HTTPException(409, 'Сценарий требует телефон. Преподаватель должен назначить SIP-номер участнику занятия.')
        # Своя ДДС и план разблокировок фиксируются вместе с карточкой: снимок
        # сценария заморожен, и правка сценария не меняет уже выданную карточку.
        owner = (scenario.get('owner_service') or '').strip()
        if owner:
            value['owner_service'] = owner
            if owner not in value['card']['services']:
                value['card']['services'] = [*value['card']['services'], owner]
            value['service_states'].setdefault(owner, {'status': 'Добавлена',
                                                       'comment': '', 'at': now(), 'added_at': now()})
        unlocks = {item['id']: item['unlocks_status'] for item in (scenario.get('updates') or [])
                   if item.get('unlocks_status')}
        if unlocks:
            value['planned_unlocks'] = unlocks
        if scenario.get('dds_expectation'):
            value['dds_expectation'] = scenario['dds_expectation']
        elif template and owner:
            value['dds_expectation'] = {'should_accept': True, 'brief_service': owner,
                                        'update_response_limit_seconds': 90}
        store.save(sid, state)
        persist(value, "session.created")
        if body.transport == "text" and not template:
            await engine.handle(sid, event(sid, "call.connected", scenario_id=body.scenario_id))
        return public(value)

    @api.get("/sessions/{sid}")
    async def get(sid: UUID):
        return public(load(sid))

    @api.post('/sessions/{sid}/open', dependencies=[Depends(serialize_mutation)])
    async def open_card(sid: UUID):
        """Record opening the incoming DDS row; receipt is confirmed by a primary status."""
        value = load(sid)
        if value.get('exercise_mode') != 'actions' or value['status'] == 'Завершена':
            return public(value)
        if not value.get('opened_at'):
            value['opened_at'] = now()
            owner = value.get('owner_service')
            if owner and value.get('service_states', {}).get(owner, {}).get('status') == 'Добавлена':
                value['service_states'][owner].update(status='Получена службой', at=value['opened_at'])
            persist(value, 'card.opened')
        return public(value)

    @api.put("/sessions/{sid}/card", dependencies=[Depends(serialize_mutation)])
    async def save(sid: UUID, body: SaveCard):
        value = load(sid)
        editable(value)
        if value["revision"] != body.revision:
            raise HTTPException(409, "Карточка изменена в другом окне. Откройте её заново.")
        previous = value["card"]
        if student_view(value)['card_locked']:
            raise HTTPException(409, 'Работы завершены; карточка доступна только для просмотра')
        card = body.card.model_dump()
        if value.get('exercise_mode') == 'actions':
            # Поля карточки 112 заполняет Служба 112. ДДС правит только свои
            # поля, а об ошибке сообщает в 112 по телефону (POST /error-reports).
            before = Card.model_validate(previous).model_dump()
            foreign = sorted(key for key in card if key not in DDS_EDITABLE_FIELDS
                             and card[key] != before.get(key))
            if foreign:
                raise HTTPException(403, 'Поля карточки 112 заполняет Служба 112. Об ошибке '
                                         'сообщите в 112 по телефону: ' + ', '.join(foreign))
        if card["classifier_id"]:
            try:
                record = resolve(card["classifier_id"], card["classifier_version"])
            except ValueError:
                raise HTTPException(422, "Запись классификатора не найдена или версия устарела. Выберите признаки заново.")
            if card["classifier_group"] != record["group_id"] or card["classifier_features"] != record["features"]:
                raise HTTPException(422, "Признаки не соответствуют выбранной записи классификатора")
            card["incident_type"] = record["incident_type"]
            value["classification"] = {"id": record["id"], "version": card["classifier_version"],
                                       "source_row": record["source_row"], "main_service": record["main_service"]}
        elif card["classifier_group"]:
            card["incident_type"] = ""
            value.pop("classification", None)
        else:
            value.pop("classification", None)
        if value.get('exercise_mode') == 'actions':
            # A DDS receives a registered card. Only the originating 112/VIS side
            # controls its recipients and their contact directory.
            if (card['services'] != previous['services'] or card['service_phones'] != previous.get('service_phones', {})
                    or card['recipient_affiliations'] != previous.get('recipient_affiliations', {})):
                raise HTTPException(403, 'Список получателей и их телефоны в ДДС доступны только для просмотра')
            value['routing'] = preview(previous)
        else:
            value["routing"] = preview(card)
            card['services'] = list(dict.fromkeys([*previous.get('services', []), *card['services'],
                                                 *(s['service'] for s in value['routing']['suggestions'])]))
        if len(card['services']) > 100:
            raise HTTPException(422, 'В карточке допускается не более 100 служб')
        for service_name in card['services']:
            if service_name not in value['service_states']:
                value['service_states'][service_name] = {'status': 'Добавлена', 'comment': '', 'at': now(), 'added_at': now()}
        value.setdefault('registered_at', now())
        value["card"] = card
        value["revision"] += 1
        value["status"] = "В работе"
        value["saved_at"] = now()
        changes = {k: {"before": previous.get(k), "after": v} for k, v in value["card"].items() if previous.get(k) != v}
        persist(value, "card.saved", changes)
        return public(value)

    @api.post("/sessions/{sid}/messages", dependencies=[Depends(serialize_mutation)])
    async def message(sid: UUID, body: Message):
        value = load(sid)
        editable(value)
        if value["transport"] != "text":
            raise HTTPException(409, "В режиме SIP говорите через учебный телефон")
        if not value.get('text_input_allowed', True):
            raise HTTPException(409, 'Преподаватель отключил текстовый ввод для этого сценария')
        if value.get('exercise_mode') == 'actions':
            raise HTTPException(409, 'В этом упражнении работайте с карточкой и реагированием служб, без звонка')
        if not body.text.strip():
            raise HTTPException(422, "Введите реплику")
        request = event(sid, "operator.utterance", text=body.text, utterance_id=str(body.message_id))
        request["event_id"] = str(body.message_id)
        await engine.handle(str(sid), request)
        return public(load(sid))

    @api.post("/sessions/{sid}/services", dependencies=[Depends(serialize_mutation)])
    async def service(sid: UUID, body: ServiceAction):
        """Update own DDS; the first accepted/refused decision closes the 30-second receipt clock."""
        value = load(sid)
        editable(value)
        if body.service not in value["card"]["services"]:
            raise HTTPException(422, "Служба отсутствует в сохранённой карточке")
        body = body.model_copy(update={'status': STATUS_ALIASES.get(body.status, body.status)})
        # Диспетчер ведёт статусы только своей ДДС. Остальные назначенные службы
        # он видит, но их состояние приходит от них самих — так в реальном АРМ.
        owner = value.get('owner_service')
        if owner and body.service != owner:
            raise HTTPException(403, f'Вы ведёте только свою службу: {owner}. '
                                     'Состояние остальных служб приходит от них.')
        without_brigade = (not value.get('assigned_crew') and no_brigade_completion(
            body.service, value['service_states'].get(body.service, {}).get('status'),
            body.status, body.comment))
        if (not without_brigade and value.get('exercise_mode') == 'actions' and body.status in PROGRESS_STATUSES
                and value.get('crew_options') and not value.get('assigned_crew')):
            raise HTTPException(409, 'Сначала выберите реагирующую бригаду')
        # Статусы хода работ отражают доклад с места, а не желание обучающегося
        # прокликать цепочку. Каждый такой статус открывает соответствующая
        # оперативная вводная.
        gated = unlocked_statuses(value)
        if not without_brigade and gated is not None and body.status in PROGRESS_STATUSES and body.status not in gated:
            raise HTTPException(409, f'Статус «{body.status}» ещё не подтверждён с места. '
                                     'Дождитесь сообщения от службы.')
        # Перед закрытием работ в карточку вносится результат: после этого
        # статуса она больше не редактируется.
        if body.status == 'Работы завершены' and len(body.comment.strip()) < 10:
            raise HTTPException(422, 'Перед завершением работ внесите в комментарий результат: '
                                     'что сделано и чем закончилось.')
        # В карточке ДДС запись — это статус и текст: карточка отработана,
        # когда все статусы активированы и поля заполнены.
        own_record = value.get('exercise_mode') == 'actions' and owner and body.service == owner
        if own_record and body.status in PROGRESS_STATUSES and body.status not in sequential(
                value, body.service, allowed_statuses(body.service, value['service_states'].get(body.service, {}).get('status'))):
            raise HTTPException(409, f'Статус «{body.status}» нельзя проставить, пропустив предыдущие этапы')
        if own_record and not body.comment.strip():
            raise HTTPException(422, f'Заполните комментарий к статусу «{body.status}»')
        payload = body.model_dump(mode='json')
        if body.message_id:
            for e in value['events']:
                if e['type'] == 'service.updated' and e['detail'].get('message_id') == str(body.message_id):
                    if e['detail'].get('request') != payload:
                        raise HTTPException(409, 'Идентификатор действия уже использован')
                    return public(value)
        old = value['service_states'].get(body.service, {})
        try:
            status = validate_transition(body.service, old.get('status'), body.status, body.comment)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        order_number = body.order_number.strip()
        action_at = now()
        if (value.get('exercise_mode') == 'actions' and body.service == owner
                and (status in ('Принята', 'Не принята') or without_brigade) and not value.get('receipt_decided_at')):
            value['receipt_decided_at'] = action_at
            if status == 'Принята':
                value['accepted_at'] = action_at
        if own_record:
            # Статус ставится только в открытой карточке; открытие через API без
            # отдельного запроса считается состоявшимся в момент записи.
            value.setdefault('opened_at', action_at)
            value.setdefault('first_record_at', action_at)
        value['service_states'][body.service] = {
            **old,
            'status': status,
            'order_number': order_number,
            'comment': body.comment.strip(),
            'at': action_at,
        }
        persist(value, "service.updated", {
            **payload,
            'status': status,
            'order_number': order_number,
            'request': payload,
        })
        return public(value)

    @api.post('/sessions/{sid}/crew', dependencies=[Depends(serialize_mutation)])
    async def assign_crew(sid: UUID, body: AssignCrew):
        value = load(sid)
        editable(value)
        if value.get('exercise_mode') != 'actions' or not value.get('owner_service'):
            raise HTTPException(409, 'Назначение бригады доступно в режиме ДДС')
        for event in value['events']:
            if event['type'] == 'crew.assigned' and event['detail'].get('message_id') == str(body.message_id):
                if event['detail'].get('request') != body.model_dump(mode='json'):
                    raise HTTPException(409, 'Идентификатор назначения уже использован')
                return public(value)
        if value.get('assigned_crew'):
            raise HTTPException(409, 'Бригада уже назначена для этой карточки')
        if value['service_states'].get(value['owner_service'], {}).get('status') != 'Принята':
            raise HTTPException(409, 'Сначала примите карточку своей ДДС')
        selected = next((option for option in value.get('crew_options', [])
                         if option['id'] == body.crew_id), None)
        if not selected:
            raise HTTPException(422, 'Выберите бригаду из списка занятия')
        if body.decision_by == 'leadership' and not body.decision_note:
            raise HTTPException(422, 'Укажите, кто из руководства принял решение')
        value['assigned_crew'] = {**selected, 'decision_by': body.decision_by,
                                  'decision_note': body.decision_note, 'at': now()}
        persist(value, 'crew.assigned', {**value['assigned_crew'],
                                         'message_id': str(body.message_id),
                                         'request': body.model_dump(mode='json')})
        return public(value)

    @api.post('/sessions/{sid}/notifications', dependencies=[Depends(serialize_mutation)])
    async def notification(sid: UUID, body: NotificationAction):
        value = load(sid)
        editable(value)
        if body.service not in value['card']['services']:
            raise HTTPException(422, 'Сначала сохраните службу в карточке')
        if value.get('exercise_mode') == 'actions' and not value['card'].get('service_phones', {}).get(body.service):
            raise HTTPException(409, 'У службы нет номера телефона для связи')
        payload = body.model_dump(mode='json')
        for item in value.setdefault('notifications', []):
            if item['message_id'] == str(body.message_id):
                if any(item[k] != v for k, v in payload.items()):
                    raise HTTPException(409, 'Идентификатор оповещения уже использован')
                return public(value)
        if len(value['notifications']) >= 200:
            raise HTTPException(409, 'Достигнут лимит оповещений карточки')
        value['notifications'].append({**payload, 'at': now(), 'operator': (actor.get() or {}).get('display_name', 'Учебный оператор')})
        persist(value, 'notification.recorded', payload)
        return public(value)

    @api.post('/sessions/{sid}/error-reports', dependencies=[Depends(serialize_mutation)])
    async def error_report(sid: UUID, body: ErrorReport):
        """ДДС сообщает в Службу 112 об ошибке в полученной карточке.

        Правильные сведения диспетчер узнаёт из звонка бригады, затем звонит по
        обычному телефону в 112 и сообщает об ошибке. Сама карточка 112 при этом не меняется: её поля ведёт 112.
        """
        value = load(sid)
        editable(value)
        if value.get('exercise_mode') != 'actions':
            raise HTTPException(409, 'Сообщение в 112 об ошибке доступно в режиме ДДС')
        payload = body.model_dump(mode='json')
        reports = value.setdefault('error_reports', [])
        for item in reports:
            if item['message_id'] == str(body.message_id):
                if any(item[key] != payload[key] for key in payload):
                    raise HTTPException(409, 'Идентификатор сообщения уже использован')
                return public(value)
        if len(reports) >= 20:
            raise HTTPException(409, 'Достигнут лимит сообщений об ошибках по карточке')
        reports.append({**payload, 'at': now(),
                        'card_value': value['card'].get(body.field, ''),
                        'operator': (actor.get() or {}).get('display_name', 'Учебный оператор')})
        persist(value, 'card.error_reported', payload)
        return public(value)

    @api.post('/sessions/{sid}/forward', dependencies=[Depends(serialize_mutation)])
    async def forward(sid: UUID, body: ForwardCard):
        """Перенаправление происшествия в другую службу.

        Это третье действие диспетчера наряду с приёмом и отказом. Технически служба добавляется в карточку, как при ручном
        дополнении списка оповещения, но событие пишется отдельным типом с
        обоснованием: иначе перенаправление неотличимо от обычного добавления.
        """
        value = load(sid)
        editable(value)
        if value.get('exercise_mode') == 'actions':
            raise HTTPException(403, 'Диспетчер ДДС не изменяет список получателей карточки')
        if not value.get('saved_at'):
            raise HTTPException(409, 'Сначала сохраните карточку')
        payload = body.model_dump(mode='json')
        for event in value['events']:
            if event['type'] == 'card.forwarded' and event['detail'].get('message_id') == str(body.message_id):
                if event['detail'] != payload:
                    raise HTTPException(409, 'Идентификатор перенаправления уже использован')
                return public(value)
        if body.service in value['card']['services']:
            raise HTTPException(422, 'Служба уже есть в карточке')
        if len(value['card']['services']) >= 100:
            raise HTTPException(422, 'В карточке допускается не более 100 служб')
        value['card']['services'].append(body.service)
        value['service_states'][body.service] = {'status': 'Добавлена', 'comment': body.reason,
                                                 'at': now(), 'added_at': now()}
        value['revision'] += 1
        persist(value, 'card.forwarded', payload)
        return public(value)

    @api.post('/sessions/{sid}/processed', dependencies=[Depends(serialize_mutation)])
    async def processed(sid: UUID):
        value = load(sid)
        editable(value)
        if not value['revision']:
            raise HTTPException(409, 'Сначала сохраните карточку')
        if not value.get('processed_at'):
            value['processed_at'] = now()
            persist(value, 'card.processed')
        return public(value)

    @api.post('/sessions/{sid}/unproductive', dependencies=[Depends(serialize_mutation)])
    async def unproductive(sid: UUID, body: UnproductiveCall):
        """Закрыть карточку как нерезультативный вызов.

        Заполнять нечего: признак ставится, карточка сохраняется и сразу
        завершается с отметкой о проверке. Норматив реакции при этом измеряется
        как и у любой другой карточки — нажатие кнопки и есть действие
        оператора.
        """
        value = load(sid)
        # Проверка повтора идёт до editable: карточка уже завершена этим же
        # запросом, и повторная отправка обязана вернуть тот же результат, а не
        # конфликт.
        for event in value['events']:
            if event['type'] == 'card.unproductive' and event['detail'].get('message_id') == str(body.message_id):
                return public(value)
        editable(value)
        if value['card']['services']:
            raise HTTPException(409, 'В карточке уже назначены службы: нерезультативным вызов не считается')
        value['card'][body.kind] = True
        value['revision'] += 1
        value['saved_at'] = value.get('saved_at') or now()
        value.setdefault('registered_at', value['saved_at'])
        value['checked_by'] = {'role': 'student', 'reason': 'Нерезультативный вызов'}
        persist(value, 'card.unproductive', body.model_dump(mode='json'))
        return await complete_session(sid, {'role': 'student', 'user_id': (actor.get() or {}).get('id'),
                                            'reason': 'Нерезультативный вызов'})

    def report_heard(call: dict | None, *, ended: bool = False) -> bool:
        """Первая реплика бригады проиграна полностью; ended — и звонок завершён."""
        if not call:
            return False
        report_state = store.load(call['session_id'])
        opening = next((reply for reply in report_state.get('replies', {}).values()
                        if reply and reply.get('type') == 'caller.reply'), None)
        return (bool(opening) and report_state.get('playback', {}).get(opening['payload']['reply_id']) == 'played'
                and (not ended or bool(report_state.get('ended'))))

    REPEAT_OPENING = 'Алло, 112? Звоню снова по тому же вызову. '

    async def operator_updates(sid: UUID, value: dict, fresh: list[dict], scenario: dict):
        """Вводные в полном цикле 112. В карточку сами они не пишутся.

        * Статус службы («Наряд сформирован…») приходит из ДДС автоматически на
          плитку службы — только в сохранённую карточку, куда служба назначена
          (§11.1 инструкции по заведению карточки).
        * Новое от заявителя — это повторный вызов с того же АОН: звонок на
          учебный телефон после окончания первого разговора (в текстовом режиме —
          новое сообщение в диалоге). Сведения оператор вносит сам, дополнением.
        """
        changed = False
        for item in fresh:
            if item.get('unlocks_status'):
                service = next((name for name in value.get('service_states', {})
                                if name and name in item.get('source', '')), None) or scenario.get('owner_service')
                if not value.get('revision') or service not in value.get('service_states', {}):
                    continue
                value['service_states'][service] = {**value['service_states'][service],
                                                    'status': item['unlocks_status'], 'comment': item['text'], 'at': now()}
                persist(value, 'service.status_received', {'id': item['id'], 'service': service,
                                                          'status': item['unlocks_status'], 'comment': item['text']})
                changed = True
            elif await repeat_call(value, item, scenario):
                changed = True
        return public(load(sid) if changed else value)

    async def repeat_call(value: dict, item: dict, scenario: dict) -> bool:
        main = store.load(str(value['id']))
        opening = REPEAT_OPENING + item['text'].rstrip('.') + '.'
        if value.get('transport') != 'sip' or not value.get('sip_extension'):
            main.setdefault('messages', []).append({'role': 'assistant', 'content': opening})
            store.save(str(value['id']), main)
            persist(value, 'caller.repeat_call', {'id': item['id'], 'transport': 'text'})
            return True
        # Линия одна: повторный вызов поступает, когда первый разговор закончен.
        if not value.get('call_id') or not main.get('ended'):
            return False
        if any(not store.load(call['session_id']).get('ended') for call in value.get('repeat_calls', {}).values()):
            return False
        source = main.get('scenario') or scenario
        # Тот же человек звонит снова: его слова из первого разговора держат роль
        # (кто звонит, кем приходится пострадавшему), модель их не придумывает.
        said = [m['content'][:300] for m in main.get('messages', []) if m.get('role') == 'assistant'][:3]
        repeat = {**source, 'opening': opening,
                  'known_facts': [*source.get('known_facts', []),
                                  *(['Это повторный звонок того же заявителя. В первом звонке вы говорили: '
                                     + ' / '.join(said)] if said else []),
                                  'Новое после первого звонка: ' + item['text']]}
        repeat_sid = str(uuid4())
        store.save(repeat_sid, {'step': 0, 'seq': 0, 'messages': [], 'replies': {}, 'ended': False, 'scenario': repeat})
        try:
            result = await voice('calls', 'POST', {'session_id': repeat_sid, 'extension': value['sip_extension'],
                                                   'mode': 'auto', 'scenario_id': value['scenario_id'],
                                                   **citizen_caller(value)})
        except HTTPException as error:
            if error.status_code in (409, 429, 503):
                return False  # Линия занята или голос недоступен: повтор при следующем опросе.
            raise
        value.setdefault('repeat_calls', {})[item['id']] = {'session_id': repeat_sid, 'call_id': result['call_id'],
                                                           'started_at': now()}
        persist(value, 'caller.repeat_call', {'id': item['id'], 'transport': 'sip', 'call_id': result['call_id']})
        return True

    @api.post('/sessions/{sid}/updates', dependencies=[Depends(serialize_mutation)])
    async def situation_updates(sid: UUID):
        """Доставить вводные, срок которых наступил.

        Момент срабатывания считается от выдачи карточки по её же секундомеру,
        поэтому одна и та же карточка ведёт себя одинаково у всех обучающихся
        группы и не зависит ни от модели, ни от того, когда рабочее место
        обратилось за обновлением. Повторный вызов ничего не дублирует.
        """
        value = load(sid)
        if value['status'] == 'Завершена':
            return public(value)
        scenario = store.load(str(sid)).get('scenario') or {}
        planned = scenario.get('updates') or []
        if not planned:
            return public(value)
        delivered = {event['detail'].get('id') for event in value['events']
                     if event['type'] in ('situation.update', 'service.status_received', 'caller.repeat_call')}
        fresh = [item for item in planned
                 if item['id'] not in delivered and report_due(value, item, planned)]
        if value.get('exercise_mode') != 'actions':
            return await operator_updates(sid, value, fresh, scenario)
        if value.get('exercise_mode') == 'actions' and value.get('sip_extension'):
            # The progress fact remains locked until the student hears the
            # separate SIP report from the response team. Отдельного
            # подтверждения ТЗ не требует: доклад, прозвучавший до конца,
            # вносится в карточку, когда разговор с бригадой завершён.
            heard = [item for item in planned if item['id'] not in delivered
                     and report_heard((value.get('field_report_calls') or {}).get(item['id']), ended=True)]
            for item in heard:
                call = value['field_report_calls'][item['id']]
                value.setdefault('situation_updates', []).append({**item, 'at': now(), 'transport': 'sip'})
                persist(value, 'situation.update', {**item, 'transport': 'sip', 'call_id': call['call_id'],
                                                    'recorded': 'call_ended'})
            return public(load(sid) if heard else value)
        if not fresh:
            return public(value)
        for item in fresh:
            value.setdefault('situation_updates', []).append({**item, 'at': now()})
            persist(value, 'situation.update', item)
        return public(load(sid))

    @api.post('/inbox/poll')
    async def poll_inbox():
        user = actor.get()
        if not user:
            raise HTTPException(403, 'Требуется обучающийся')
        rows = store.db.execute("SELECT body FROM workspace WHERE json_text(body,'student_id')=? "
                                "ORDER BY json_text(body,'created_at') DESC LIMIT 100", (user['id'],)).fetchall()
        result = []
        phone_started = False
        for (encoded,) in rows:
            value = json.loads(encoded)
            if value['status'] == 'Завершена':
                continue
            if value.get('lesson_id'):
                try:
                    lesson = lesson_load(value['lesson_id'])
                except HTTPException as error:
                    if error.status_code != 404:
                        raise
                    # The student was removed from an old lesson. Its saved
                    # card remains in history, but must not break live polling
                    # for the current lesson.
                    continue
                if lesson['state'] != 'running':
                    continue
            async with coordinated('workspace-session', value['id']):
                refreshed = await situation_updates(UUID(value['id']))
                pending = refreshed.get('pending_phone_reports') or []
                ready = not value.get('crew_options') or bool(value.get('assigned_crew'))
                if pending and ready and not phone_started and not pending[0].get('call_id'):
                    try:
                        refreshed = await call_field_report(UUID(value['id']), pending[0]['id'])
                        phone_started = True
                    except HTTPException as error:
                        if error.status_code not in (409, 429, 503):
                            raise
                        refreshed['phone_error'] = error.detail
                result.append(refreshed)
        return result

    @api.post('/sessions/{sid}/progress', dependencies=[Depends(serialize_mutation)])
    async def request_progress(sid: UUID):
        value = load(sid)
        editable(value)
        if value.get('exercise_mode') != 'actions' or not value.get('owner_service'):
            raise HTTPException(409, 'Запрос обстановки доступен в карточке ДДС')
        crew = value.get('assigned_crew')
        if not crew:
            raise HTTPException(409, 'Сначала назначьте реагирующую бригаду')
        if value.get('updates_anchor') in ('crew_assigned', 'crew_briefed') and update_elapsed(value, {'unlocks_status': 'progress'}) < 0:
            raise HTTPException(409, 'Сначала передайте бригаде адрес и задачу и завершите доклад')
        planned = (store.load(str(sid)).get('scenario') or {}).get('updates') or []
        delivered = {event['detail'].get('id') for event in value['events'] if event['type'] == 'situation.update'}
        # Only crew progress, never applicant messages or future facts.
        due = sorted((item for item in planned if item.get('unlocks_status') and item['id'] not in delivered
                      and report_due(value, item, planned)), key=lambda item: item['after_seconds'])
        pending = next((item for item in due if item['id'] not in delivered), None)
        if pending and value.get('sip_extension'):
            result = await call_field_report(sid, pending['id'])
            value = load(sid)
            persist(value, 'progress.requested', {'update_id': pending['id'], 'transport': 'sip'})
            return {**result, 'progress_message': 'Примите соединение на IP-телефоне и подтвердите прослушанный доклад.'}
        message = (pending or (due[-1] if due else {})).get('text', 'Бригада назначена. Новых сведений о ходе работ пока нет.')
        if not value.get('sip_extension'):
            if pending:
                value.setdefault('situation_updates', []).append({**pending, 'at': now(), 'transport': 'text'})
                persist(value, 'situation.update', {**pending, 'transport': 'text', 'direction': 'outgoing'})
            persist(value, 'progress.requested', {'transport': 'text', 'message': message})
            return {**public(value), 'progress_message': message}
        previous = value.get('progress_call')
        if previous:
            try:
                snapshot = await voice('calls/' + previous['call_id'])
                if snapshot.get('status') not in ('ended', 'failed'):
                    return {**public(value), 'progress_message': 'Разговор о ходе работ уже открыт на IP-телефоне.'}
            except HTTPException as error:
                if error.status_code != 404:
                    raise
        report_sid = str(uuid4())
        store.save(report_sid, {'step': 0, 'seq': 0, 'messages': [], 'replies': {}, 'ended': False,
                               'field_report': report_context(value, crew['leader'], message)})
        result = await voice('calls', 'POST', {'session_id': report_sid,
                                              'extension': value['sip_extension'], 'mode': 'auto',
                                              **caller_id(f"{crew['leader']}, {crew['id']}", crew.get('phone', ''))})
        value['progress_call'] = {'session_id': report_sid, 'call_id': result['call_id']}
        persist(value, 'progress.requested', {'transport': 'sip', 'call_id': result['call_id']})
        return {**public(value), 'progress_message': 'Примите соединение на IP-телефоне. Новых вводных этот ответ не открывает.'}

    @api.post('/sessions/{sid}/updates/{update_id}/call', dependencies=[Depends(serialize_mutation)])
    async def call_field_report(sid: UUID, update_id: str):
        value = load(sid)
        editable(value)
        if value.get('exercise_mode') != 'actions' or not value.get('sip_extension'):
            raise HTTPException(409, 'Телефонный доклад доступен только в SIP-занятии ДДС')
        if value.get('crew_options') and not value.get('assigned_crew'):
            raise HTTPException(409, 'Сначала назначьте бригаду')
        planned = (store.load(str(sid)).get('scenario') or {}).get('updates') or []
        item = next((entry for entry in planned if entry['id'] == update_id), None)
        if not item:
            raise HTTPException(404, 'Доклад не найден')
        if any(event['type'] == 'situation.update' and event['detail'].get('id') == update_id
               for event in value['events']):
            return public(value)
        if not report_due(value, item, planned):
            raise HTTPException(409, 'Доклад ещё не поступил: сначала отразите предыдущий доклад статусом')
        calls = value.setdefault('field_report_calls', {})
        if update_id in calls:
            previous = calls[update_id]
            report_state = store.load(previous['session_id'])
            opening = next((reply for reply in report_state.get('replies', {}).values()
                            if reply and reply.get('type') == 'caller.reply'), None)
            if opening and report_state.get('playback', {}).get(opening['payload']['reply_id']) == 'played':
                return public(value)
            try:
                snapshot = await voice('calls/' + previous['call_id'])
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                snapshot = {'status': 'failed'}  # Voice restarted; the call no longer exists.
            if snapshot.get('status') not in ('ended', 'failed'):
                return public(value)
            calls.pop(update_id)
        report_sid = str(uuid4())
        crew = value.get('assigned_crew') or {}
        source = f"{crew['leader']}, {crew['id']}" if crew else item['source']
        store.save(report_sid, {'step': 0, 'seq': 0, 'messages': [], 'replies': {}, 'ended': False,
                                'field_report': report_context(value, source, item['text'], crew.get('id', ''), update_id)})
        result = await voice('calls', 'POST', {'session_id': report_sid,
                                              'extension': value['sip_extension'], 'mode': 'auto',
                                              **caller_id(source, crew.get('phone', ''))})
        calls[update_id] = {'session_id': report_sid, 'call_id': result['call_id'], 'started_at': now()}
        persist(value, 'field_report.call_started', {'id': update_id, 'call_id': result['call_id']})
        return public(value)

    @api.post('/sessions/{sid}/updates/{update_id}/confirm', dependencies=[Depends(serialize_mutation)])
    async def confirm_field_report(sid: UUID, update_id: str):
        value = load(sid)
        editable(value)
        if any(event['type'] == 'situation.update' and event['detail'].get('id') == update_id
               for event in value['events']):
            return public(value)
        item = next((entry for entry in (store.load(str(sid)).get('scenario') or {}).get('updates', [])
                     if entry['id'] == update_id), None)
        call = (value.get('field_report_calls') or {}).get(update_id)
        if not item or not call:
            raise HTTPException(409, 'Сначала примите телефонный доклад')
        if not report_heard(call):
            raise HTTPException(409, 'Дождитесь окончания телефонного доклада')
        try:
            await voice('calls/' + call['call_id'] + '/hangup', 'POST', {})
        except HTTPException as error:
            if error.status_code != 404:
                raise
        value.setdefault('situation_updates', []).append({**item, 'at': now(), 'transport': 'sip'})
        persist(value, 'situation.update', {**item, 'transport': 'sip', 'call_id': call['call_id']})
        return public(value)

    @api.post('/sessions/{sid}/reminders', dependencies=[Depends(serialize_mutation)])
    async def reminder(sid: UUID, body: Reminder):
        """Напоминание по карточке. Срабатывает на рабочем месте обучающегося."""
        value = load(sid)
        editable(value)
        reminders = value.setdefault('reminders', [])
        for item in reminders:
            if item['message_id'] == str(body.message_id):
                return public(value)
        if len(reminders) >= 20:
            raise HTTPException(409, 'Достигнут лимит напоминаний карточки')
        payload = body.model_dump(mode='json')
        reminders.append({**payload, 'created_at': now()})
        persist(value, 'card.reminder_set', payload)
        return public(value)

    @api.post('/sessions/{sid}/links', dependencies=[Depends(serialize_mutation)])
    async def link_card(sid: UUID, body: LinkCard):
        value = load(sid)
        editable(value)
        target = load(body.target_id)
        if target['id'] == value['id']:
            raise HTTPException(422, 'Нельзя связать карточку с собой')
        links = value.setdefault('linked_cards', [])
        if not any(item['id'] == target['id'] for item in links):
            if len(links) >= 50:
                raise HTTPException(409, 'Достигнут лимит связей')
            links.append({'id': target['id'], 'number': target['number']})
            persist(value, 'card.linked', {'id': target['id'], 'number': target['number']})
        return public(value)

    def citizen_caller(value):
        """Вызов 112 от гражданина: на телефоне — «Гражданин» и номер АОН из сценария.
        Имя заявителя не показывается: оператор узнаёт его в разговоре."""
        scenario = store.load(str(value['id'])).get('scenario') or {}
        phone = next((p for p in (value['card'].get('phone'), (value.get('initial_card') or {}).get('phone'),
                                  (scenario.get('prefilled_card') or {}).get('phone')) if p), '')
        if not phone:
            fact = next((f for f in scenario.get('known_facts', []) if re.match(r'\s*Телефон заявителя\s*:', str(f))), '')
            phone = fact.split(':', 1)[1] if fact else ''
        return caller_id('Гражданин', phone)

    # Клиент голосового модуля общий с докладом дежурному: см. voice_client.py.
    async def voice(path, method='GET', body=None):
        return await voice_request(path, method, body)

    @api.post("/sessions/{sid}/call", dependencies=[Depends(serialize_mutation)])
    async def call(sid: UUID):
        value = load(sid)
        editable(value)
        if value["transport"] != "sip":
            raise HTTPException(409, "Создайте занятие в режиме SIP")
        if value['call_id']:
            return public(value)
        try:
            result = await voice("calls", "POST", {"session_id": str(sid), "extension": value.get('sip_extension', '201'), "mode": "auto", "scenario_id": value["scenario_id"],
                                                   **citizen_caller(value)})
        except HTTPException:
            persist(value, 'call.failed', {'message': 'Не удалось подключить учебный звонок. Проверьте Voice и SIP-номер.'})
            raise
        value = load(sid)
        value["call_id"] = result["call_id"]
        persist(value, "call.requested")
        return public(value)

    @api.get("/sessions/{sid}/call")
    async def call_status(sid: UUID):
        value = load(sid)
        if not value["call_id"]:
            raise HTTPException(404, "Звонок ещё не создан")
        result=await voice("calls/" + value["call_id"])
        ended=store.load(str(sid)).get('last_call_end',{})
        if ended.get('call_id')==value['call_id']:result.setdefault('reason',ended.get('reason'))
        result['recovery_allowed']=value['status'] != 'Завершена' and result.get('status') in ('ended','failed') and result.get('reason') in ('ari_disconnected','media_disconnected','asterisk_media_ended','backend_unavailable','service_restart')
        result['recovery']=value.get('call_recovery')
        return result

    @api.post('/sessions/{sid}/call/recover',dependencies=[Depends(serialize_mutation)])
    async def recover_call(sid:UUID,expected_call_id:UUID=Query(...)):
        value=load(sid);editable(value)
        if value['transport']!='sip' or not value.get('call_id'):raise HTTPException(409,'Нет звонка для восстановления')
        if value['call_id']!=str(expected_call_id):return public(value)
        snapshot=await call_status(sid)
        if not snapshot['recovery_allowed']:raise HTTPException(409,'Причина завершения не допускает автоматический повтор звонка')
        recovery=value.setdefault('call_recovery',{'started_at':now(),'attempts':0,'state':'recovering'})
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(recovery['started_at'])).total_seconds()
        if recovery['attempts']>=3 or age>30:
            recovery['state']='exhausted';persist(value,'call.recovery.exhausted')
            raise HTTPException(409,'Автовосстановление ограничено тремя попытками и 30 секундами')
        recovery['attempts']+=1;recovery['state']='recovering'
        persist(value,'call.recovery.started',{'previous_call_id':value['call_id'],'reason':snapshot.get('reason'),'attempt':recovery['attempts']})
        await engine.handle(str(sid),event(sid,'session.resume',previous_call_id=value['call_id']))
        try:
            result=await voice('calls','POST',{'session_id':str(sid),'extension':value.get('sip_extension','201'),
                'mode':'auto','scenario_id':value['scenario_id'],**citizen_caller(value)})
        except HTTPException:
            persist(value,'call.recovery.failed',{'attempt':recovery['attempts']})
            raise
        # Keep one logical training session/card; each phone attempt has its own ID/recording.
        value.setdefault('call_history',[]).append({'call_id':value['call_id'],'reason':snapshot.get('reason')})
        value['call_id']=result['call_id'];recovery['state']='redialing'
        persist(value,'call.recovery.redialed',{'call_id':result['call_id']})
        return public(value)

    @instructor.post('/sessions/{sid}/finish', dependencies=[Depends(serialize_mutation)])
    async def teacher_finish(sid: UUID, body: TeacherFinish):
        user = actor.get()
        if not user or user['role'] != 'teacher':
            raise HTTPException(403, 'Требуется преподаватель')
        return await complete_session(sid, {'role': 'teacher', 'user_id': user['id'],
                                          'name': user['display_name'], 'reason': body.reason})

    @api.post("/sessions/{sid}/finish", dependencies=[Depends(serialize_mutation)])
    async def finish(sid: UUID):
        user = actor.get()
        return await complete_session(sid, {'role': 'student', 'user_id': user['id'] if user else None})

    async def stop_session_calls(value):
        extra_calls = {item['call_id'] for item in value.get('field_report_calls', {}).values()
                       if item.get('call_id')}
        if value.get('call_id'):
            extra_calls.add(value['call_id'])
        if getattr(store, 'briefings_available', False):
            for (encoded,) in store.db.execute('SELECT body FROM briefings WHERE session_id=?', (str(value['id']),)).fetchall():
                briefing = json.loads(encoded)
                if briefing.get('state') == 'open' and briefing.get('call_id'):
                    extra_calls.add(briefing['call_id'])
        if value.get('progress_call'):
            extra_calls.add(value['progress_call']['call_id'])
        extra_calls |= {call['call_id'] for call in value.get('repeat_calls', {}).values() if call.get('call_id')}
        for call_id in extra_calls:
            try:
                await voice('calls/' + call_id + '/hangup', 'POST', {})
            except HTTPException as error:
                if error.status_code != 404:
                    raise

    async def complete_session(sid, completed_by):
        value = load(sid)
        if value["status"] == "Завершена":
            return public(value)
        if completed_by['role'] == 'student' and value.get('exercise_mode') == 'actions':
            missing = unfinished_dds(value, value.get('dds_expectation'))
            if missing:
                raise HTTPException(409, 'Завершите работу по карточке: ' + ', '.join(missing))
        await stop_session_calls(value)
        await engine.handle(str(sid), event(sid, "call.ended"))
        value = load(sid)
        value["status"] = "Завершена"
        value['completed_by'] = completed_by
        value["finished_at"] = now()
        value["elapsed_seconds"] = round((datetime.now(timezone.utc) - datetime.fromisoformat(value["created_at"])).total_seconds())
        value["checks"] = [{"field": key, "passed": bool(value["card"][key])} for key in ("caller_name", "street", "house", "description", "incident_type", "services")]
        dds_card = value.get('exercise_mode') == 'actions' and value.get('owner_service')
        # Норматив 30 секунд в ДДС — от появления карточки до её открытия.
        reaction_at = value.get('opened_at') if dds_card else value.get('first_action_at')
        if value.get('exercise_mode') == 'actions' and value.get('opened_at'):
            value['opening_seconds'] = max(0, round((datetime.fromisoformat(value['opened_at'])
                - datetime.fromisoformat(value['created_at'])).total_seconds()))
        if dds_card and value.get('first_record_at'):
            value['first_record_seconds'] = max(0, round(
                (datetime.fromisoformat(value['first_record_at'])
                 - datetime.fromisoformat(value['created_at'])).total_seconds()))
        if reaction_at:
            value['response_seconds'] = max(0, round(
                (datetime.fromisoformat(reaction_at)
                 - datetime.fromisoformat(value['created_at'])).total_seconds()))
        assessment = store.load(str(sid)).get("evaluation_rubric", {"revision": 0, "rubric": None})
        value["evaluation"] = evaluate(assessment["rubric"], value["card"], value["elapsed_seconds"],
                                       value.get('response_seconds'))
        apply_default_norms(value)
        # Смысловая доводка идёт после детерминированной оценки и только в плюс:
        # см. semantic_grading.py. Сбой модели оставляет оценку как есть.
        await semantic_review(value["evaluation"])
        # Ошибки ручного ввода считаются отдельным числом: преподаватель просил
        # видеть их количество в отчёте, а опечатки в адресе — критическими.
        comments = [event['detail'].get('comment', '') for event in value['events']
                    if event['type'] == 'service.updated'
                    and (not value.get('owner_service') or event['detail'].get('service') == value['owner_service'])]
        value['grammar'] = grammar_report(value['card'], assessment['rubric'], comments)
        # Разбор решений диспетчера: отдельно от эталона по полям, потому что в
        # основном режиме поля приходят заполненными и ничего не измеряют.
        decisions = dds_decision_review(value, value.get('dds_expectation'))
        if decisions:
            # Факты, записанные своими словами, засчитываются по смыслу — только в плюс.
            decisions = await semantic_review_dds(decisions, value.get('dds_expectation') or {})
            value['dds_review'] = decisions
        value["evaluation"]["rubric_revision"] = assessment["revision"]
        finish_event = {'seq': len(value['events']) + 1, 'type': 'session.finished', 'at': value['finished_at'], 'detail': completed_by}
        policy_evaluation = dict(value['evaluation'])
        if value.get('exercise_mode') == 'actions' and decisions:
            policy_evaluation['score_percent'] = decisions['score_percent']
        value['policy_result'] = evaluate_policy(store.load(str(sid)).get('assessment_policy'), policy_evaluation, [*value['events'], finish_event])
        if value.get('exercise_mode') == 'actions':
            value['action_report'] = {'changed_fields': [key for key in value['card'] if value['card'][key] != value['initial_card'].get(key)],
                                      'service_actions': sum(e['type'] == 'service.updated' for e in value['events']),
                                      'note': 'Отчёт фиксирует действия. Порядок проверяется только при наличии правил преподавателя; содержательную правильность реагирования оценивает преподаватель.'}
        persist(value, "session.finished", completed_by)
        return public(value)

    @api.get("/sessions/{sid}/report")
    async def report(sid: UUID):
        value = load(sid)
        if value['status'] != 'Завершена':
            raise HTTPException(409, 'Отчёт доступен после завершения занятия')
        if 'evaluation' not in value:
            raise HTTPException(404, 'Для этого ранее завершённого занятия отчёт не формировался')
        return value['evaluation']

    @api.post('/sessions/{sid}/restart', status_code=201)
    @instructor.post('/sessions/{sid}/restart', status_code=201)
    async def restart_attempt(sid: UUID):
        source = load(sid)
        user = actor.get()
        if not user:
            raise HTTPException(403)
        async with AsyncExitStack() as stack:
            if source.get('lesson_id'):
                await stack.enter_async_context(coordinated('lesson', source['lesson_id']))
            await stack.enter_async_context(coordinated('workspace-session', sid))
            source = load(sid)
            if source.get('restarted_to'):
                return public(load(source['restarted_to']))
            lesson = lesson_load(source['lesson_id']) if source.get('lesson_id') else None
            if lesson and lesson['state'] != 'running':
                raise HTTPException(409, 'Занятие остановлено. Преподаватель может начать его заново в кабинете.')
            if lesson and source['status'] == 'Завершена':
                others = [c for c in lesson_cards(lesson['id'], source['student_id']) if c['status'] != 'Завершена']
                if len(others) >= lesson.get('parallel_cards', 1):
                    raise HTTPException(409, 'Сначала завершите или начните заново текущую открытую карточку')
            student = accounts.get_user(source['student_id'])
            if not student or not student.get('active'):
                raise HTTPException(409, 'Учётная запись ученика неактивна')
            if not lesson:
                assignment = learning.assignment_for_student(source['assignment_id'], source['scenario_id'], student)
            snapshot = store.load(str(sid))
            if not snapshot.get('scenario'):
                raise HTTPException(409, 'Нет исходного сценария для повтора')
            if (lesson or assignment).get('practice_with_hints'):
                require_approved(snapshot['scenario'])
            if source['status'] != 'Завершена':
                await stop_session_calls(source)
                await engine.handle(str(sid), event(sid, 'call.ended'))
                source = load(sid)
                source.update(status='Завершена', finished_at=now(), attempt_outcome='restarted',
                              completed_by={'role': user['role'], 'user_id': user['id'], 'reason': 'Начата новая попытка'})
                persist(source, 'session.restart_requested', {'user_id': user['id']})
            template = None
            if source.get('exercise_mode') == 'actions':
                template = {'card': source['initial_card'], 'scenario': snapshot['scenario'],
                            'source_kind': source.get('source_kind', 'scenario'),
                            'assessment_policy': snapshot.get('assessment_policy')}
            token = actor.set(student)
            try:
                created = await create_card(CreateSession(scenario_id=source['scenario_id'], assignment_id=source.get('assignment_id'),
                    transport=source['transport'], workstation=(source.get('registration') or {}).get('workstation', '')),
                    lesson, template, snapshot['scenario'],
                    {**(snapshot.get('evaluation_rubric') or {'revision': 0, 'rubric': None}),
                     'assessment_policy': snapshot.get('assessment_policy')}, restart_source=source)
            finally:
                actor.reset(token)
            source['restarted_to'] = created['id']
            persist(source, 'session.restarted', {'new_session_id': created['id'], 'user_id': user['id']})
            return created

    def lesson_load(lid):
        row = store.db.execute('SELECT body FROM lessons WHERE id=?', (str(lid),)).fetchone()
        if not row:
            raise HTTPException(404, 'Занятие группы не найдено')
        value = json.loads(row[0])
        user = actor.get()
        if not user or (user['role'] == 'teacher' and value['teacher_id'] != user['id']) or (user['role'] == 'student' and user['id'] not in value.get('members', [])):
            raise HTTPException(404, 'Занятие группы не найдено')
        return value

    def lesson_save(value, kind, detail=None):
        value['events'].append({'at': now(), 'type': kind, 'user_id': actor.get()['id'],
                                **({'detail': detail} if detail else {})})
        with store.db:
            store.db.execute('INSERT INTO lessons VALUES (?,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body', (value['id'], json.dumps(value, ensure_ascii=False)))

    def lesson_cards(lid, student_id=None):
        rows = store.db.execute("SELECT body FROM workspace WHERE json_text(body,'lesson_id')=? ORDER BY json_text(body,'created_at'),id", (str(lid),)).fetchall()
        cards = [json.loads(row[0]) for row in rows]
        return [c for c in cards if student_id is None or c.get('student_id') == student_id]

    @instructor.get('/categories')
    async def lesson_categories():
        return CATEGORIES

    @instructor.post('/lessons', status_code=201)
    async def create_lesson(body: CreateLesson):
        user = actor.get()
        if not user or not learning or not learning._group(body.group_id, user['id']):
            raise HTTPException(404, 'Группа не найдена')
        group = learning._group(body.group_id, user['id'])
        # Телефония в режиме ДДС нужна: карточка приходит данными, но связь
        # дальше — с дежурным своей службы — идёт голосом. Прежний запрет
        # закрывал именно основной сценарий.
        # Учебный номер назначается и в текстовом занятии. В работе диспетчера
        # ДДС карточка приходит данными, а доклад дежурному службы — это
        # отдельный исходящий звонок с его рабочего телефона. Запрет на номера
        # в текстовом занятии закрывал именно этот, основной сценарий.
        if any(uid not in group['member_ids'] for uid in body.sip_extensions):
            raise HTTPException(422, 'Назначайте SIP-номера только участникам группы')
        if len(set(body.sip_extensions.values())) != len(body.sip_extensions):
            raise HTTPException(422, 'Каждому студенту нужен отдельный SIP-номер')
        ids = list(dict.fromkeys(body.scenario_ids))
        categories = list(dict.fromkeys(body.category_ids))
        selection = body.model_dump()
        if (categories or body.difficulty or body.dds_profile) and not ids and body.mode in ('fill', 'mixed'):
            for s in store.list_scenarios():
                owner = store.db.execute('SELECT teacher_id FROM scenario_owners WHERE scenario_id=?', (s['id'],)).fetchone()
                if s['enabled'] and (not categories or s.get('category_id', 'other') in categories) and matches(s, selection) and (not owner or owner[0] == user['id']):
                    ids.append(s['id'])
            if len(ids) > 20:
                raise HTTPException(422, 'Найдено более 20 сценариев. Уточните фильтры или выберите сценарии явно.')
        if body.mode in ('fill', 'mixed') and not ids:
            raise HTTPException(422, 'Выберите сценарии для заполнения')
        prefilled_ids = list(dict.fromkeys(body.prefilled_scenario_ids))
        if (body.mode in ('actions', 'mixed') and not prefilled_ids and not body.source_session_ids
                and (categories or body.difficulty or body.dds_profile)):
            # Ролевая модель: в ленту ДДС попадают только события её профиля.
            for s in store.list_scenarios():
                owner = store.db.execute('SELECT teacher_id FROM scenario_owners WHERE scenario_id=?', (s['id'],)).fetchone()
                if (s['enabled'] and s.get('prefilled_card') and s.get('owner_service')
                        and (not categories or s.get('category_id', 'other') in categories)
                        and matches(s, selection) and (not owner or owner[0] == user['id'])):
                    prefilled_ids.append(s['id'])
            if len(prefilled_ids) > 20:
                raise HTTPException(422, 'Найдено более 20 готовых карточек. Уточните фильтры или выберите карточки явно.')
            body = body.model_copy(update={'prefilled_scenario_ids': prefilled_ids})
        if body.dds_profile:
            for scenario_id in prefilled_ids:
                scenario = store.scenario(scenario_id)
                if scenario and not matches(scenario, selection):
                    raise HTTPException(422, 'Готовая карточка не относится к выбранному профилю ДДС')
        if body.mode in ('actions', 'mixed') and not (body.source_session_ids or body.prefilled_scenario_ids):
            raise HTTPException(422, 'Выберите завершённые карточки для действий')
        if body.mode == 'fill' and (body.source_session_ids or body.prefilled_scenario_ids) or body.mode == 'actions' and ids:
            raise HTTPException(422, 'Источники не соответствуют режиму занятия')
        for scenario_id in ids:
            check_scenario_visibility(scenario_id)
            scenario = store.scenario(scenario_id)
            if not scenario or not scenario['enabled']:
                raise HTTPException(422, 'Выберите доступные сценарии')
            if categories and scenario.get('category_id', 'other') not in categories:
                raise HTTPException(422, 'Сценарий не относится к выбранным категориям')
            if not matches(scenario, selection):
                raise HTTPException(422, 'Сценарий не соответствует сложности или профилю ДДС')
        templates = []
        for scenario_id in dict.fromkeys(body.prefilled_scenario_ids):
            check_scenario_visibility(scenario_id)
            scenario = store.scenario(scenario_id)
            if not scenario or not scenario['enabled'] or categories and scenario.get('category_id', 'other') not in categories:
                raise HTTPException(422, 'Сценарий готовой карточки недоступен в выбранных категориях')
            if not matches(scenario, selection):
                raise HTTPException(422, 'Готовая карточка не соответствует сложности или профилю ДДС')
            try:
                card = prefilled_from_scenario(scenario)
            except ValueError as exc:
                raise HTTPException(422, f'Проверьте готовую карточку сценария: {exc}') from None
            templates.append({'card': card, 'scenario': scenario, 'source_kind': 'scenario', 'assessment_policy': policy_for(store, scenario_id, user['id'])})
        for source_id in dict.fromkeys(body.source_session_ids):
            source = load(source_id)
            if source['status'] != 'Завершена':
                raise HTTPException(422, 'Используйте только завершённые карточки')
            scenario = store.load(str(source_id)).get('scenario')
            if not scenario:
                raise HTTPException(422, 'У исходной карточки отсутствует снимок сценария')
            if categories and scenario.get('category_id', 'other') not in categories:
                raise HTTPException(422, 'Готовая карточка не относится к выбранным категориям')
            if not matches(scenario, selection):
                raise HTTPException(422, 'Исходная карточка не соответствует сложности или профилю ДДС')
            card = Card.model_validate(source['card']).model_dump()
            try:
                preview(card)
            except ValueError:
                raise HTTPException(422, 'Классификатор исходной карточки устарел')
            templates.append({'card': card, 'scenario': scenario, 'source_kind': 'student_card', 'assessment_policy': policy_for(store, source['scenario_id'], user['id'])})
        if body.allow_mode_switch and body.mode != 'mixed':
            raise HTTPException(422, 'Переключение режима доступно в смешанном занятии: '
                                     'нужны и сценарии вызова, и готовые карточки')
        available = set(ids) | {item['scenario']['id'] for item in templates}
        if body.practice_with_hints:
            for scenario in [store.scenario(sid) for sid in ids] + [t['scenario'] for t in templates]:
                require_approved(scenario)
        unknown = [sid for sid in body.student_scenarios.values() if sid not in available]
        if unknown:
            raise HTTPException(422, 'Адресное задание ссылается на сценарий вне занятия')
        value = {**body.model_dump(mode='json'), 'templates': templates, 'scenario_ids': ids, 'id': str(uuid4()), 'teacher_id': user['id'],
                 'state': 'planned', 'members': [], 'created_at': now(), 'events': []}
        lesson_save(value, 'lesson.created')
        return value

    @instructor.put('/lessons/{lid}/phones')
    async def configure_lesson_phones(lid: UUID, body: LessonPhones):
        async with coordinated('lesson', lid):
            lesson = lesson_load(lid)
            if lesson['state'] not in ('planned', 'running'):
                raise HTTPException(409, 'Настройка телефонов доступна до завершения занятия')
            group = learning._group(lesson['group_id'], actor.get()['id'])
            members = lesson.get('members') or group['member_ids']
            if set(body.sip_extensions) != set(members) or len(set(body.sip_extensions.values())) != len(members):
                raise HTTPException(422, 'Назначьте каждому участнику отдельный SIP-номер')
            active = [c for c in lesson_cards(lid) if c['status'] != 'Завершена']
            async with AsyncExitStack() as locks:
                for item in sorted(active, key=lambda c: c['id']):
                    await locks.enter_async_context(coordinated('workspace-session', item['id']))
                active = [load(c['id']) for c in active]
                for item in active:
                    if item.get('call_id') or item.get('field_report_calls') or item.get('progress_call'):
                        raise HTTPException(409, 'Телефон уже использовался в активной карточке. Завершите её перед перенастройкой.')
                    if item.get('exercise_mode') != 'actions' and store.load(item['id']).get('messages'):
                        raise HTTPException(409, 'Уже начатый диалог 112 нельзя переключить на другой канал')
                    if getattr(store, 'briefings_available', False):
                        briefs = store.db.execute('SELECT body FROM briefings WHERE session_id=?', (item['id'],)).fetchall()
                        if any(json.loads(row[0]).get('transport') == 'sip' and json.loads(row[0]).get('state') == 'open' for row in briefs):
                            raise HTTPException(409, 'Сначала завершите открытый телефонный доклад')
                with store.db:
                    lesson.update(transport='sip', sip_extensions=body.sip_extensions)
                    lesson_save(lesson, 'lesson.phones_configured')
                    for item in active:
                        item['sip_extension'] = body.sip_extensions[item['student_id']]
                        if item.get('exercise_mode') != 'actions':
                            item['transport'] = 'sip'
                        persist(item, 'phone.configured', {'sip_extension': item['sip_extension']})
            return {'id': str(lid), 'transport': 'sip', 'sip_extensions': body.sip_extensions}

    @instructor.get('/lessons')
    async def teacher_lessons():
        user = actor.get()
        if not user:
            raise HTTPException(403)
        rows = store.db.execute("SELECT body FROM lessons WHERE json_text(body,'teacher_id')=? ORDER BY json_text(body,'created_at') DESC,id DESC", (user['id'],)).fetchall()
        return [{**json.loads(row[0]), 'cards': [{'id': c['id'], 'student_id': c['student_id'], 'status': c['status'], 'number': c['number']} for c in lesson_cards(json.loads(row[0])['id'])]} for row in rows]

    @instructor.post('/lessons/{lid}/start')
    async def start_lesson(lid: UUID):
        async with coordinated('lesson', lid):
            value = lesson_load(lid)
            if value['state'] == 'running':
                return value
            if value['state'] != 'planned':
                raise HTTPException(409, 'Завершённое занятие нельзя запустить повторно')
            group = learning._group(value['group_id'], actor.get()['id'])
            members = [uid for uid in group['member_ids'] if accounts.get_user(uid).get('active')]
            if not members:
                raise HTTPException(409, 'В группе нет активных студентов')
            if value.get('transport') == 'sip' and any(uid not in value.get('sip_extensions', {}) for uid in members):
                raise HTTPException(409, 'Не всем студентам назначены SIP-номера. Подготовьте занятие заново.')
            scenarios = [store.scenario(sid) or {} for sid in value['scenario_ids']]
            scenarios += [t['scenario'] for t in value.get('templates', [])]
            if value.get('practice_with_hints'):
                for scenario in scenarios:
                    require_approved(scenario)
            if any(not s.get('text_input_allowed', True) for s in scenarios):
                if any(uid not in value.get('sip_extensions', {}) for uid in members) or (value.get('scenario_ids') and value.get('transport') != 'sip'):
                    raise HTTPException(409, 'Сценарий запрещает текстовый ввод: настройте телефоны всех участников занятия')
            snapshots = []
            for scenario_id in value['scenario_ids']:
                scenario = store.scenario(scenario_id)
                if not scenario or not scenario['enabled'] or value.get('category_ids') and scenario.get('category_id', 'other') not in value['category_ids']:
                    raise HTTPException(409, 'Сценарии изменились после подготовки. Подготовьте занятие заново.')
                if not matches(scenario, value):
                    raise HTTPException(409, 'Сложность или профиль сценария изменились. Подготовьте занятие заново.')
                snapshots.append({'scenario': scenario, 'rubric': {**rubric_for(scenario_id, actor.get()['id']), 'assessment_policy': policy_for(store, scenario_id, actor.get()['id'])}})
            if not snapshots and not value.get('templates'):
                raise HTTPException(409, 'Пул карточек пуст')
            value['fill_snapshots'] = snapshots
            value.update(state='running', members=members, started_at=now())
            lesson_save(value, 'lesson.started')
            return value

    @api.get('/lessons')
    async def student_lessons():
        user = actor.get()
        if not user:
            raise HTTPException(403)
        values = [json.loads(r[0]) for r in store.db.execute("SELECT body FROM lessons ORDER BY json_text(body,'created_at') DESC,id DESC")]
        result = []
        for v in values:
            planned_member = v['state'] == 'planned' and store.db.execute('SELECT 1 FROM group_members WHERE group_id=? AND student_id=?', (v['group_id'], user['id'])).fetchone()
            if planned_member or user['id'] in v.get('members', []):
                cards = lesson_cards(v['id'], user['id'])
                completed = sum(c['status'] == 'Завершена' and not c.get('restarted_to') for c in cards)
                open_ids = [c['id'] for c in cards if c['status'] != 'Завершена']
                active = open_ids[0] if open_ids else None
                restart_session_id = next((c['id'] for c in reversed(cards) if not c.get('restarted_to')), None)
                result.append({'id': v['id'], 'title': v['title'], 'state': v['state'], 'cards_per_student': v['cards_per_student'],
                               'mode': v.get('mode', 'fill'), 'category_ids': v.get('category_ids', []),
                               'transport': v.get('transport', 'text'),
                               'sip_extension': v.get('sip_extensions', {}).get(user['id']),
                               'difficulty': v.get('difficulty'), 'dds_profile': v.get('dds_profile'),
                               'completed': completed, 'active_session_id': active,
                               'restart_session_id': restart_session_id,
                               'active_session_ids': open_ids,
                               'parallel_cards': v.get('parallel_cards', 1),
                               'guided_step': v.get('guided_step') if v.get('practice_with_hints') else None,
                               'practice_with_hints': v.get('practice_with_hints', False),
                               'allow_mode_switch': v.get('allow_mode_switch', False),
                               'exhausted': v['cards_per_student'] is not None and completed >= v['cards_per_student']})
        return result

    @api.post('/lessons/{lid}/next')
    async def next_lesson_card(lid: UUID, body: NextCard | None = None):
        async with coordinated('lesson', lid):
            value = lesson_load(lid)
            if value['state'] != 'running':
                raise HTTPException(409, 'Преподаватель ещё не запустил или уже завершил занятие')
            cards = lesson_cards(lid, actor.get()['id'])
            cards = [c for c in cards if not c.get('restarted_to')]
            predecessor = str(body.after_session_id) if body and body.after_session_id else None
            if predecessor:
                previous = next((c for c in cards if c['id'] == predecessor), None)
                if not previous or previous['status'] != 'Завершена':
                    raise HTTPException(409, 'Предыдущая карточка не завершена или не принадлежит этому занятию')
                following = next((c for c in cards if c.get('previous_session_id') == predecessor), None)
                if following:
                    return public(following)
            open_cards = [c for c in cards if c['status'] != 'Завершена']
            if len(open_cards) >= value.get('parallel_cards', 1):
                # Лимит одновременных карточек достигнут: возвращается самая
                # ранняя открытая, чтобы обучающийся закрывал их по очереди.
                return public(open_cards[0])
            if value['cards_per_student'] is not None and len(cards) >= value['cards_per_student']:
                raise HTTPException(409, 'Все карточки занятия выполнены')
            fills = value.get('fill_snapshots')
            if fills is None:
                fills = [{'scenario': store.scenario(sid), 'rubric': rubric_for(sid, value['teacher_id'])} for sid in value['scenario_ids'] if (store.scenario(sid) or {}).get('enabled')]
            choices = [(f['scenario']['id'], None, f) for f in fills]
            choices.extend((t['scenario']['id'], t, None) for t in value.get('templates', []))
            if not choices:
                raise HTTPException(409, 'В занятии нет доступных сценариев')
            requested_mode = body.mode if body else None
            if requested_mode:
                if not value.get('allow_mode_switch'):
                    raise HTTPException(409, 'Преподаватель не разрешил менять режим в этом занятии')
                # fill — полный цикл с приёмом вызова, actions — готовая карточка ДДС.
                wanted = [choice for choice in choices
                          if (choice[1] is not None) == (requested_mode == 'actions')]
                if not wanted:
                    raise HTTPException(409, 'В занятии нет материала для выбранного режима')
                choices = wanted
            targeted = value.get('student_scenarios', {}).get(actor.get()['id'])
            addressed = [choice for choice in choices if choice[0] == targeted] if targeted else []
            pool = addressed or choices
            advice = None
            if not addressed and value.get('adaptive_difficulty'):
                history = [attempt_view(card) for card in cards]
                advice = recommend(value.get('difficulty') or 'basic', history)
                # Уровень меняется только если в занятии есть подходящие сценарии.
                suited = [choice for choice in pool
                          if ((choice[2]['scenario'] if choice[2] else choice[1]['scenario'])
                              .get('difficulty', 'basic')) == advice['level']]
                pool = suited or pool
                if not suited and advice['changed']:
                    advice = {**advice, 'changed': False,
                              'reason': 'В занятии нет сценариев рекомендованного уровня'}
            scenario_id, template, frozen = random.choice(pool)
            # Готовая карточка приходит данными, а SIP используется для
            # отдельного исходящего доклада. Не звоним от имени заявителя.
            created = await create_card(CreateSession(scenario_id=scenario_id,
                                                  transport='text' if template else value.get('transport', 'text'),
                                                  workstation=(body.workstation if body else '')), value, template,
                                        frozen['scenario'] if frozen else None, frozen['rubric'] if frozen else None)
            card = load(created['id'])
            card.update(previous_session_id=predecessor, lesson_position=len(cards)+1, lesson_title=value['title'])
            if advice:
                card['difficulty_advice'] = advice
            persist(card, 'lesson.card_issued', {'position': len(cards)+1,
                                                 **({'difficulty_advice': advice} if advice else {})})
            return public(card)

    @instructor.put('/lessons/{lid}/guided-step')
    async def set_guided_step(lid: UUID, body: GuidedStep):
        async with coordinated('lesson', lid):
            value = lesson_load(lid)
            user = actor.get()
            if not user or user['role'] != 'teacher':
                raise HTTPException(403, 'Требуется преподаватель')
            if body.step is not None and not value.get('practice_with_hints'):
                raise HTTPException(409, 'Подсказки не разрешены для этого занятия')
            value['guided_step'] = body.step
            lesson_save(value, 'lesson.guided_step', {'step': body.step})
            return {'id': value['id'], 'guided_step': body.step}

    @instructor.put('/lessons/{lid}/practice')
    async def set_practice_policy(lid: UUID, body: PracticePolicy):
        async with coordinated('lesson', lid):
            value = lesson_load(lid)
            if body.practice_with_hints:
                for scenario in ([t['scenario'] for t in value.get('templates', [])] +
                                 ([t['scenario'] for t in value['fill_snapshots']] if value.get('fill_snapshots') else
                                  [store.scenario(sid) for sid in value.get('scenario_ids', [])]) +
                                 [store.load(c['id']).get('scenario') for c in lesson_cards(lid) if c['status'] != 'Завершена']):
                    require_approved(scenario)
            value['practice_with_hints'] = body.practice_with_hints
            if not body.practice_with_hints:
                value['guided_step'] = None
            lesson_save(value, 'lesson.practice_changed', body.model_dump())
            for card in lesson_cards(lid):
                if card['status'] != 'Завершена':
                    async with coordinated('workspace-session', card['id']):
                        fresh = load(card['id'])
                        fresh['practice_with_hints'] = body.practice_with_hints
                        persist(fresh, 'practice.changed', body.model_dump())
            return {'id': value['id'], **body.model_dump()}

    @instructor.post('/lessons/{lid}/finish')
    async def finish_lesson(lid: UUID, body: TeacherFinish):
        async with coordinated('lesson', lid):
            value = lesson_load(lid)
            if value['state'] == 'finished':
                return value
            value.update(state='stopping', reason=body.reason)
            lesson_save(value, 'lesson.stopping')
            user = actor.get()
            failures = []
            for card in lesson_cards(lid):
                if card['status'] != 'Завершена':
                    async with coordinated('workspace-session', card['id']):
                        try:
                            await complete_session(card['id'], {'role': 'teacher', 'user_id': user['id'], 'name': user['display_name'], 'reason': body.reason})
                        except HTTPException:
                            failures.append(card['id'])
            if failures:
                raise HTTPException(503, 'Часть звонков не удалось завершить. Выдача остановлена; восстановите Voice и повторите завершение.')
            value.update(state='finished', finished_at=now())
            lesson_save(value, 'lesson.finished')
            return value

    @instructor.post('/lessons/{lid}/restart', status_code=201)
    async def restart_lesson(lid: UUID):
        """Пройти занятие заново с нуля: прежнее завершается (его результаты
        остаются в отчётах), создаётся и запускается такое же с новыми карточками."""
        source = lesson_load(lid)
        if source['state'] != 'finished':
            await finish_lesson(lid, TeacherFinish(reason='Занятие начато заново'))
        settings = CreateLesson.model_validate(
            {key: source[key] for key in CreateLesson.model_fields if key in source}).model_dump(mode='json')
        value = {**settings, 'templates': source.get('templates', []), 'scenario_ids': source.get('scenario_ids', []),
                 'id': str(uuid4()), 'teacher_id': actor.get()['id'], 'state': 'planned', 'members': [],
                 'created_at': now(), 'events': [], 'restarted_from': str(lid)}
        lesson_save(value, 'lesson.created')
        return await start_lesson(UUID(value['id']))

    @instructor.get('/lessons/{lid}/report')
    async def lesson_report(lid: UUID):
        value = lesson_load(lid)
        cards = lesson_cards(lid)
        participants = []
        for uid in value.get('members', []):
            account = accounts.get_user(uid) or {}
            own = [c for c in cards if c['student_id'] == uid]
            participants.append({'student_id': uid, 'display_name': account.get('display_name', 'Пользователь'),
                'workstation': value.get('workstations', {}).get(uid) or (own[0]['registration']['workstation']
                                                                          if own and own[0].get('registration') else None),
                'completed': sum(c['status'] == 'Завершена' for c in own),
                'grammar_errors': sum((c.get('grammar') or {}).get('errors', 0) for c in own),
                'critical_grammar_errors': sum((c.get('grammar') or {}).get('critical_errors', 0) for c in own),
                'cards': [{'id': c['id'], 'number': c['number'], 'title': c['title'], 'status': c['status'],
                           'exercise_mode': c.get('exercise_mode', 'fill'), 'elapsed_seconds': c.get('elapsed_seconds'),
                           'communication': communication_summary(store, c),
                           'attempt_number': c.get('attempt_number', 1), 'restarted_from': c.get('restarted_from'),
                           'restarted_to': c.get('restarted_to'), 'attempt_outcome': c.get('attempt_outcome'),
                           'response_seconds': c.get('response_seconds'),
                           'workstation': (c.get('registration') or {}).get('workstation'),
                           'timing': (c.get('evaluation') or {}).get('timing'),
                           'grammar': c.get('grammar'),
                           'score_percent': (c.get('dds_review') or c.get('evaluation') or {}).get('score_percent'),
                           'dds_review': c.get('dds_review'),
                           'action_report': c.get('action_report'), 'completed_by': c.get('completed_by')} for c in own]})
        return {'id': value['id'], 'title': value['title'], 'state': value['state'],
                'started_at': value.get('started_at'), 'finished_at': value.get('finished_at'), 'reason': value.get('reason'),
                'summary': {'participants': len(participants), 'issued': len(cards), 'completed': sum(c['status'] == 'Завершена' for c in cards)},
                'participants': participants, 'events': value['events']}

    @instructor.get('/lessons/{lid}/live')
    async def live_lesson(lid: UUID):
        value = lesson_load(lid)
        cards = lesson_cards(lid)
        members = value.get('members', [])
        if value['state'] == 'planned':
            members = learning._group(value['group_id'], actor.get()['id'])['member_ids']
        participants = []
        observed = datetime.now(timezone.utc)
        for uid in members:
            own = [c for c in cards if c['student_id'] == uid]
            active = next((c for c in reversed(own) if c['status'] != 'Завершена'), None)
            completed = [c for c in own if c['status'] == 'Завершена']
            current = None
            if active:
                current = {key: active.get(key) for key in ('id', 'number', 'title', 'created_at', 'transport', 'call_id')}
                current.update(elapsed_seconds=max(0, int((observed - datetime.fromisoformat(active['created_at'])).total_seconds())),
                    last_event=active['events'][-1] if active['events'] else None,
                    provider_error=store.load(active['id']).get('provider_error'))
            latest = completed[-1] if completed else None
            participants.append({'student_id': uid, 'display_name': (accounts.get_user(uid) or {}).get('display_name', 'Пользователь'),
                'completed': len(completed), 'active_card': current,
                'latest_result': {'score_percent': (latest.get('dds_review') or latest.get('evaluation') or {}).get('score_percent'),
                                  'dds_review': latest.get('dds_review'),
                                  'policy_result': latest.get('policy_result')} if latest else None})
        return {'id': value['id'], 'title': value['title'], 'state': value['state'],
                'observed_at': observed.isoformat(), 'participants': participants}

    @api.post("/sessions/{sid}/ai-review")
    async def ai_review(sid: UUID):
        # Serialize duplicate requests. Ready/mock results are cached permanently;
        # only failed requests may retry. Existing grades and cards never change.
        value = load(sid)
        if value["status"] != "Завершена":
            raise HTTPException(409, "ИИ-разбор доступен после завершения занятия")
        async with coordinated('review', sid):
            value = load(sid)
            if value["status"] != "Завершена":
                raise HTTPException(409, "ИИ-разбор доступен после завершения занятия")
            if value.get("ai_review", {}).get("status") in ("ready", "mock"):
                return public(value)
            state = store.load(str(sid))
            config = configuration()
            try:
                guidance = guidance_examples(store, value.get('teacher_id'), value.get('dds_profile', 'general'))
                materials = material_context(store, value.get('teacher_id'), value.get('dds_profile', 'general'),
                                             value.get('group_id'), query=value['card'].get('description', '') + ' ' + value['card'].get('incident_type', ''))
                if value.get('exercise_mode') == 'actions' and value.get('owner_service'):
                    # В режиме ДДС ученик не заполняет карточку, а ведёт записи о
                    # реагировании: разбираются они, с цитатами и докладами бригады.
                    result = await asyncio.wait_for(review_dds_actions(value, value.get('dds_review'),
                                                                       guidance, materials), timeout=60)
                else:
                    result = await asyncio.wait_for(review_card(value["card"], state.get("scenario", {}),
                        state.get("evaluation_rubric", {}).get("rubric"), guidance, materials), timeout=45)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, asyncio.TimeoutError):
                result = {"status": "failed", "provider": config["provider"], "model": config["model"],
                          "error": "Не удалось получить проверяемый ИИ-разбор. Можно повторить запрос. Балл и карточка сохранены."}
            # Feedback and other card updates use workspace-session, and may run
            # while the provider request is in flight on another replica. Reload
            # and persist under that same aggregate lease to avoid overwriting them.
            async with coordinated('workspace-session', sid):
                value = load(sid)
                value["ai_review"] = {**result, "created_at": now(), "version": "advisory-v1"}
                persist(value, "review.completed" if result["status"] in ("ready", "mock") else "review.failed",
                        {"status": result["status"], "provider": result["provider"], "model": result["model"]})
                return public(value)

    combined = APIRouter()
    combined.include_router(api)
    combined.include_router(instructor)
    return combined
