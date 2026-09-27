import asyncio
import json

import pytest

import ai_review


def session():
    return {
        'owner_service': 'Деп. ЖКХ',
        'card': {'incident_type': 'Повреждение трубы', 'city': 'Москва', 'street': 'Учебная', 'house': '12'},
        'events': [
            {'type': 'service.updated', 'detail': {'service': 'Деп. ЖКХ', 'status': 'Принята', 'comment': 'Принята, бригада 17'}},
            {'type': 'service.updated', 'detail': {'service': 'Деп. ЖКХ', 'status': 'Работы завершены', 'comment': 'трубу починили все ок'}},
            {'type': 'notification.recorded', 'detail': {'source': 'briefing', 'message_id': 'm1'}},
        ],
        'notifications': [{'message_id': 'm1', 'counterpart': 'superior', 'comment': 'Учебная 12, прорыв трубы'}],
        'situation_updates': [{'id': 'done', 'source': 'Бригада 17', 'text': 'Повреждение устранено. Водоснабжение восстановлено.'}],
    }


def test_records_cover_status_comments_and_spoken_briefing():
    records = ai_review.dds_records(session())
    assert [item['label'] for item in records] == [
        'Комментарий к статусу «Принята»', 'Комментарий к статусу «Работы завершены»',
        'Доклад по телефону вышестоящему начальнику']


def test_findings_keep_only_literal_quotes(monkeypatch):
    monkeypatch.setattr(ai_review.llm, 'configuration', lambda: {'provider': 'ollama', 'model': 'm'})
    reply = {'summary': 'Итог записан коротко.', 'findings': [
        {'kind': 'omission', 'record': 'status.2', 'quote': 'трубу починили', 'explanation': 'Не сказано, что вода подана.',
         'suggestion': 'Трубу заменили, водоснабжение восстановлено.', 'reference_ids': ['report.done', 'unknown']},
        {'kind': 'clarity', 'record': 'status.2', 'quote': 'этого нет в записи', 'explanation': 'x', 'suggestion': 'y'}]}

    async def complete(*args, **kwargs):
        return json.dumps(reply, ensure_ascii=False)
    monkeypatch.setattr(ai_review.llm, 'complete', complete)
    result = asyncio.run(ai_review.review_dds(session(), {'checks': []}))
    assert result['status'] == 'ready' and len(result['findings']) == 1
    finding = result['findings'][0]
    assert finding['record_label'] == 'Комментарий к статусу «Работы завершены»'
    assert finding['reference_ids'] == ['report.done']
    reply['findings'] = reply['findings'][1:]
    with pytest.raises(ValueError):
        asyncio.run(ai_review.review_dds(session(), {'checks': []}))
