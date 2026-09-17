from server import Scenario
from test_rbac_integration import classroom
from test_lesson_lifecycle import create_lesson


def advanced(c):
    value = c['store'].scenario(c['scenario_id'])
    value.update(id='advanced_medical', difficulty='advanced', dds_profile='medical', learning_objectives='Уточнить учебные сведения')
    c['store'].put_scenario(Scenario.model_validate(value))
    return value


def test_filter_pool_explicit_mismatch_and_frozen_metadata(classroom):
    c = classroom; client = c['client']; h = c['headers']; value = advanced(c)
    assert create_lesson(c, difficulty='advanced').status_code == 422
    lesson = create_lesson(c, scenario_ids=[], difficulty='advanced', dds_profile='medical')
    assert lesson.status_code == 201, lesson.text
    lesson = lesson.json()
    assert lesson['scenario_ids'] == [value['id']]
    url = '/api/v1/instructor/lessons/'+lesson['id']
    assert client.post(url+'/start', headers=h['teacher1']).status_code == 200
    value['difficulty'] = 'basic'
    c['store'].put_scenario(Scenario.model_validate(value))
    issued = client.post('/api/v1/student/lessons/'+lesson['id']+'/next', headers=h['student1']).json()
    assert issued['difficulty'] == 'advanced' and issued['dds_profile'] == 'medical'
    assert issued['learning_objectives'] == 'Уточнить учебные сведения'
    assert 'known_facts' not in issued and 'scenario' not in issued


def test_recheck_on_start_and_action_templates(classroom):
    c = classroom; value = advanced(c); client = c['client']; h = c['headers']['teacher1']
    lesson = create_lesson(c, scenario_ids=[value['id']], difficulty='advanced').json()
    assert create_lesson(c, scenario_ids=[], mode='actions', prefilled_scenario_ids=[value['id']], dds_profile='fire').status_code == 422
    assert create_lesson(c, scenario_ids=[], mode='actions', prefilled_scenario_ids=[value['id']], dds_profile='medical', difficulty='advanced').status_code == 201
    value['difficulty'] = 'standard'
    c['store'].put_scenario(Scenario.model_validate(value))
    assert client.post('/api/v1/instructor/lessons/'+lesson['id']+'/start', headers=h).status_code == 409
    assert create_lesson(c, difficulty='unknown').status_code == 422


def test_legacy_defaults_and_assignment_discovery(classroom):
    c = classroom
    result = c['client'].get('/api/v1/student/assignments', headers=c['headers']['student1']).json()[0]
    assert result['difficulty'] == 'basic' and result['dds_profile'] == 'general'
    assert c['session']['difficulty'] == 'basic'
