import json
import pytest
from territories import recipients


def test_exact_approved_routes_only(monkeypatch, tmp_path):
    path = tmp_path / 'routes.json'
    path.write_text(json.dumps({'version': 1, 'rules': [
        {'id': 'district', 'source': 'Утверждённый учебный справочник', 'approved': True,
         'match': {'city': 'Москва', 'district': 'Щукино'}, 'services': ['ДДС района']},
        {'id': 'school', 'source': 'Автор сценария', 'approved': False,
         'match': {'district': 'Щукино'}, 'services': ['Ведомственная ДДС']}
    ]}), encoding='utf-8')
    monkeypatch.setenv('TERRITORIAL_ROUTES_FILE', str(path))
    found = recipients({'city': 'москва', 'district': ' Щукино '})
    assert [r['service'] for r in found] == ['ДДС района']
    assert found[0]['mappings'][0]['cell'] == 'territory:district'
    assert recipients({'city': 'Москва', 'district': 'Северное Щукино'}) == []


def test_missing_and_invalid_directory(monkeypatch, tmp_path):
    monkeypatch.delenv('TERRITORIAL_ROUTES_FILE', raising=False)
    assert recipients({}) == []
    path = tmp_path / 'bad.json'
    path.write_text('[]', encoding='utf-8')
    monkeypatch.setenv('TERRITORIAL_ROUTES_FILE', str(path))
    with pytest.raises(ValueError):
        recipients({})


def test_explicit_affiliations_cover_other_territories_and_deduplicate(monkeypatch):
    monkeypatch.delenv('TERRITORIAL_ROUTES_FILE', raising=False)
    found = recipients({'city': 'Учебный город', 'area': 'Район 1', 'district': 'Округ 1',
                        'recipient_affiliations': {'area': 'ДДС района 1', 'district': 'ДДС округа 1',
                                                   'department': 'ДДС образования'}})
    assert len(found) == 3
    assert all(item['mappings'][0]['cell'].startswith('scenario-affiliation:') for item in found)
    found = recipients({'city': 'Москва', 'area': 'Щукино',
                        'recipient_affiliations': {'area': 'Район Щукино'}})
    assert len([item for item in found if item['service'] == 'Район Щукино']) == 1
    with pytest.raises(ValueError):
        recipients({'recipient_affiliations': {'area': 'ДДС неизвестной территории'}})


def test_shipped_geography_and_address_aliases(monkeypatch):
    monkeypatch.delenv('TERRITORIAL_ROUTES_FILE', raising=False)
    assert [i['service'] for i in recipients({'city':'г. Москва', 'district':'Южный административный округ'})] == ['ЮАО']
    result = recipients({'city':'Москва','street':'Коломенская наб.','house':'18'})
    assert {i['service'] for i in result} == {'Район Нагатинский Затон','ЮАО'}
    assert recipients({'city':'Москва','street':'Коломенская наб.','house':'180'}) == []
    result = recipients({'city':'Москва','street':'ул. Маршала Василевского','house':'13','building':'3'})
    assert {i['service'] for i in result} == {'Район Щукино','СЗАО'}
    assert recipients({'city':'Москва','street':'Маршала Василевского','house':'13','building':'99'}) == []
