"""Сквозной разбор учебной попытки: задание → карточка → звонок → решение → разбор.

Собирает на одной странице то, что раньше было разнесено по отчёту занятия,
карточке, окну звонков и оценке: условия задания и нормативы, хронологию
действий с отметкой времени от поступления карточки, расшифровки всех звонков
попытки, карточку против эталона и итог оценки. Ничего не вычисляет заново —
только показывает сохранённые при завершении результаты.
"""
from __future__ import annotations

import json
from datetime import datetime

EVENT_LABELS = {
    'session.created': 'Карточка поступила', 'lesson.card_issued': 'Карточка выдана в занятии',
    'card.opened': 'Карточка открыта', 'card.saved': 'Карточка сохранена',
    'service.updated': 'Статус службы изменён', 'service.status_received': 'Получен статус службы',
    'service.vis_added': 'Служба добавлена из внешней системы', 'notification.recorded': 'Записана телефонограмма',
    'card.processed': 'Происшествие отмечено отработанным', 'card.linked': 'Карточка связана',
    'card.forwarded': 'Карточка перенаправлена', 'card.error_reported': 'Сообщение об ошибке в Службу 112',
    'card.unproductive': 'Вызов закрыт как нерезультативный', 'card.reminder_set': 'Установлено напоминание',
    'call.requested': 'Звонок на IP-телефон', 'call.failed': 'Звонок не состоялся',
    'call.recovery.started': 'Восстановление звонка', 'call.recovery.redialed': 'Повторный набор',
    'call.recovery.failed': 'Восстановление звонка не удалось', 'call.recovery.exhausted': 'Попытки дозвона исчерпаны',
    'caller.repeat_call': 'Повторный вызов заявителя', 'crew.assigned': 'Назначена бригада',
    'situation.update': 'Поступил доклад / вводная', 'field_report.call_started': 'Звонок от бригады',
    'progress.requested': 'Запрошен ход работ', 'teacher.feedback': 'Комментарий преподавателя',
    'practice.changed': 'Изменён режим подсказок', 'phone.configured': 'Назначен IP-телефон',
    'session.finished': 'Попытка завершена', 'session.restarted': 'Попытка начата заново',
    'session.restart_requested': 'Запрошен повтор попытки', 'review.completed': 'Готов ИИ-разбор',
}
STUDENT_EVENTS = {'card.opened', 'card.saved', 'service.updated', 'notification.recorded', 'card.processed',
                  'card.linked', 'card.forwarded', 'card.error_reported', 'card.unproductive', 'card.reminder_set',
                  'crew.assigned', 'progress.requested', 'session.finished'}
CARD_FIELDS = {
    'caller_name': 'Заявитель', 'phone': 'Телефон', 'city': 'Населённый пункт', 'street': 'Улица',
    'house': 'Дом', 'apartment': 'Квартира', 'description': 'Описание', 'incident_type': 'Тип происшествия',
    'services': 'Службы', 'injured': 'Пострадавшие', 'classifier_id': 'Код классификатора',
}


def _seconds(start: str | None, stamp: str | None) -> int | None:
    if not start or not stamp:
        return None
    return max(0, round((datetime.fromisoformat(stamp) - datetime.fromisoformat(start)).total_seconds()))


def _detail(event: dict) -> str:
    detail = event.get('detail') or {}
    parts = []
    for key in ('service', 'status', 'comment', 'recipient', 'crew', 'reason', 'name', 'text'):
        value = detail.get(key)
        if isinstance(value, dict):
            value = value.get('id') or value.get('title')
        if value:
            parts.append(str(value)[:200])
    return ' · '.join(parts)


def timeline(value: dict) -> list[dict]:
    start = value['created_at']
    rows = []
    for event in value.get('events', []):
        kind = event.get('type', '')
        rows.append({'at': event.get('at'), 'offset_seconds': _seconds(start, event.get('at')), 'type': kind,
                     'label': EVENT_LABELS.get(kind, kind), 'detail': _detail(event),
                     'actor': 'student' if kind in STUDENT_EVENTS else 'system'})
    return rows


def norms(value: dict) -> list[dict]:
    """Нормативы времени попытки: предел, факт и соблюдение."""
    timing = (value.get('evaluation') or {}).get('timing') or {}
    dds = value.get('exercise_mode') == 'actions' and value.get('owner_service')
    rows = []

    def add(label, limit, actual):
        if limit:
            rows.append({'label': label, 'limit_seconds': limit, 'actual_seconds': actual,
                         'within': None if actual is None else actual <= limit})
    if dds:
        add('Открытие карточки', value.get('time_limit_seconds') or 30, value.get('opening_seconds'))
        add('Первая запись (статус и текст)', 180, value.get('first_record_seconds'))
    else:
        add('Реакция (первое действие)', timing.get('response_limit_seconds') or value.get('response_limit_seconds'),
            value.get('response_seconds'))
        add('Лимит карточки', timing.get('limit_seconds') or value.get('time_limit_seconds'), value.get('elapsed_seconds'))
    return rows


def call_list(store, value: dict) -> list[dict]:
    """Все звонки попытки: заявитель, доклады, бригада, повторные вызовы; с расшифровкой."""
    calls = []

    def transcript(messages):
        return [{'role': 'student' if m.get('role') == 'user' else 'caller', 'text': m.get('content', '')}
                for m in messages if m.get('content')]
    main_ids = list(dict.fromkeys(i for i in [item.get('call_id') for item in value.get('call_history') or []]
                                  + [value.get('call_id')] if i))
    main_messages = store.load(value['id']).get('messages', [])
    kind = 'Вызов заявителя' if value.get('exercise_mode') != 'actions' else 'Звонок по карточке'
    for index, call_id in enumerate(main_ids):
        # Разговор попытки один: при обрыве и повторном наборе реплики продолжаются в том же диалоге.
        last = index == len(main_ids) - 1
        calls.append({'call_id': call_id, 'kind': kind if last else kind + ' (оборвался)',
                      'transcript': transcript(main_messages) if last else []})
    if not calls and main_messages:
        calls.append({'call_id': None, 'kind': 'Текстовый диалог', 'transcript': transcript(main_messages)})
    for label, items in (('Доклад бригады', (value.get('field_report_calls') or {}).values()),
                         ('Ход работ', [value['progress_call']] if value.get('progress_call') else []),
                         ('Повторный вызов', (value.get('repeat_calls') or {}).values())):
        for item in items:
            messages = store.load(item['session_id']).get('messages', []) if item.get('session_id') else []
            calls.append({'call_id': item.get('call_id'), 'kind': label, 'transcript': transcript(messages)})
    if getattr(store, 'briefings_available', False):
        for (raw,) in store.db.execute('SELECT body FROM briefings WHERE session_id=?', (value['id'],)).fetchall():
            item = json.loads(raw)
            messages = store.load(item['id']).get('messages', []) if item.get('transport') == 'sip' else item.get('messages', [])
            calls.append({'call_id': item.get('call_id'), 'kind': 'Доклад: ' + str(item.get('destination') or item.get('service') or ''),
                          'transcript': transcript(messages)})
    return calls


def call_ids(store, value: dict) -> set[str]:
    return {str(c['call_id']) for c in call_list(store, value) if c.get('call_id')}


def decision(value: dict) -> dict:
    evaluation = value.get('evaluation') or {}
    criteria = [{'label': c['label'], 'expected': c.get('expected'), 'actual': c.get('actual'),
                 'passed': c.get('passed'), 'recommendation': c.get('recommendation')}
                for c in evaluation.get('criteria', [])]
    card = value.get('card') or {}
    return {'card': [{'label': label, 'value': card.get(key)} for key, label in CARD_FIELDS.items()
                     if card.get(key) not in (None, '', [])],
            'services': [{'service': name, **state} for name, state in (value.get('service_states') or {}).items()],
            'criteria': criteria,
            # Решения ДДС оцениваются только в режиме готовой карточки; в приёме вызова 112 они не показываются.
            'dds_checks': [{'label': c['label'], 'passed': c.get('passed'), 'detail': c.get('detail'),
                            'critical': c.get('critical', False)} for c in (value.get('dds_review') or {}).get('checks', [])]
            if value.get('exercise_mode') == 'actions' else []}


def build(store, value: dict, lesson: dict | None, assessment: dict | None) -> dict:
    finished = value.get('status') == 'Завершена'
    return {
        'session_id': value['id'], 'number': value.get('number'), 'status': value.get('status'),
        'task': {'title': value.get('title'), 'lesson_title': (lesson or {}).get('title') or value.get('lesson_title'),
                 'scenario_id': value.get('scenario_id'), 'mode': 'ДДС: готовая карточка' if value.get('exercise_mode') == 'actions'
                 else 'Полный цикл 112: приём вызова', 'difficulty': value.get('difficulty'),
                 'dds_profile': value.get('dds_profile'), 'owner_service': value.get('owner_service'),
                 'learning_objectives': value.get('learning_objectives'), 'transport': value.get('transport', 'text'),
                 'practice_with_hints': bool(value.get('practice_with_hints'))},
        'times': {'created_at': value.get('created_at'), 'finished_at': value.get('finished_at'),
                  'elapsed_seconds': value.get('elapsed_seconds'), 'attempt_outcome': value.get('attempt_outcome'),
                  'completed_by': (value.get('completed_by') or {}).get('role')},
        'norms': norms(value), 'timeline': timeline(value), 'calls': call_list(store, value),
        'decision': decision(value),
        'assessment': assessment if finished else None,
        'grammar': value.get('grammar') if finished else None,
        'ai_review': value.get('ai_review') if finished else None,
    }
