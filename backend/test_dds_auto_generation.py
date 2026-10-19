import json
from uuid import uuid4

import pytest

from test_generation import drafts, BASE
from test_rbac_integration import classroom
from simple_dds import scenarios
from practice_plan import approved, hint, validate_plan
from dds_authoring import assemble, service_for


def create(c, **values):
    return c['client'].post(BASE, headers=c['headers']['teacher1'], json={
        'request_id': str(uuid4()), 'mode': 'dds', **values})


def test_default_release_can_generate_with_only_a_request_id(drafts, monkeypatch):
    monkeypatch.setenv('OPERATOR_112_ENABLED', 'false')
    response = drafts['client'].post(BASE, headers=drafts['headers']['teacher1'], json={'request_id': str(uuid4())})
    assert response.status_code == 201, response.text
    assert response.json()['mode'] == 'dds' and response.json()['automatic_topic']


@pytest.mark.parametrize('profile', ['general', 'fire', 'police', 'medical', 'gas', 'utilities'])
def test_automatic_mock_has_complete_profile_bundle_and_approved_hints(drafts, profile):
    c = drafts
    response = create(c, dds_profile=profile, difficulty='standard')
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft['automatic_topic'] and draft['brief'] == ''
    scenario = draft['scenario']
    assert scenario['owner_service'] == service_for(profile, 'other')
    assert scenario['dds_profile'] == profile and scenario['difficulty'] == 'standard'
    assert len(scenario['phone_contacts']) >= 4
    assert len({contact['number'] for contact in scenario['phone_contacts']}) == len(scenario['phone_contacts'])
    for crew in scenario['crew_options']:
        assert crew['phone'] == next(contact['number'] for contact in scenario['phone_contacts']
                                     if contact['role'] == 'crew_leader' and contact['crew_id'] == crew['id'])
    assert set(scenario['dds_expectation']['update_keywords']) == {update['id'] for update in scenario['updates']}
    assert scenario['practice_plan'] and not approved(scenario)
    assert c['store'].scenario(scenario['id']) is None
    published = c['client'].post(BASE + '/' + draft['id'] + '/approve', headers=c['headers']['teacher1'], json={'revision': 1})
    assert published.status_code == 200, published.text
    stored = c['store'].scenario(scenario['id'])
    assert stored['enabled'] and approved(stored)
    assert published.json()['scenario'] == stored


def model_stub(monkeypatch, source, seen):
    monkeypatch.setenv('LLM_PROVIDER', 'ollama')
    async def complete(messages, **kwargs):
        seen.append((messages, kwargs))
        return json.dumps({'scenario': source}, ensure_ascii=False)
    monkeypatch.setattr('generation.llm.complete', complete)


def test_model_authors_whole_bundle_and_retry_id_is_stable(drafts, monkeypatch):
    source = scenarios()[0]
    seen = []
    model_stub(monkeypatch, source, seen)
    request = {'request_id': str(uuid4()), 'mode': 'dds', 'dds_profile': 'utilities', 'difficulty': 'advanced'}
    response = drafts['client'].post(BASE, headers=drafts['headers']['teacher1'], json=request)
    assert response.status_code == 201, response.text
    value = response.json()
    assert value['provider'] == 'ollama'
    context = json.loads(seen[0][0][1]['content'])
    assert context['automatic_topic'] is True and context['curriculum']['difficulty'] == 'advanced'
    assert context['owner_service'] == 'Деп. ЖКХ'
    schema = seen[0][1]['json_mode']['properties']['scenario']
    assert 'phone_contacts' in schema['required']
    assert 'phone_contacts' in seen[0][0][0]['content']
    assert value['scenario']['phone_contacts'][1]['facts'] == source['phone_contacts'][1]['facts']
    assert drafts['client'].post(BASE, headers=drafts['headers']['teacher1'], json=request).json() == value
    assert len(seen) == 1
    assert drafts['client'].post(BASE, headers=drafts['headers']['teacher1'], json={**request, 'difficulty': 'basic'}).status_code == 409


def test_fast_profile_expands_compact_facts_into_publishable_complete_exercise(drafts, monkeypatch):
    monkeypatch.setenv('LLM_PROFILE', 'fast')
    original = scenarios()[0]
    source = {
        'prefilled_card': original['prefilled_card'],
        'updates': [{'text': update['text']} for update in original['updates']],
        'phone_contacts': [{key: contact.get(key, [] if key in ('facts', 'required_keywords') else False)
                            for key in ('name', 'role', 'facts', 'required', 'required_keywords')}
                           for contact in original['phone_contacts']],
        'dds_expectation': {'should_accept': True, 'refusal_kind': 'none'},
    }
    seen = []
    model_stub(monkeypatch, source, seen)
    response = create(drafts, dds_profile='utilities')
    assert response.status_code == 201, response.text
    value = response.json()
    scenario = value['scenario']
    assert seen[0][1]['max_tokens'] == 2500
    assert 'known_facts' not in seen[0][1]['json_mode']['properties']['scenario']['required']
    compact_props = seen[0][1]['json_mode']['properties']['scenario']['properties']
    assert compact_props['updates']['minItems'] == compact_props['phone_contacts']['minItems'] == 4
    assert compact_props['dds_expectation']['properties']['should_accept']['enum'] == [True]
    assert scenario['known_facts'] == [original['prefilled_card']['description']]
    assert scenario['dds_expectation']['refusal_kind'] == ''
    assert compact_props['dds_expectation']['properties']['refusal_kind']['enum'] == ['', 'foreign_territory', 'duplicate', 'no_works']
    assert [update['text'] for update in scenario['updates']] == [update['text'] for update in original['updates']]
    assert [update['after_seconds'] for update in scenario['updates']] == [30,60,90,120]
    assert len(scenario['phone_contacts']) == 4 and scenario['practice_plan']
    assert len({contact['number'] for contact in scenario['phone_contacts']}) == 4
    assert all(contact['crew_id'] == scenario['crew_options'][0]['id']
               for contact in scenario['phone_contacts'] if contact['role'].startswith('crew_'))
    published = drafts['client'].post(BASE + '/' + value['id'] + '/approve',
                                     headers=drafts['headers']['teacher1'], json={'revision': 1})
    assert published.status_code == 200, published.text
    assert approved(published.json()['scenario'])


def test_invalid_model_bundle_gets_one_structural_retry(drafts, monkeypatch):
    seen = []
    monkeypatch.setenv('LLM_PROVIDER', 'ollama')
    async def complete(messages, **kwargs):
        seen.append(json.loads(messages[1]['content']))
        source = scenarios()[0]
        if len(seen) == 1:
            source['phone_contacts'][2]['crew_id'] = 'absent_crew'
        return json.dumps({'scenario': source}, ensure_ascii=False)
    monkeypatch.setattr('generation.llm.complete', complete)
    response = create(drafts, dds_profile='utilities')
    assert response.status_code == 201, response.text
    assert len(seen) == 2 and seen[1]['repair_note']


def test_authoring_timeout_is_total_and_network_wait_is_not_retried(drafts, monkeypatch):
    import asyncio
    monkeypatch.setenv('LLM_PROVIDER', 'ollama')
    monkeypatch.setattr('generation.llm.AUTHORING_TIMEOUT_SECONDS', 0.05)
    calls = []
    async def complete(*args, **kwargs):
        calls.append(1)
        await asyncio.sleep(1)
    monkeypatch.setattr('generation.llm.complete', complete)
    assert create(drafts, dds_profile='utilities').status_code == 502
    assert len(calls) == 1


@pytest.mark.parametrize('accepted', [True, False])
def test_none_refusal_marker_is_normalized_only_for_accepted_cards(accepted):
    from server import Scenario
    from pydantic import ValidationError
    source = scenarios()[0]
    source['dds_expectation'].update(should_accept=accepted, refusal_kind='none')
    if not accepted:
        source.update(updates=[], crew_options=[], phone_contacts=source['phone_contacts'][:1])
    value = assemble(source, 'Деп. ЖКХ', strict=True)
    if accepted:
        assert Scenario.model_validate(value).dds_expectation.refusal_kind == ''
    else:
        assert value['dds_expectation']['refusal_kind'] == 'none'
        with pytest.raises(ValidationError, match='refusal_kind'):
            Scenario.model_validate(value)


@pytest.mark.parametrize('fault', ['future', 'missing_role', 'unreachable_crew', 'wrong_keyword'])
def test_invalid_revision_never_replaces_previous_bundle(drafts, monkeypatch, fault):
    old = create(drafts, dds_profile='utilities').json()
    source = scenarios()[0]
    if fault == 'future':
        source['phone_contacts'][1]['facts'] = ['Бригада прибыла, работы завершены.']
    elif fault == 'missing_role':
        source['phone_contacts'] = source['phone_contacts'][:3]
    elif fault == 'unreachable_crew':
        source['phone_contacts'][2]['crew_id'] = 'absent'
    else:
        source['dds_expectation']['update_keywords']['working'] = ['Факт, отсутствующий в докладе']
    seen = []
    model_stub(monkeypatch, source, seen)
    path = BASE + '/' + old['id']
    response = drafts['client'].post(path + '/revise', headers=drafts['headers']['teacher1'],
                                     json={'revision': 1, 'comment': 'Измените ситуацию'})
    assert response.status_code == 502
    assert len(seen) == 2
    assert drafts['client'].get(path, headers=drafts['headers']['teacher1']).json() == old


def test_contact_hint_uses_identity_without_disclosing_private_or_future_facts():
    from server import Scenario
    from dds_authoring import prepare_plan
    from practice_plan import fingerprint
    source = assemble(scenarios()[0], 'Деп. ЖКХ')
    source = Scenario.model_validate(prepare_plan(source)).model_dump()
    source['practice_approved_version'] = fingerprint(source)
    value = dict(practice_with_hints=True, exercise_mode='actions', owner_service='Деп. ЖКХ',
                 assigned_crew=source['crew_options'][0], service_states={'Деп. ЖКХ': {'status': 'Принята'}},
                 events=[{'type': 'notification.recorded', 'detail': {'source': 'briefing', 'counterpart': role}}
                         for role in ('crew', 'superior')])
    step = hint(source, value)
    assert step['phase'] == 'contact' and step['contact_id'] == 'organization'
    assert source['phone_contacts'][1]['facts'][0] not in step['text']
    assert source['updates'][-1]['text'] not in step['text']
    validate_plan(source)
    source['practice_plan'].append({**step, 'contact_id': 'absent_contact'})
    with pytest.raises(ValueError):
        validate_plan(source)


@pytest.mark.parametrize('index', [0, 1])
def test_generated_bundle_can_be_assigned_with_hints_and_completed(drafts, monkeypatch, index):
    from accounts import Accounts
    from briefing import router as briefing_router
    from test_phone_directory import complete_call
    c = drafts
    source = scenarios()[index]
    model_stub(monkeypatch, source, [])
    created = create(c, dds_profile='utilities')
    assert created.status_code == 201, created.text
    generation = created.json()
    approved_response = c['client'].post(BASE + '/' + generation['id'] + '/approve',
                                         headers=c['headers']['teacher1'], json={'revision': 1})
    assert approved_response.status_code == 200, approved_response.text
    source = approved_response.json()['scenario']
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    monkeypatch.setenv('OPERATOR_112_ENABLED', 'false')
    c['client'].app.include_router(briefing_router(c['store'], Accounts(c['store']), lambda: None))
    client, h = c['client'], c['headers']['student1']
    assignment = client.post('/api/v1/instructor/assignments', headers=c['headers']['teacher1'], json={
        'group_id': c['group']['id'], 'scenario_id': source['id'], 'title': source['title'], 'practice_with_hints': True})
    assert assignment.status_code == 201, assignment.text
    started = client.post('/api/v1/student/sessions', headers=h, json={
        'scenario_id': source['id'], 'assignment_id': assignment.json()['id']})
    assert started.status_code == 201, started.text
    card = started.json()
    assert card['practice_with_hints'] and card['practice_hint']
    path = '/api/v1/student/sessions/' + card['id']
    assert client.post(path + '/open', headers=h).status_code == 200
    assert client.post(path + '/services', headers=h, json={
        'service': 'Деп. ЖКХ', 'status': 'Принята', 'comment': 'Профильная карточка принята.'}).status_code == 200
    assert client.post(path + '/crew', headers=h, json={
        'message_id': str(uuid4()), 'crew_id': source['crew_options'][0]['id'], 'decision_by': 'dispatcher'}).status_code == 200
    contacts = {contact['role']: contact for contact in source['phone_contacts']}
    initial = f"Москва улица Учебная дом {index + 1}. {source['incident']}. Пострадавших нет."
    complete_call(c, path, contacts['crew_leader']['number'], initial)
    complete_call(c, path, contacts['superior']['number'], initial)
    current = client.get(path, headers=h).json()
    assert current['practice_hint']['phase'] == 'contact'
    complete_call(c, path, contacts['organization_head']['number'],
                  'Согласуйте уборку мусора во дворе.' if not index else 'Согласуйте доступ в подъезд, пожалуйста.')
    complete_call(c, path, contacts['crew_specialist']['number'],
                  'Что известно про мусор на площадке?' if not index else 'Что известно про замок входной двери?')
    monkeypatch.setattr('workspace.update_elapsed', lambda value, update: 1000)
    monkeypatch.setattr('workspace.REPORT_GAP_SECONDS', 0)
    for update in source['updates']:
        assert client.post(path + '/updates', headers=h).status_code == 200
        recorded = client.post(path + '/services', headers=h, json={
            'service': 'Деп. ЖКХ', 'status': update['unlocks_status'], 'comment': update['text']})
        assert recorded.status_code == 200, recorded.text
    complete_call(c, path, contacts['superior']['number'], source['updates'][-1]['text'])
    assert client.post(path + '/processed', headers=h).status_code == 200
    result = client.post(path + '/finish', headers=h)
    assert result.status_code == 200, result.text
    assert result.json()['dds_review']['passed'], json.dumps(result.json()['dds_review'], ensure_ascii=False)
    assert result.json()['dds_review']['score_percent'] == 100
