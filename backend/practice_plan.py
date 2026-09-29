"""Teacher-authored coaching; no model calls and no future report disclosure."""
import hashlib
import json
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class PracticeStep(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    phase: Literal['accept', 'refused', 'assign', 'crew', 'superior', 'correction', 'update', 'processed', 'finish', 'wait', 'completed']
    update_id: str = Field('', max_length=32)
    title: str = Field(min_length=3, max_length=160)
    text: str = Field(min_length=3, max_length=2000)
    sample: str = Field('', max_length=1000)
    target: Literal['responseStatus', 'responseComment', 'crewSelect', 'openBriefing', 'openErrorReport', 'processed', 'finish', 'requestProgress', 'audit']


def fingerprint(scenario):
    value = {k: v for k, v in scenario.items() if k not in ('practice_approved_version', 'version', 'editable', 'practice_confirm')}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def approved(scenario):
    return bool(scenario and scenario.get('practice_plan') and scenario.get('practice_approved_version') == fingerprint(scenario))


def require_approved(scenario):
    if not approved(scenario):
        raise HTTPException(409, 'Сначала подготовьте и утвердите подсказки в редакторе сценария: ' + (scenario or {}).get('title', 'сценарий'))


def validate_plan(scenario):
    steps = scenario.get('practice_plan') or []
    keys = [(s['phase'], s.get('update_id', '')) for s in steps]
    if len(keys) != len(set(keys)):
        raise ValueError('Условие показа подсказки не должно повторяться')
    updates = {u['id'] for u in scenario.get('updates', []) if u.get('unlocks_status')}
    if any((s['phase'] == 'update' and s.get('update_id') not in updates) or
           (s['phase'] != 'update' and s.get('update_id')) for s in steps):
        raise ValueError('Подсказка должна ссылаться на существующий доклад со статусом')
    return steps


def draft(scenario):
    steps = []
    def add(phase, title, text, target, **extra):
        steps.append(dict(phase=phase, update_id='', title=title, text=text, sample='', target=target, **extra))
    refused = (scenario.get('dds_expectation') or {}).get('should_accept') is False
    add('accept', 'Проверьте поступившую карточку',
        'Проверьте территорию и принадлежность происшествия своей службе. ' +
        ('Выберите «Не принята», укажите причину и кому передана информация.' if refused else
         'Подтвердите получение статусом «Принята» и сохраните комментарий. Не указывайте ещё не выполненные действия.'), 'responseStatus')
    add('refused', 'Проверьте отказ', 'Проверьте причину отказа и сведения о передаче информации, затем завершите карточку.', 'finish')
    add('assign', 'Назначьте бригаду', 'Выберите подходящую бригаду и укажите, кто принял решение. Сохраните назначение.', 'crewSelect')
    add('crew', 'Передайте задачу бригаде', 'Откройте доклад по телефону, выберите назначенную бригаду. Передайте адрес и существенные обстоятельства из карточки. Дождитесь подтверждения и завершите доклад.', 'openBriefing')
    add('superior', 'Доложите руководителю', 'Выберите начальника дежурной смены в окне доклада. Сообщите адрес, обстановку и фактически принятые меры. После подтверждения сохраните доклад.', 'openBriefing')
    add('correction', 'Уточните данные карточки', 'Сопоставьте полученные сведения с карточкой. Сообщите в 112 об обнаруженной ошибке, укажите уточнённое значение и источник.', 'openErrorReport')
    for update in scenario.get('updates', []):
        if update.get('unlocks_status'):
            steps.append(dict(phase='update', update_id=update['id'], title='Отразите доклад: ' + update['unlocks_status'],
                text='Получен доклад: «' + update['text'] + '». Выберите статус «' + update['unlocks_status'] + '», запишите существенные факты и сохраните. Итог работ вносится до закрытия редактирования.', sample='', target='responseComment'))
    add('processed', 'Отметьте отработку', 'Проверьте итоговый статус и результат. Нажмите «Отметить отработанным».', 'processed')
    add('finish', 'Завершите карточку', 'Завершите карточку. Если система показывает невыполненные действия, сначала выполните их.', 'finish')
    add('wait', 'Получите сведения о ходе работ', 'Ответьте на входящий звонок бригады либо уточните ход работ по телефону. Не меняйте статус до получения соответствующих сведений.', 'requestProgress')
    add('completed', 'Посмотрите результат', 'Откройте отчёт и разбор решений диспетчера.', 'audit')
    return steps


def hint(scenario, value):
    if not value.get('practice_with_hints') or value.get('exercise_mode') != 'actions' or not approved(scenario):
        return None
    events = value.get('events', [])
    status = value.get('service_states', {}).get(value.get('owner_service'), {}).get('status')
    briefs = [e.get('detail', {}) for e in events if e['type'] == 'notification.recorded' and e.get('detail', {}).get('source') == 'briefing']
    crew = any(b.get('counterpart') == 'crew' or b.get('crew_id') for b in briefs)
    superior = any(b.get('counterpart') == 'superior' or not b.get('counterpart') and not b.get('crew_id') for b in briefs)
    update_id = ''
    if value.get('status') == 'Завершена': phase = 'completed'
    elif not status or status in ('Добавлена', 'Получена службой'): phase = 'accept'
    elif status == 'Не принята': phase = 'refused'
    elif value.get('crew_options') and not value.get('assigned_crew') and not value.get('card_locked'): phase = 'assign'
    elif value.get('assigned_crew') and not crew and not value.get('card_locked'): phase = 'crew'
    elif not superior and not value.get('card_locked'): phase = 'superior'
    elif value.get('correction_evidence') and not value.get('error_reports') and not value.get('card_locked'): phase = 'correction'
    else:
        update = next((e for e in events if e['type'] == 'situation.update' and e.get('detail', {}).get('unlocks_status') and not any(
            a['type'] == 'service.updated' and a.get('detail', {}).get('service') == value.get('owner_service') and
            a.get('detail', {}).get('status') == e['detail']['unlocks_status'] and a['seq'] > e['seq'] for a in events)), None)
        if update and not value.get('card_locked'): phase, update_id = 'update', update['detail']['id']
        elif value.get('card_locked'): phase = 'finish' if value.get('processed_at') else 'processed'
        else: phase = 'wait'
    step = next((dict(s) for s in scenario['practice_plan'] if s['phase'] == phase and s.get('update_id', '') == update_id), None)
    if step:
        if phase == 'update' and value.get('text_input_allowed') is False:
            step['title'] = 'Отразите услышанное'
            step['text'] = 'Выберите соответствующий статус и запишите существенные сведения в комментарий.'
            step['sample'] = ''
        step['fill'] = 'responseComment' if step['target'] in ('responseStatus', 'responseComment') else None
    return step
