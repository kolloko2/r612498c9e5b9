"""Assemble one coherent DDS exercise from local-model content, not live events."""
from copy import deepcopy
import re

from phone_directory import PhoneContact, mentioned
from practice_plan import draft as practice_draft
from text_facts import asserted

PROFILE_SERVICES = {'fire': 'Служба 101', 'police': 'Служба 102', 'medical': 'Служба 103',
                    'gas': 'Служба 104', 'utilities': 'Деп. ЖКХ'}


def service_for(profile, category, supplied=''):
    return supplied.strip() or PROFILE_SERVICES.get(profile) or {
        'fire': 'Служба 101', 'medical': 'Служба 103', 'utilities': 'Деп. ЖКХ',
        'public': 'Служба 102', 'traffic': 'Служба 102'}.get(category, 'Деп. ЖКХ')


def automatic_brief(profile, difficulty, seed):
    return (f'Самостоятельно придумай новую учебную ситуацию для диспетчера ДДС. '
            f'Профиль: {profile}, сложность: {difficulty}. '
            'Выбери конкретное происшествие и синтетический адрес, разработай полный цикл '
            'от поступления карточки до результата работ. Согласуй карточку, роли и сведения '
            'собеседников, бригады, четыре доклада и проверяемые факты. '
            f'Идентификатор варианта: {seed}. Не повторяй пример из инструкции.')


def mock_exercise(profile, seed):
    """Explicitly labeled mock, with profile-consistent content and no AI claim."""
    from simple_dds import scenarios
    source = scenarios()[int(seed[-1], 16) % 2]
    choices = {
        'fire': ('Задымление в помещении', 'В помещении обнаружено задымление. Пострадавших нет.', 'Задымление устранено, работы завершены.'),
        'police': ('Нарушение общественного порядка', 'Во дворе шумная ссора. Пострадавших нет.', 'Нарушение прекращено, обстановка спокойная, работы завершены.'),
        'medical': ('Человеку нужна медицинская помощь', 'Заявитель сообщает о недомогании человека в подъезде.', 'Помощь оказана, вызов отработан, работы завершены.'),
        'gas': ('Запах газа в подъезде', 'Заявитель сообщает о запахе газа в подъезде. Пострадавших нет.', 'Источник запаха устранён, работы завершены.'),
    }
    if profile in choices:
        incident, description, final = choices[profile]
        source.update(title='ДДС: ' + incident, incident=incident, description='Учебный пример (mock)',
                      known_facts=[description])
        source['prefilled_card'].update(incident_type=incident, description=description)
        source['prefilled_card']['services'] = []
        source['prefilled_card']['service_phones'] = {}
        source['updates'][1]['text'] = 'Бригада прибыла. ' + description
        source['updates'][2]['text'] = 'Бригада выполняет профильные работы на месте.'
        source['updates'][3]['text'] = final
        source['dds_expectation'].update(brief_keywords=[incident], result_keywords=keywords(final), update_keywords={})
        for contact in source['phone_contacts']:
            contact.update(facts=['Уточнённых сведений об обстановке пока нет.'], required=False, required_keywords=[])
    return source


COMPLETED = (r'\b(?:устранен[аоы]?|восстановлен[аоы]?|ликвидирован[аоы]?|убран[аоы]?|очищен[аоы]?|потушен[аоы]?'
             r'|отремонтирован[аоы]?|завершены|прибыл[аи]?|выехал[аи]?)\b')


def address_error(card):
    """Street and house are checked in spoken reports, so they must be clean separate fields."""
    street, house = str(card.get('street', '')).strip(), str(card.get('house', '')).strip()
    if re.search(r'\bд\.|\bдом\b|,\s*\d|\s\d+[А-Яа-я]?$', street, re.I):
        return 'street must contain only the street name, the house number goes to house'
    if not re.fullmatch(r'\d{1,4}[А-Яа-я]?(?:\s*(?:к|корп\.?|стр\.?)\s*\d{1,3})?', house):
        return 'house must be a short house number like 12 or 12Б'
    return ''


def adds_information(facts, card_text):
    known = {w.casefold() for w in re.findall(r'[\w-]+', card_text, re.U)}
    fresh = [w for fact in facts for w in re.findall(r'[\w-]+', fact, re.U) if len(w) > 4 and w.casefold() not in known]
    return len(fresh) >= 2


def keywords(text):
    """Literal, bounded evidence; no invented expected actions or regulations."""
    words = re.findall(r'[\w-]+', text, re.U)
    selected = []
    for word in words:
        if len(word) > 3 and word.casefold() not in {'бригада', 'старший', 'докладывает', 'сообщает'} and asserted(text, word):
            selected.append(word)
        if len(selected) == 4:
            break
    return list(dict.fromkeys(selected)) or [text.strip()[:100]]


def assemble(source, owner, strict=False):
    source = deepcopy(source)
    # Compact authoring carries each fact once. Expand only unambiguous metadata;
    # never replace supplied facts/references or invent operational report content.
    card = source.get('prefilled_card') or {}
    if card:
        source.setdefault('incident', card.get('incident_type', ''))
        source.setdefault('title', 'ДДС: ' + card.get('incident_type', ''))
        source.setdefault('location', ', '.join(str(card[key]) for key in ('city', 'street', 'house') if card.get(key)))
        source.setdefault('known_facts', [card['description']] if card.get('description') else [])
    if strict and re.fullmatch(r'[A-Za-z0-9_ .-]+', str(card.get('incident_type', '')).strip() or 'x'):
        # A model sometimes writes a code like "water_supply": the card must read naturally.
        raise ValueError('Write incident_type and texts in Russian, not as an English code')
    if card.get('incident_type'):
        # The incident type is shown as a title in the card and journal.
        card['incident_type'] = card['incident_type'][:1].upper() + card['incident_type'][1:]
    if strict and re.search(COMPLETED, str(card.get('incident_type', '')).replace('ё', 'е'), re.I):
        raise ValueError('incident_type must name what happened, not the result of works')
    if strict:
        address_problem = address_error(card)
        if address_problem:
            raise ValueError(address_problem)
    applicant = next((c for c in source.get('phone_contacts') or [] if c.get('role') == 'applicant'), None)
    if applicant and not card.get('phone'):
        # The dispatcher dials the applicant by the number shown in the card.
        digits = str(sum(map(ord, card.get('description', '') + applicant.get('name', ''))) % 10000).zfill(4)
        card['phone'] = f'+7 900 000-{digits[:2]}-{digits[2:]}'
        card.setdefault('caller_name', applicant.get('name', ''))
    contacts = source.get('phone_contacts') or []
    accepted = (source.get('dds_expectation') or {}).get('should_accept', True)
    leader = next((contact.get('name') for contact in contacts if contact.get('role') == 'crew_leader'), None)
    crews = source.get('crew_options') or ([{'id': 'Бригада 1', 'leader': leader or 'Старший учебной бригады', 'phone': '8103'}] if accepted else [])
    if accepted and len(crews) > 3:
        raise ValueError('Use at most three crews in a generated exercise')
    ids = [crew['id'] for crew in crews]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate crew identifiers')
    source['crew_options'] = crews
    if strict and not contacts:
        raise ValueError('Generate a phone directory with roles and known facts')
    if not contacts:
        contacts = [dict(id='chief', name='Начальник дежурной смены', role='superior', number='8101')]
        if accepted:
            contacts.append(dict(id='organization', name='Руководитель организации', role='organization_head',
                                 number='8102', facts=['Дополнительных сведений пока нет.']))
            for crew in crews:
                contacts.append(dict(id='leader_' + str(len(contacts)), name=crew['leader'], role='crew_leader',
                                     number='8103', crew_id=crew['id']))
            contacts.append(dict(id='specialist', name='Специалист бригады', role='crew_specialist',
                                 number='8104', crew_id=crews[0]['id'], facts=['Уточнённые сведения будут после осмотра.']))
    if not 1 <= len(contacts) <= 10:
        raise ValueError('Directory must contain 1–10 contacts')
    normalized = []
    initial = '\n'.join(str(value) for value in (source.get('prefilled_card') or {}).values() if isinstance(value, str))
    for index, contact in enumerate(contacts, 1):
        # Allocate internal training extensions; model output cannot dial a real number.
        contact = dict(contact)
        contact.setdefault('id', f"{contact.get('role', 'contact')}_{index}")
        if not contact.get('crew_id') and contact.get('role') in ('crew_leader', 'crew_specialist') and len(crews) == 1:
            contact['crew_id'] = crews[0]['id']
        card_phone = str((source.get('prefilled_card') or {}).get('phone', '')).strip()
        # The applicant is dialled by the number shown in the incoming card.
        allocated = card_phone if contact.get('role') == 'applicant' and card_phone else str(8100 + index)
        contact = PhoneContact.model_validate({**contact, 'number': allocated}).model_dump()
        if strict and accepted and contact['role'] in ('organization_head', 'crew_specialist', 'response_chief'):
            if not contact['facts']:
                raise ValueError('Generate role-specific initial facts for coordination contacts')
            if contact['required'] and not contact['required_keywords']:
                raise ValueError('Required coordination call needs verifiable transmitted facts')
        if strict and accepted and contact['role'] == 'crew_specialist' and not contact['crew_id']:
            raise ValueError('Crew specialist must reference its crew')
        if contact['crew_id'] and contact['crew_id'] not in ids:
            raise ValueError('Contact references an unknown crew')
        if not accepted and contact['required']:
            raise ValueError('Refusal exercise must not require operational phone calls')
        for fact in contact['facts']:
            if re.search(COMPLETED, fact.replace('ё', 'е'), re.I) and fact not in initial:
                raise ValueError('Contact facts reveal a future completed action')
        if strict and accepted and contact['role'] in ('organization_head', 'crew_specialist') \
                and not adds_information(contact['facts'], initial):
            # A call must be worth making: the contact knows something the card does not say.
            raise ValueError(contact['role'] + ' facts repeat the card; give this contact own information not in the card')
        available_facts = [str(value) for value in (source.get('prefilled_card') or {}).values()
                           if isinstance(value, str)] + ([] if strict else contact['facts'])
        # The dispatcher can only pass on what the incoming card says; the
        # contact's own knowledge is what the dispatcher learns in this call.
        if any(not any(asserted(fragment, word) or mentioned(fragment, word) for fragment in available_facts)
               for word in contact['required_keywords']):
            raise ValueError('Contact criterion is not grounded in available facts')
        normalized.append(contact)
    roles = {contact['role'] for contact in normalized}
    if 'superior' not in roles:
        raise ValueError('Directory needs a shift chief')
    if accepted:
        if not {'crew_leader', 'organization_head', 'crew_specialist'} <= roles:
            raise ValueError('Generate chief, organization head, crew leader and specialist')
        for crew in crews:
            leaders = [contact for contact in normalized if contact['role'] == 'crew_leader' and contact['crew_id'] == crew['id']]
            if len(leaders) != 1:
                raise ValueError('Every crew needs exactly one reachable leader')
            crew['phone'] = leaders[0]['number']
            crew['leader'] = leaders[0]['name']
    source['phone_contacts'] = normalized
    chief = next(contact for contact in normalized if contact['role'] == 'superior')
    card = source.setdefault('prefilled_card', {})
    card['services'] = list(dict.fromkeys([*card.get('services', []), owner]))
    card['service_phones'] = {owner: chief['number']}
    expectation = source.setdefault('dds_expectation', {})
    if accepted and (expectation.get('refusal_kind') is None or
                     str(expectation.get('refusal_kind', '')).strip().casefold() == 'none'):
        expectation['refusal_kind'] = ''
    expectation['brief_service'] = owner if accepted else ''
    if accepted:
        if not expectation.get('expected_crew_id'):
            expectation['expected_crew_id'] = crews[0]['id']
        if expectation['expected_crew_id'] not in ids:
            raise ValueError('Expected crew is absent')
        if not expectation.get('brief_keywords'):
            expectation['brief_keywords'] = keywords(card.get('incident_type', ''))
        updates = source.get('updates') or []
        stages = ['Начало реагирования', 'Прибытие', 'Проведение работ', 'Работы завершены']
        for index, update in enumerate(updates):
            update.setdefault('id', f'report_{index + 1}')
            update.setdefault('after_seconds', (index + 1) * 30)
            update.setdefault('source', crews[0]['leader'])
            if index < len(stages):
                update.setdefault('unlocks_status', stages[index])
        known_ids = {update['id'] for update in updates}
        if len(known_ids) != len(updates):
            raise ValueError('Update identifiers must be unique')
        update_keywords = expectation.setdefault('update_keywords', {})
        if set(update_keywords) - known_ids:
            raise ValueError('Criteria reference an absent update')
        for update in updates:
            expected = update_keywords.setdefault(update['id'], keywords(update['text']))
            if not expected or any(not asserted(update['text'], word) for word in expected):
                raise ValueError('Update criteria are not grounded in its report')
        if not expectation.get('result_keywords') and updates:
            expectation['result_keywords'] = keywords(updates[-1]['text'])
    elif source.get('updates'):
        raise ValueError('Refusal exercise cannot have an operational work cycle')
    if not accepted:
        expectation.update(expected_crew_id='', leadership_decision_required=False, update_keywords={},
                           brief_keywords=[], result_keywords=[])
    source['owner_service'] = owner
    return source


def prepare_plan(scenario):
    scenario['practice_plan'] = practice_draft(scenario)
    scenario['practice_approved_version'] = ''
    return scenario
