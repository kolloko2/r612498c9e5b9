"""The DDS may call the applicant only via the number shown in the card and only when listed."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

import phone_directory
from dds_authoring import assemble
from server import Scenario
from simple_dds import scenarios


def with_applicant(number):
    scenario = deepcopy(scenarios()[0])
    scenario['prefilled_card']['phone'] = '+7 900 000-00-12'
    scenario['phone_contacts'].append(dict(id='applicant', name='Заявитель Иванова', role='applicant', number=number,
                                           facts=['Мешки с мусором стоят у третьего подъезда.']))
    return scenario


def test_applicant_number_must_match_card_phone():
    Scenario.model_validate(with_applicant('79000000012'))
    with pytest.raises(ValidationError, match='совпадать с телефоном'):
        Scenario.model_validate(with_applicant('8199'))


def test_generated_applicant_keeps_card_number_while_others_get_training_numbers():
    source = with_applicant('1')
    result = assemble(source, 'Деп. ЖКХ')
    applicant = next(c for c in result['phone_contacts'] if c['role'] == 'applicant')
    assert phone_directory.number(applicant['number']) == '79000000012'
    assert all(c['number'].startswith('81') for c in result['phone_contacts'] if c['role'] != 'applicant')


def test_applicant_is_public_and_dialable_with_title():
    contact = with_applicant('79000000012')['phone_contacts'][-1]
    view = phone_directory.public_contact(contact, {})
    assert view['role_title'] == 'Заявитель' and view['available']


def test_prefill_is_skipped_without_local_model(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    monkeypatch.delenv('LLM_PROFILE', raising=False)
    phone_directory.prefill({}, {'name': 'X', 'role': 'applicant'}, 'X. Слушаю вас.')  # no running loop needed


def test_strict_generation_rejects_contacts_that_only_repeat_the_card():
    source = deepcopy(scenarios()[0])
    card_text = source['prefilled_card']['description']
    for contact in source['phone_contacts']:
        if contact['role'] in ('organization_head', 'crew_specialist'):
            contact['facts'] = [card_text]
            contact['required_keywords'] = []
            contact['required'] = False
    with pytest.raises(ValueError, match='repeat the card'):
        assemble(source, 'Деп. ЖКХ', strict=True)


def test_strict_generation_rejects_result_as_incident_type():
    source = deepcopy(scenarios()[0])
    source['prefilled_card']['incident_type'] = 'Убрано'
    with pytest.raises(ValueError, match='not the result'):
        assemble(source, 'Деп. ЖКХ', strict=True)


def test_generated_applicant_gets_card_phone_and_caller_name():
    source = deepcopy(scenarios()[0])
    source['prefilled_card'].pop('phone', None)
    source['phone_contacts'].append(dict(id='applicant', name='Вера Кудрявцева', role='applicant', number='1',
                                         facts=['Мешки стоят у третьего подъезда, рядом синяя дверь.']))
    result = assemble(source, 'Деп. ЖКХ')
    card = result['prefilled_card']
    applicant = next(c for c in result['phone_contacts'] if c['role'] == 'applicant')
    assert card['phone'].startswith('+7 900') and phone_directory.number(applicant['number']) == phone_directory.number(card['phone'])
    assert card['caller_name'] == 'Вера Кудрявцева'
    Scenario.model_validate({**result, 'victim_name': 'Служба 112'})
