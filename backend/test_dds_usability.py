import pytest
import llm
from briefing import duty_reply
from dds_review import review
from test_dds_review import card, event, verdict, SERVICE


@pytest.mark.asyncio
async def test_new_facts_acknowledged_without_waiting_for_model(monkeypatch):
    async def slow(*args, **kwargs):
        raise AssertionError('Routine receipt must not wait for inference')
    monkeypatch.setattr(llm, 'reply', slow)
    reply = await duty_reply([{'role':'user','content':'улица Лесная'}],
                             {'street':'Лесная','house':'12'}, SERVICE, ['Дом'])
    assert 'дом' in reply.lower()


@pytest.mark.asyncio
async def test_free_question_still_reaches_model(monkeypatch):
    calls=[]
    async def answer(*args, **kwargs):
        calls.append(args)
        return 'Сведений о доступе пока нет, уточняем.'
    monkeypatch.setattr(llm, 'configuration', lambda: {'provider':'local','configured':True})
    monkeypatch.setattr(llm, 'reply', answer)
    reply=await duty_reply([{'role':'user','content':'Можете уточнить доступ во двор?'}],
                           {'street':'Лесная','house':'12'}, SERVICE, ['Улица или ориентир','Дом'])
    assert calls and 'уточняем' in reply


def test_listening_and_status_alone_do_not_credit_observation():
    events = [event(1,'situation.update','2026-09-27T10:00:00+00:00',id='working'),
              event(2,'service.updated','2026-09-27T10:00:10+00:00',
                    service=SERVICE,status='Проведение работ',comment='Принято')]
    value=card(events,unlocks={'working':'Проведение работ'})
    expectation={'should_accept':True,'update_keywords':{'working':['повреждена труба']}}
    assert not verdict(review(value,expectation),'update_facts:working')['passed']
    events[-1]['detail']['comment']='Повреждена труба, бригада выполняет ремонт.'
    assert verdict(review(value,expectation),'update_facts:working')['passed']
    events[-1]['detail']['comment']='Не повреждена труба.'
    assert not verdict(review(value,expectation),'update_facts:working')['passed']
