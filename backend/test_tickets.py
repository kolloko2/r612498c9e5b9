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
        assert item['rubric']['time_limit_seconds'] == 30


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


def test_missing_catalog_degrades_to_empty(tmp_path):
    catalog = load_catalog(tmp_path / 'absent.json')
    assert catalog['drafts'] == [] and catalog['metadata']['drafts'] == 0
