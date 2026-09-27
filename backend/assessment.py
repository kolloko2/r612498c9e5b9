"""Teacher-defined thresholds, append-only expert decisions and scoped learning statistics."""
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from cluster import Coordinator, ClusterUnavailable, LockUnavailable

EventType = Literal['card.saved', 'service.updated', 'notification.recorded', 'card.processed', 'card.linked', 'card.forwarded', 'call.requested', 'session.finished', 'crew.assigned', 'situation.update', 'field_report.call_started']


class SequenceStep(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
    label: str = Field(min_length=1, max_length=200)
    event_type: EventType
    service: str = Field('', max_length=160)
    status: str = Field('', max_length=80)

    @model_validator(mode='after')
    def valid_filters(self):
        if self.service and self.event_type not in ('service.updated', 'notification.recorded'):
            raise ValueError('service filter requires a service event')
        if self.status and self.event_type != 'service.updated':
            raise ValueError('status filter requires service.updated')
        return self


class AssessmentPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pass_score_percent: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    max_field_errors: int | None = Field(None, ge=0, le=30)
    max_sequence_errors: int = Field(0, ge=0, le=20)
    fail_on_timeout: bool = False
    steps: list[SequenceStep] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def configured(self):
        if len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError('step IDs must be unique')
        if self.pass_score_percent is None and self.max_field_errors is None and not self.fail_on_timeout and not self.steps:
            raise ValueError('configure at least one check or disable the policy with null')
        return self


class SavePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    policy: AssessmentPolicy | None


class ExpertDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: UUID
    revision: int = Field(ge=0)
    action: Literal['grade', 'revoke'] = 'grade'
    score_percent: float | None = Field(None, ge=0, le=100, allow_inf_nan=False)
    passed: bool | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def complete_decision(self):
        if self.action == 'grade' and (self.score_percent is None or self.passed is None):
            raise ValueError('expert grade requires both score and verdict')
        if self.action == 'revoke' and (self.score_percent is not None or self.passed is not None):
            raise ValueError('revocation must not include a grade')
        return self


def initialize(store):
    with store.db:
        store.db.execute('CREATE TABLE IF NOT EXISTS assessment_policies (teacher_id TEXT, scenario_id TEXT, revision INTEGER, body TEXT, PRIMARY KEY(teacher_id,scenario_id))')
        store.db.execute('CREATE TABLE IF NOT EXISTS assessment_policy_history (teacher_id TEXT, scenario_id TEXT, revision INTEGER, at TEXT, body TEXT, PRIMARY KEY(teacher_id,scenario_id,revision))')
        store.db.execute('CREATE TABLE IF NOT EXISTS expert_reviews (session_id TEXT, revision INTEGER, request_id TEXT, body TEXT, PRIMARY KEY(session_id,revision), UNIQUE(session_id,request_id))')


def policy_for(store, scenario_id, teacher_id):
    row = store.db.execute('SELECT revision,body FROM assessment_policies WHERE teacher_id=? AND scenario_id=?', (teacher_id or '', scenario_id)).fetchone()
    return {'revision': row[0], 'policy': json.loads(row[1])} if row else {'revision': 0, 'policy': None}


def evaluate_policy(snapshot, evaluation, events):
    """Match an ordered subsequence of successful persisted actions, not mouse clicks."""
    if not snapshot or not snapshot.get('policy'):
        return None
    policy = AssessmentPolicy.model_validate(snapshot['policy'])
    criteria = evaluation.get('criteria', [])
    configured = evaluation.get('status') == 'evaluated'
    field_errors = sum(not c['passed'] for c in criteria) if configured else None
    cursor = -1
    steps = []
    for step in policy.steps:
        indexes = [i for i, e in enumerate(events) if e['type'] == step.event_type
                   and (not step.service or e.get('detail', {}).get('service') == step.service)
                   and (not step.status or e.get('detail', {}).get('status') == step.status)]
        index = next((i for i in indexes if i > cursor), None)
        passed = index is not None
        if passed:
            cursor = index
        steps.append({'id': step.id, 'label': step.label, 'event_type': step.event_type,
                      'passed': passed, 'reason': 'matched' if passed else 'out_of_order' if indexes else 'missing',
                      'event_seq': events[index].get('seq', index + 1) if passed else None})
    sequence_errors = sum(not step['passed'] for step in steps)
    checks = []
    if policy.pass_score_percent is not None:
        score = evaluation.get('score_percent')
        checks.append({'id': 'score', 'passed': score >= policy.pass_score_percent if score is not None else None, 'actual': score, 'limit': policy.pass_score_percent})
    if policy.max_field_errors is not None:
        checks.append({'id': 'field_errors', 'passed': field_errors <= policy.max_field_errors if field_errors is not None else None, 'actual': field_errors, 'limit': policy.max_field_errors})
    if steps:
        checks.append({'id': 'sequence_errors', 'passed': sequence_errors <= policy.max_sequence_errors, 'actual': sequence_errors, 'limit': policy.max_sequence_errors})
    if policy.fail_on_timeout:
        timing = evaluation.get('timing', {})
        checks.append({'id': 'time', 'passed': timing.get('within_limit'), 'actual': timing.get('elapsed_seconds'), 'limit': timing.get('limit_seconds')})
    verdicts = [c['passed'] for c in checks]
    return {'version': 'policy-v1', 'policy_revision': snapshot['revision'],
            'passed': False if False in verdicts else None if None in verdicts else True,
            'field_errors': field_errors, 'sequence_errors': sequence_errors, 'checks': checks, 'steps': steps}


def expert_history(store, sid):
    history = [json.loads(r[0]) for r in store.db.execute('SELECT body FROM expert_reviews WHERE session_id=? ORDER BY revision', (sid,))]
    return {'revision': history[-1]['revision'] if history else 0,
            'current': history[-1] if history and history[-1]['action'] == 'grade' else None, 'history': history}


def effective(value, expert):
    if expert:
        return {'score_percent': expert['score_percent'], 'passed': expert['passed'], 'source': 'expert'}
    if value.get('exercise_mode') == 'actions' and value.get('dds_review'):
        review = value['dds_review']
        policy = value.get('policy_result')
        passed = review.get('passed')
        if policy is not None:
            if policy.get('passed') is False:
                passed = False
            elif policy.get('passed') is None and passed is True:
                passed = None
        return {'score_percent': review.get('score_percent'), 'passed': passed,
                'source': 'automatic'}
    return {'score_percent': (value.get('evaluation') or {}).get('score_percent'),
            'passed': (value.get('policy_result') or {}).get('passed'), 'source': 'automatic'}


def summary(rows):
    completed = [r for r in rows if r['status'] == 'Завершена']
    scores = [r['effective']['score_percent'] for r in completed if r['effective']['score_percent'] is not None]
    times = [r['elapsed_seconds'] for r in completed if r.get('elapsed_seconds') is not None]
    return {'attempts': len(rows), 'completed': len(completed), 'graded': len(scores),
            'passed': sum(r['effective']['passed'] is True for r in completed),
            'failed': sum(r['effective']['passed'] is False for r in completed),
            'unassessed': sum(r['effective']['passed'] is None for r in completed),
            'average_score': round(sum(scores) / len(scores), 2) if scores else None,
            'average_seconds': round(sum(times) / len(times), 2) if times else None}


def router(store, accounts, learning, authorize, coordinator=None):
    initialize(store)
    coordinator = coordinator or Coordinator.from_database(store.db)
    api = APIRouter(dependencies=[Depends(authorize)])
    teacher, student = accounts.require('teacher'), accounts.require('student')

    @asynccontextmanager
    async def coordinated(namespace, resource):
        try:
            async with coordinator.hold(namespace, str(resource)):
                yield
        except (LockUnavailable, ClusterUnavailable):
            raise HTTPException(503, 'Оценивание занято или кластерная координация недоступна') from None

    def owned_session(sid, user):
        row = store.db.execute('SELECT body FROM workspace WHERE id=?', (str(sid),)).fetchone()
        value = json.loads(row[0]) if row else None
        if not value or not learning.owns_session(user, value):
            raise HTTPException(404, 'Занятие не найдено')
        if value['status'] != 'Завершена':
            raise HTTPException(409, 'Оценивание доступно после завершения занятия')
        return value

    def scenario_access(sid, user):
        owner = store.db.execute('SELECT teacher_id FROM scenario_owners WHERE scenario_id=?', (sid,)).fetchone()
        if not store.scenario(sid) or owner and owner[0] != user['id']:
            raise HTTPException(404, 'Сценарий не найден')

    def assessment(sid, user):
        value = owned_session(sid, user)
        expert = expert_history(store, str(sid))
        return {'session_id': str(sid), 'title': value.get('title'),
                'student_name': (accounts.get_user(value.get('student_id')) or {}).get('display_name', 'Студент'),
                'automatic': value.get('evaluation'), 'dds_review': value.get('dds_review'),
                'policy_result': value.get('policy_result'),
                'expert': expert, 'effective': effective(value, expert['current'])}

    @api.get('/api/v1/instructor/scenarios/{sid}/assessment-policy')
    async def get_policy(sid: str, user=Depends(teacher)):
        scenario_access(sid, user)
        return policy_for(store, sid, user['id'])

    @api.put('/api/v1/instructor/scenarios/{sid}/assessment-policy')
    async def put_policy(sid: str, body: SavePolicy, user=Depends(teacher)):
        async with coordinated('assessment-policy', f"{user['id']}:{sid}"):
            scenario_access(sid, user)
            current = policy_for(store, sid, user['id'])
            if current['revision'] != body.revision:
                raise HTTPException(409, 'Настройки изменились. Перечитайте актуальную версию.')
            revision, at = body.revision + 1, datetime.now(timezone.utc).isoformat()
            encoded = body.policy.model_dump_json() if body.policy else 'null'
            with store.db:
                store.db.execute('INSERT INTO assessment_policies VALUES (?,?,?,?) ON CONFLICT (teacher_id,scenario_id) DO UPDATE SET revision=excluded.revision,body=excluded.body', (user['id'], sid, revision, encoded))
                store.db.execute('INSERT INTO assessment_policy_history VALUES (?,?,?,?,?)', (user['id'], sid, revision, at, encoded))
            return {'revision': revision, 'policy': json.loads(encoded)}

    @api.get('/api/v1/instructor/sessions/{sid}/assessment')
    async def teacher_assessment(sid: UUID, user=Depends(teacher)):
        return assessment(sid, user)

    @api.get('/api/v1/student/sessions/{sid}/assessment')
    async def student_assessment(sid: UUID, user=Depends(student)):
        return assessment(sid, user)

    @api.post('/api/v1/instructor/sessions/{sid}/assessment')
    async def expert_decision(sid: UUID, body: ExpertDecision, user=Depends(teacher)):
        async with coordinated('expert-review', sid):
            owned_session(sid, user)
            history = expert_history(store, str(sid))
            request = body.model_dump(mode='json')
            previous = next((r for r in history['history'] if r['request_id'] == str(body.request_id)), None)
            if previous:
                if previous['request'] != request:
                    raise HTTPException(409, 'Идентификатор запроса уже использован')
                return assessment(sid, user)
            if body.revision != history['revision']:
                raise HTTPException(409, 'Оценка уже изменена. Перечитайте историю.')
            if body.action == 'revoke' and not history['current']:
                raise HTTPException(409, 'Нет действующей экспертной оценки')
            if history['revision'] >= 100:
                raise HTTPException(409, 'Достигнут лимит 100 решений по работе')
            review = {**request, 'revision': history['revision'] + 1, 'teacher_id': user['id'],
                      'teacher_name': user['display_name'], 'at': datetime.now(timezone.utc).isoformat(), 'request': request}
            with store.db:
                store.db.execute('INSERT INTO expert_reviews VALUES (?,?,?,?)', (str(sid), review['revision'], str(body.request_id), json.dumps(review, ensure_ascii=False)))
            return assessment(sid, user)

    def statistics(user, group_id=None):
        roster = []
        if user['role'] == 'teacher':
            if group_id:
                group = learning._group(str(group_id), user['id'])
                if not group:
                    raise HTTPException(404, 'Группа не найдена')
                roster = group['students']
            rows = store.db.execute("SELECT body FROM workspace WHERE json_text(body,'teacher_id')=? ORDER BY json_text(body,'created_at'),id", (user['id'],))
        else:
            rows = store.db.execute("SELECT body FROM workspace WHERE json_text(body,'student_id')=? ORDER BY json_text(body,'created_at'),id", (user['id'],))
        values = [json.loads(r[0]) for r in rows]
        if group_id:
            values = [v for v in values if v.get('group_id') == str(group_id)]
        latest = {}
        identity_field = 'teacher_id' if user['role'] == 'teacher' else 'student_id'
        for row in store.db.execute("SELECT e.session_id,e.body FROM expert_reviews e JOIN workspace w ON w.id=e.session_id WHERE json_text(w.body,?)=? ORDER BY e.revision", (identity_field, user['id'])):
            latest[row[0]] = json.loads(row[1])
        for v in values:
            review = latest.get(v['id'])
            v['effective'] = effective(v, review if review and review['action'] == 'grade' else None)
        completed = sorted([v for v in values if v['status'] == 'Завершена'], key=lambda v: (v.get('finished_at', ''), v['id']))
        progress = [{'session_id': v['id'], 'title': v['title'], 'finished_at': v.get('finished_at'),
                     'difficulty': v.get('difficulty', 'basic'), 'dds_profile': v.get('dds_profile', 'general'), **v['effective']} for v in completed[-200:]]
        if user['role'] == 'teacher':
            for item, value in zip(progress, completed[-200:]):
                item['student_id'] = value['student_id']
                item['student_name'] = (accounts.get_user(value['student_id']) or {}).get('display_name', 'Студент')
        errors = {}
        # Матрица «сценарий × проверка» строится из тех же исходов, что и список
        # типичных ошибок: тепловая карта не вводит отдельного способа счёта.
        heat = {}
        def record(key, label, passed, scenario_id=None, title=None):
            entry = errors.setdefault(key, {'key': key, 'label': label, 'count': 0, 'attempts': 0})
            entry['attempts'] += 1
            entry['count'] += not passed
            if scenario_id is None:
                return
            cell = heat.setdefault((scenario_id, label), {
                'scenario_id': scenario_id, 'title': title, 'label': label, 'count': 0, 'attempts': 0})
            cell['attempts'] += 1
            cell['count'] += not passed
        for v in completed:
            for check in (v.get('dds_review') or {}).get('checks', []):
                if check.get('passed') is not None:
                    record('dds:'+v.get('teacher_id', '')+':'+v['scenario_id']+':'
                           +(v.get('dds_review') or {}).get('version', 'v1')+':'+check['id'],
                           check['label'], check['passed'], v['scenario_id'], v['title'])
            for c in (v.get('evaluation') or {}).get('criteria', []):
                record('field:'+v.get('teacher_id', '')+':'+v['scenario_id']+':'+str(v['evaluation'].get('rubric_revision', 0))+':'+c['id'],
                       c['label'], c['passed'], v['scenario_id'], v['title'])
            for s in (v.get('policy_result') or {}).get('steps', []):
                record('step:'+v.get('teacher_id', '')+':'+v['scenario_id']+':'+str(v['policy_result']['policy_revision'])+':'+s['id'],
                       s['label'], s['passed'], v['scenario_id'], v['title'])
        typical = [{**r, 'rate_percent': round(r['count'] * 100 / r['attempts'], 2)} for r in errors.values() if r['count']]
        typical.sort(key=lambda r: (-r['count'], r['key']))
        people = {p['id']: p['display_name'] for p in roster}
        if user['role'] == 'teacher':
            for v in values:
                uid = v['student_id']
                people.setdefault(uid, (accounts.get_user(uid) or {}).get('display_name', 'Студент'))
        scenarios = defaultdict(list)
        for v in values:
            scenarios[v['scenario_id']].append(v)
        cells = [{**cell, 'rate_percent': round(cell['count'] * 100 / cell['attempts'], 2)}
                 for cell in heat.values() if cell['attempts']]
        cells.sort(key=lambda c: (c['title'] or '', c['label']))
        heatmap = {
            'scenarios': [{'scenario_id': sid, 'title': title} for sid, title in
                          dict.fromkeys((c['scenario_id'], c['title']) for c in cells)],
            'checks': list(dict.fromkeys(c['label'] for c in cells)),
            'cells': cells,
        }
        return {'summary': summary(values), 'progress': progress,
                'students': [{'student_id': uid, 'display_name': name, **summary([v for v in values if v.get('student_id') == uid])} for uid, name in people.items()],
                'typical_errors': typical, 'error_heatmap': heatmap,
                'by_scenario': [{'scenario_id': sid, 'title': rows[-1]['title'], **summary(rows)} for sid, rows in scenarios.items()]}

    @api.get('/api/v1/instructor/statistics')
    async def teacher_statistics(group_id: UUID | None = Query(None), user=Depends(teacher)):
        return statistics(user, group_id)

    @api.get('/api/v1/student/statistics')
    async def student_statistics(user=Depends(student)):
        return statistics(user)

    def workbook_response(snapshot, scope):
        """Base64 envelope, like the PDF certificate: the BFF proxies JSON only."""
        import base64
        from reports import workbook_bytes
        data = workbook_bytes(snapshot, scope=scope)
        return {'filename': f'statistics-{datetime.now(timezone.utc).date().isoformat()}.xlsx',
                'content_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'file_base64': base64.b64encode(data).decode(), 'scope': scope}

    @api.get('/api/v1/instructor/statistics-workbook')
    async def teacher_statistics_workbook(group_id: UUID | None = Query(None), user=Depends(teacher)):
        scope = f'преподаватель, группа {group_id}' if group_id else 'преподаватель, все свои занятия'
        return workbook_response(statistics(user, group_id), scope)

    @api.get('/api/v1/student/statistics-workbook')
    async def student_statistics_workbook(user=Depends(student)):
        return workbook_response(statistics(user), 'обучающийся, собственные результаты')

    return api
