"""Explicit local territorial/departmental routing; never infer an authority."""
import json
import os
import re
from pathlib import Path

FIELDS = {'city', 'district', 'area', 'object', 'street', 'house', 'building', 'structure'}

DISTRICTS = dict(zip(
    ('центральный', 'северный', 'северо-восточный', 'восточный', 'юго-восточный',
     'южный', 'юго-западный', 'западный', 'северо-западный', 'зеленоградский', 'новомосковский', 'троицкий'),
    ('цао', 'сао', 'свао', 'вао', 'ювао', 'юао', 'юзао', 'зао', 'сзао', 'зелао', 'нао', 'тао')))


def normalize(value, field=''):
    value = ' '.join(str(value or '').casefold().replace('ё', 'е').replace('‑', '-').split())
    if field == 'district':
        value = re.sub(r'\s+(административный округ|ао)$', '', value)
        return DISTRICTS.get(value, value)
    if field == 'city':
        return re.sub(r'^(город\s+|г\.\s*)', '', value)
    if field == 'area':
        return re.sub(r'^район\s+', '', value)
    if field == 'street':
        value = re.sub(r'^(улица\s+|ул\.\s*)', '', value)
        value = re.sub(r'\s+наб\.$', ' набережная', value)
        value = re.sub(r'\s+пр-д$', ' проезд', value)
    return value


def rules():
    path = os.getenv('TERRITORIAL_ROUTES_FILE', '').strip()
    if not path:
        path = str(Path(__file__).with_name('data') / 'territorial_routes.json')
    raw = Path(path).read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('Справочник территорий превышает 1 МиБ')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('rules'), list) or len(data['rules']) > 2000:
        raise ValueError('Некорректный справочник территорий')
    ids = set()
    for rule in data['rules']:
        if (not isinstance(rule, dict) or not isinstance(rule.get('id'), str)
                or rule['id'] in ids or not isinstance(rule.get('source'), str) or not 1 <= len(rule['source']) <= 500
                or not isinstance(rule.get('approved'), bool)
                or not isinstance(rule.get('match'), dict) or not rule['match']
                or set(rule['match']) - FIELDS
                or any(not isinstance(v, str) or not v.strip() for v in rule['match'].values())
                or not isinstance(rule.get('services'), list) or not 1 <= len(rule['services']) <= 100
                or any(not isinstance(s, str) or not 1 <= len(s.strip()) <= 160 for s in rule['services'])):
            raise ValueError('Некорректное правило территории')
        ids.add(rule['id'])
    return [r for r in data['rules'] if r['approved']]


def recipients(card):
    found = []
    # Explicit author-supplied recipients cover any territory or owning department.
    for scope, service in (card.get('recipient_affiliations') or {}).items():
        if scope not in ('area', 'district', 'department') or not isinstance(service, str) or not 1 <= len(service.strip()) <= 160:
            raise ValueError('Некорректная территориальная принадлежность')
        if scope in ('area', 'district') and not str(card.get(scope, '')).strip():
            raise ValueError('Для получателя территории заполните поле ' + scope)
        found.append({'service': service.strip(), 'mappings': [
            {'cell': 'scenario-affiliation:' + scope, 'incident_type': 'Получатель задан автором карточки: ' + scope}], 'primary': False})
    for rule in rules():
        if all(normalize(card.get(k), k) == normalize(v, k) for k, v in rule['match'].items()):
            for service in rule['services']:
                found.append({'service': service.strip(), 'mappings': [
                    {'cell': 'territory:' + rule['id'], 'incident_type': rule['source']}], 'primary': False})
    merged = {}
    for item in found:
        key = normalize(item['service'])
        if key in merged:
            merged[key]['mappings'].extend(item['mappings'])
        else:
            merged[key] = item
    return list(merged.values())
