import json
from uuid import uuid4

from accounts import Accounts
from briefing import router
from test_briefing import saved_card, base
from test_rbac_integration import classroom


def test_sip_recovery_keeps_report_and_checks_owner(classroom):
    c = classroom
    old, new = str(uuid4()), str(uuid4())
    attempts = []
    reason = ['service_restart']

    async def voice(path, method='GET', body=None):
        if path == 'calls':
            attempts.append(body)
            return {'call_id': old if len(attempts) == 1 else new}
        return {'status': 'failed', 'reason': reason[0]}

    c['client'].app.include_router(router(c['store'], Accounts(c['store']), lambda: None, voice=voice))
    card = saved_card(c)
    card['sip_extension'] = '201'
    with c['store'].db:
        c['store'].db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(card), card['id']))
    headers = c['headers']['student1']
    started = c['client'].post(base(card), headers=headers, json={
        'message_id':str(uuid4()), 'service':'Служба 101', 'transport':'sip'}).json()
    bid = started['id']
    state = c['store'].load(bid)
    state['messages'] = [{'role':'user', 'content':'Улица Лесная, дом 12'}]
    c['store'].save(bid, state)
    url = base(card) + '/' + bid + '/recover?expected_call_id=' + old
    assert c['client'].post(url, headers=c['headers']['student2']).status_code == 404
    reason[0] = 'remote_hangup'
    assert c['client'].post(url, headers=headers).json()['call_id'] == old
    assert len(attempts) == 1
    reason[0] = 'service_restart'
    result = c['client'].post(url, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()['call_id'] == new
    assert result.json()['messages'][0]['content'] == 'Улица Лесная, дом 12'
    assert c['store'].load(bid)['superseded_calls'] == [old]
    # Lost response retry must not dial a third call.
    assert c['client'].post(url, headers=headers).json()['call_id'] == new
    assert len(attempts) == 2
    row = c['store'].db.execute('SELECT body FROM briefings WHERE id=?', (bid,)).fetchone()
    item = json.loads(row[0])
    item['recovery']['attempts'] = 3
    with c['store'].db:
        c['store'].db.execute('UPDATE briefings SET body=? WHERE id=?', (json.dumps(item), bid))
    result = c['client'].post(base(card)+'/'+bid+'/recover?expected_call_id='+new, headers=headers)
    assert result.json()['recovery']['state'] == 'exhausted'
    assert len(attempts) == 2
    card['status'] = 'Завершена'
    with c['store'].db:
        c['store'].db.execute('UPDATE workspace SET body=? WHERE id=?', (json.dumps(card), card['id']))
    assert c['client'].post(url, headers=headers).status_code == 409
