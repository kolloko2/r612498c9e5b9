import asyncio
import json
from uuid import uuid4

import pytest
import llm
import incident_meaning
from briefing import check, check_live, duty_reply
from test_briefing import reporting, saved_card, base
from test_rbac_integration import classroom


@pytest.fixture
def semantic_model(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, 'phone_configuration', lambda: {
        'provider': 'ollama', 'configured': True, 'model': 'local-test'})
    def answer(verdict='equivalent', quote='люди бьют друг друга', detail='none'):
        async def complete(messages, **kwargs):
            calls.append((messages, kwargs))
            return json.dumps({'verdict': verdict, 'quote': quote, 'detail': detail}, ensure_ascii=False)
        monkeypatch.setattr(llm, 'complete', complete)
    answer.calls = calls
    return answer


@pytest.mark.asyncio
@pytest.mark.parametrize('expected,spoken', [
    ('Драка', 'люди бьют друг друга'),
    ('Массовая драка', 'две большие группы людей бьют друг друга'),
    ('Прорыв водопровода', 'из лопнувшей водопроводной трубы хлещет вода'),
    ('Повреждение электросети', 'оборвало силовой кабель'),
    ('Без сознания', 'человек не реагирует и лежит неподвижно'),
])
async def test_semantic_path_is_not_specific_to_one_ticket(semantic_model, expected, spoken):
    semantic_model(quote=spoken)
    report = await check_live(spoken, {'incident_type': expected})
    assert report['complete']
    assert report['checks'][0]['granted_by'] == 'model'
    assert report['semantic_evidence']['quote'] == spoken
    assert semantic_model.calls[-1][1]['phone'] is True


@pytest.mark.asyncio
async def test_no_inference_for_literal_matches_or_address_only(semantic_model):
    semantic_model()
    assert (await check_live('Драка', {'incident_type': 'Драка'}))['complete']
    assert not (await check_live('дом 13', {'house': '12'}))['complete']
    assert not semantic_model.calls


@pytest.mark.asyncio
async def test_partial_scale_prompts_for_participants_not_classifier_title(semantic_model):
    semantic_model('insufficient', 'Во дворе драка', 'participants')
    spoken = 'Во дворе драка'
    report = await check_live(spoken, {'incident_type': 'Массовая драка'})
    assert not report['complete']
    reply = await duty_reply([{'role': 'user', 'content': spoken}],
                             {'incident_type': 'Массовая драка'}, '102', report['missing'], report=report)
    assert 'сколько людей' in reply


@pytest.mark.asyncio
async def test_evidence_reused_only_for_identical_transcript_and_reference(semantic_model):
    spoken = 'люди бьют друг друга'
    semantic_model(quote=spoken)
    report = await check_live(spoken, {'incident_type': 'Драка'})
    assert (await check_live(spoken, {'incident_type': 'Драка'}, report))['complete']
    assert len(semantic_model.calls) == 1
    semantic_model('contradiction', 'драки нет', 'none')
    changed = await check_live(spoken + '. драки нет', {'incident_type': 'Драка'}, report)
    assert not changed['complete']
    assert len(semantic_model.calls) == 2
    assert not check(spoken, {'incident_type': 'Массовая драка'}, report['semantic_evidence'])['complete']


@pytest.mark.asyncio
async def test_semantics_cannot_credit_wrong_house(semantic_model):
    semantic_model()
    report = await check_live('Дом 13, люди бьют друг друга', {'house': '12', 'incident_type': 'Драка'})
    assert report['missing'] == ['Дом']


@pytest.mark.asyncio
async def test_fabricated_quote_and_timeout_do_not_credit(semantic_model, monkeypatch):
    semantic_model(quote='несуществующая цитата')
    assert not (await check_live('люди бьют друг друга', {'incident_type': 'Драка'}))['complete']
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(llm, 'complete', slow)
    monkeypatch.setattr(llm, 'VOICE_REPLY_TIMEOUT_SECONDS', .01)
    assert not (await check_live('люди бьют друг друга', {'incident_type': 'Драка'}))['complete']


@pytest.mark.asyncio
async def test_quote_case_is_restored_to_original_source(semantic_model):
    semantic_model('insufficient', 'во дворе драка', 'participants')
    report = await check_live('Во дворе драка', {'incident_type': 'Массовая драка'})
    assert report['semantic_evidence']['quote'] == 'Во дворе драка'


@pytest.mark.asyncio
async def test_negative_paraphrase_is_not_presented_as_student_quote(semantic_model):
    semantic_model('contradiction', 'никто не дерётся', 'none')
    report = await check_live('Только спорят, никто никого не бьёт', {'incident_type': 'Драка'})
    assert not report['complete']
    assert report['semantic_evidence']['verdict'] == 'contradiction'
    assert report['semantic_evidence']['quote'] == ''


@pytest.mark.asyncio
async def test_cloud_and_mock_never_used(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Must not call a provider')
    monkeypatch.setattr(llm, 'complete', forbidden)
    for provider in ('mock', 'openrouter'):
        monkeypatch.setattr(llm, 'phone_configuration', lambda: {'provider': provider, 'configured': True})
        assert not (await check_live('люди бьют друг друга', {'incident_type': 'Драка'}))['complete']


def test_text_api_finish_and_dds_grading_reuse_semantic_receipt(reporting, semantic_model):
    from dds_review import _briefing
    c = reporting
    client, headers = c['client'], c['headers']['student1']
    session = saved_card(c)
    session = client.put('/api/v1/student/sessions/' + session['id'] + '/card', headers=headers,
                         json={'revision': session['revision'], 'card': {**session['card'], 'incident_type': 'Драка'}}).json()
    created = client.post(base(session), headers=headers, json={
        'message_id': str(uuid4()), 'service': 'Служба 101'}).json()
    url = base(session) + '/' + created['id']
    semantic_model()
    result = client.post(url + '/messages', headers=headers, json={
        'message_id': str(uuid4()), 'text': 'Лесная дом 12, люди бьют друг друга'})
    assert result.status_code == 200, result.text
    assert result.json()['report']['complete']
    final = client.post(url + '/finish', headers=headers, json={
        'message_id': str(uuid4()), 'recipient': 'Начальник смены'})
    assert final.status_code == 200, final.text
    stored = json.loads(c['store'].db.execute('SELECT body FROM workspace WHERE id=?', (session['id'],)).fetchone()[0])
    checks = _briefing(stored, {'brief_service': 'Служба 101'})
    checked = next(item for item in checks if item['id'] == 'briefing_facts')
    assert checked['passed'] and checked['granted_by'] == 'model'
    assert len(semantic_model.calls) == 1


@pytest.mark.asyncio
async def test_equivalent_without_quote_is_asked_once_more(monkeypatch):
    monkeypatch.setattr(llm, 'phone_configuration', lambda: {
        'provider': 'ollama', 'configured': True, 'model': 'local-test'})
    replies = [{'verdict': 'equivalent', 'quote': '', 'detail': 'none'},
               {'verdict': 'equivalent', 'quote': 'из трубы хлещет вода', 'detail': 'none'}]

    async def complete(messages, **kwargs):
        return json.dumps(replies.pop(0), ensure_ascii=False)
    monkeypatch.setattr(llm, 'complete', complete)
    text = 'Учебная 12, во дворе из трубы хлещет вода'
    evidence = await incident_meaning.assess(text, 'Повреждение трубы водоснабжения', timeout=5)
    assert evidence['verdict'] == 'equivalent' and evidence['quote'] == 'из трубы хлещет вода'
    replies[:] = [{'verdict': 'equivalent', 'quote': '', 'detail': 'none'}] * 2
    # Цитаты нет и после повтора — совпадение не засчитывается.
    assert await incident_meaning.assess(text, 'Повреждение трубы водоснабжения', timeout=5) is None
