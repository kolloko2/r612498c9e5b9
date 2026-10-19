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


def test_dds_review_asks_for_a_short_answer(monkeypatch):
    # A CPU server writes about three tokens a second: the request caps the answer.
    monkeypatch.setattr(ai_review.llm, 'configuration', lambda: {'provider': 'ollama', 'model': 'm'})
    seen = {}

    async def complete(*args, **kwargs):
        seen.update(kwargs)
        return json.dumps({'summary': 'Коротко.', 'findings': []}, ensure_ascii=False)
    monkeypatch.setattr(ai_review.llm, 'complete', complete)
    asyncio.run(ai_review.review_dds(session(), {'checks': []}))
    schema = seen['json_mode']
    assert schema["properties"]["findings"]["maxItems"] == 2
    assert schema['properties']['summary']['maxLength'] <= 500
    assert seen["max_tokens"] <= 900 and seen['timeout'] >= 300


def test_cut_text_ends_on_a_sentence():
    text = 'Первое предложение сказано. Второе обрывается на полусло'
    assert ai_review._whole_sentences(text, len(text)) == 'Первое предложение сказано.'
    assert ai_review._whole_sentences('Коротко.', 100) == 'Коротко.'
    assert ai_review._whole_sentences('слово ' * 30, 120).endswith('…')


def test_dds_review_prompt_stays_small(monkeypatch):
    monkeypatch.setattr(ai_review.llm, 'configuration', lambda: {'provider': 'ollama', 'model': 'm'})
    seen = {}

    async def complete(messages, **kwargs):
        seen['payload'] = json.loads(messages[1]['content'])
        return json.dumps({'summary': 'Коротко.', 'findings': []}, ensure_ascii=False)
    monkeypatch.setattr(ai_review.llm, 'complete', complete)
    materials = [{'id': str(i), 'title': 't', 'revision': 1, 'location': 'p', 'excerpt': 'х' * 1000} for i in range(12)]
    asyncio.run(ai_review.review_dds(session(), {'checks': []}, [{'x': 1}] * 5, materials))
    assert sum(len(m['excerpt']) for m in seen['payload']['teacher_materials']) <= 1500
    assert len(seen['payload']['teacher_corrections']) <= 2


def test_omission_contradicting_the_record_is_dropped():
    record = 'Шум появляется в период с 18:00 до 19:00, особенно в подвале.'
    assert ai_review._already_written('В записи не указан период 18:00–19:00.', record)
    assert not ai_review._already_written('Не указан адрес дома 12.', record)
    assert not ai_review._already_written('Не сказано, что вода подана.', record)
