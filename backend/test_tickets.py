import json
from pathlib import Path

import pytest

from accounts import Accounts
from evaluation import Rubric
from server import Scenario
from test_rbac_integration import classroom
from tickets import load_catalog, router

BASE = '/api/v1/instructor/tickets'
CATALOG = Path(__file__).with_name('data') / 'tickets.json'


@pytest.fixture
def booklet(classroom):
    c = classroom
    c['client'].app.include_router(router(c['store'], Accounts(c['store']), lambda: None, Scenario))
    return c


def test_imported_catalog_matches_the_supplied_booklet():
    catalog = load_catalog(CATALOG)
    assert catalog['metadata']['tickets'] == 32 and catalog['metadata']['drafts'] == 96
    assert catalog['source']['document'].endswith('.pdf') and catalog['source']['transcription'] == 'manual'
    # Every draft must already satisfy the runtime models, otherwise a teacher
    # could only discover a broken import at publication time.
    for item in catalog['drafts']:
        Scenario.model_validate(item['scenario'])
        Rubric.model_validate(item['rubric'])
        assert item['scenario']['enabled'] is False
        assert item['rubric']['time_limit_seconds'] == 300


def test_synthetic_phones_replace_every_booklet_number():
    catalog = load_catalog(CATALOG)
    assert catalog['phones'] == 'synthetic'
    source = json.loads((Path(__file__).resolve().parents[1] / 'tools' / 'data' / 'tickets_source.json')
                        .read_text(encoding='utf-8'))
    originals = {call['situation'][-11:] for ticket in source['tickets'] for call in ticket['calls']}
    body = json.dumps(catalog['drafts'], ensure_ascii=False)
    for original in originals:
        digits = ''.join(ch for ch in original if ch.isdigit())
        if len(digits) == 10:
            assert digits not in body.replace(' ', '').replace('-', '')


def test_listing_and_detail_expose_drafts_without_publishing(booklet):
    client = booklet['client']; h = booklet['headers']['teacher1']
    listing = client.get(BASE, headers=h)
    assert listing.status_code == 200
    data = listing.json()
    assert len(data['tickets']) == 32
    assert all(len(ticket['calls']) == 3 for ticket in data['tickets'])
    assert all(call['published_scenario_id'] is None for call in data['tickets'][0]['calls'])

    detail = client.get(BASE + '/1', headers=h).json()
    assert detail['number'] == 1 and len(detail['calls']) == 3
    assert detail['calls'][0]['scenario']['enabled'] is False
    # Предпросмотр не создаёт сценарий в каталоге преподавателя.
    assert booklet['store'].scenario(detail['calls'][0]['scenario']['id']) is None
    assert client.get(BASE + '/999', headers=h).status_code == 404


def test_students_cannot_reach_the_booklet(booklet):
    client = booklet['client']
    assert client.get(BASE, headers=booklet['headers']['student1']).status_code == 403
    assert client.post(BASE + '/publish', headers=booklet['headers']['student1'],
                       json={'draft_ids': ['ticket-01-1']}).status_code == 403


def test_publishing_is_idempotent_and_owned(booklet):
    client = booklet['client']; h = booklet['headers']['teacher1']
    response = client.post(BASE + '/publish', headers=h, json={'draft_ids': ['ticket-01-1', 'ticket-01-2']})
    assert response.status_code == 201
    published = response.json()['published']
    assert all(entry['created'] for entry in published)
    first = published[0]['scenario_id']
    saved = booklet['store'].scenario(first)
    assert saved['enabled'] is True and saved['title'] == 'Возгорание мусорного контейнера'

    repeat = client.post(BASE + '/publish', headers=h, json={'draft_ids': ['ticket-01-1']}).json()['published']
    assert repeat[0]['scenario_id'] == first and repeat[0]['created'] is False

    listing = client.get(BASE, headers=h).json()
    assert listing['tickets'][0]['calls'][0]['published_scenario_id'] == first
    # Другой преподаватель получает собственный сценарий, а не чужой.
    other = client.post(BASE + '/publish', headers=booklet['headers']['teacher2'],
                        json={'draft_ids': ['ticket-01-1']}).json()['published']
    assert other[0]['scenario_id'] != first
    assert client.get(BASE, headers=booklet['headers']['teacher2']).json()['tickets'][0]['calls'][0][
        'published_scenario_id'] == other[0]['scenario_id']


def test_publish_rejects_unknown_and_duplicate_drafts(booklet):
    client = booklet['client']; h = booklet['headers']['teacher1']
    assert client.post(BASE + '/publish', headers=h, json={'draft_ids': ['ticket-99-9']}).status_code == 404
    assert client.post(BASE + '/publish', headers=h,
                       json={'draft_ids': ['ticket-02-1', 'ticket-02-1']}).status_code == 422
    assert client.post(BASE + '/publish', headers=h, json={'draft_ids': []}).status_code == 422


def test_revision_preserves_teacher_copy_and_is_repeatable(booklet):
    client = booklet['client']; h = booklet['headers']['teacher1']
    body = {'draft_ids': ['ticket-01-1'], 'new_revision': True}
    old = client.post(BASE + '/publish', headers=h, json=body).json()['published'][0]['scenario_id']
    scenario = booklet['store'].scenario(old)
    scenario['description'] = 'Правка преподавателя, которую нельзя терять'
    with booklet['store'].db:
        booklet['store'].db.execute('UPDATE scenarios SET body=? WHERE id=?', (json.dumps(scenario), old))
    new = client.post(BASE + '/publish', headers=h, json=body).json()['published'][0]
    assert new['created'] and new['scenario_id'] != old
    assert booklet['store'].scenario(old)['description'] == scenario['description']
    repeated = client.post(BASE + '/publish', headers=h, json=body).json()['published'][0]
    assert repeated['scenario_id'] == new['scenario_id'] and not repeated['created']


def test_missing_catalog_degrades_to_empty(tmp_path):
    catalog = load_catalog(tmp_path / 'absent.json')
    assert catalog['drafts'] == [] and catalog['metadata']['drafts'] == 0


def test_summary_criterion_survives_the_operator_wording():
    """Эталон билета проверяет смысл, а не дословную фразу источника.

    Диспетчер записывает происшествие своими словами. Критерий из одной
    дословной фразы делал верный по сути ответ непроходимым.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from import_tickets import _anchors
    from evaluation import normalize

    anchors = _anchors("Дерутся 10-15 человек")
    assert anchors and all(len(a) <= 6 for a in anchors)
    # Числа в опоры не входят: заявитель называет их приблизительно.
    assert not any(any(ch.isdigit() for ch in a) for a in anchors)
    written = normalize("Дерутся 15 человек с прутами и палками")
    assert all(anchor in written for anchor in anchors)
    # Совсем другое происшествие критерий по-прежнему не проходит.
    assert not all(anchor in normalize("Затопило подвал") for anchor in anchors)


def test_anchors_ignore_short_and_service_words():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from import_tickets import _anchors
    assert _anchors("Пожар в квартире") == ["пожар", "кварти"]
    # Не больше двух опор: каждая лишняя повышает шанс отклонить верный ответ.
    assert len(_anchors("Задымление мусоропровода в жилом доме")) == 2


def test_all_96_exercises_have_deliverable_cards_and_complete_dds_cycle():
    from workspace import prefilled_from_scenario
    from text_facts import asserted
    catalog = load_catalog(CATALOG)
    crews = set()
    for draft in catalog['drafts']:
        scenario = Scenario.model_validate({**draft['scenario'], 'enabled': True}).model_dump()
        card = prefilled_from_scenario(scenario)
        expectation = scenario['dds_expectation']
        assert card['description'] == scenario['incident'], draft['id']
        assert card['address_note'] == scenario['location'], draft['id']
        assert scenario['owner_service'] in card['services'], draft['id']
        assert card['service_phones'][scenario['owner_service']], draft['id']
        assert expectation['brief_service'] == scenario['owner_service'], draft['id']
        assert expectation['expected_crew_id'] == scenario['crew_options'][0]['id']
        crews.add(expectation['expected_crew_id'])
        assert [u['unlocks_status'] for u in scenario['updates']] == [
            'Начало реагирования', 'Прибытие', 'Проведение работ', 'Работы завершены']
        schedule = [u['after_seconds'] for u in scenario['updates']]
        assert schedule == sorted(set(schedule)) and schedule[0] == 40
        # A literal correct final report must satisfy the private result rubric.
        assert all(asserted(scenario['updates'][-1]['text'], fact)
                   for fact in expectation['result_keywords']), draft['id']
        assert all(card[field] for field in expectation['brief_required_fields'])
    assert len(crews) == 96


def test_booklet_exceptions_do_not_invent_addresses_or_fire():
    drafts = {item['id']: item['scenario'] for item in load_catalog(CATALOG)['drafts']}
    assert drafts['ticket-32-3']['category_id'] == 'utilities'
    assert drafts['ticket-30-3']['owner_service'] == 'Служба 104'
    assert drafts['ticket-31-3']['dds_profile'] == 'gas'
    assert drafts['ticket-03-1']['prefilled_card']['city'] == 'Королёв'
    assert drafts['ticket-23-2']['prefilled_card']['city'] == 'Зеленоград'
    assert drafts['ticket-01-3']['prefilled_card']['region'] == 'Волгоградская область'
    assert drafts['ticket-18-1']['prefilled_card']['city'] == ''
    assert drafts['ticket-08-3']['prefilled_card']['house'] == ''  # 75к2 is a landmark
    assert drafts['ticket-02-1']['prefilled_card']['injured'] is False
    assert drafts['ticket-17-2']['prefilled_card']['injured'] is True
    assert 'Признаков пожара не обнаружено' in drafts['ticket-17-1']['updates'][-1]['text']
    assert 'Огонь перекинулся' not in json.dumps(drafts, ensure_ascii=False)


def test_rebuilding_exercises_is_reproducible():
    import sys
    sys.path.insert(0, str(CATALOG.parents[2] / 'tools'))
    from import_tickets import build, SOURCE
    source = json.loads(SOURCE.read_text(encoding='utf-8'))
    assert build(source, False) == load_catalog(CATALOG)


def test_every_exercise_accepts_its_complete_report():
    from briefing import check
    from workspace import prefilled_from_scenario
    labels = {'house': 'дом', 'building': 'корпус', 'structure': 'строение',
              'apartment': 'квартира', 'entrance': 'подъезд', 'floor': 'этаж'}
    failures = []
    for item in load_catalog(CATALOG)['drafts']:
        scenario = item['scenario']
        card = prefilled_from_scenario(scenario)
        selected = scenario['dds_expectation']['brief_required_fields']
        card['_brief_required_fields'] = selected
        phrases = ['Пострадавшие есть' if field == 'injured' else
                   f'{labels.get(field, "")} {card[field]}' for field in selected]
        result = check('. '.join(phrases), card)
        if not result['complete']:
            failures.append((item['id'], result['missing']))
    assert not failures, failures


def test_all_annotations_reference_real_classifier_rows_and_individual_reports():
    from classifier import resolve
    from routing import main_services
    endings = set()
    for item in load_catalog(CATALOG)['drafts']:
        scenario = item['scenario']
        card = scenario['prefilled_card']
        record = resolve(card['classifier_id'], card['classifier_version'])
        assert card['classifier_features'] == record['features']
        assert card['classifier_group'] == record['group_id']
        assert card['incident_type'] == record['incident_type']
        primary = main_services(record['main_service'])
        assert not primary or scenario['owner_service'] in primary
        endings.add(scenario['updates'][-1]['text'])
    assert len(endings) == 96


def test_negative_medical_classifier_is_not_reversed_in_report():
    from text_facts import incident_asserted
    assert incident_asserted('Мужчина без сознания.', 'Без сознания')
    assert not incident_asserted('Мужчина в сознании.', 'Без сознания')
    assert not incident_asserted('Мужчина без сознания. Мужчина в сознании.', 'Без сознания')
