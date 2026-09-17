import pytest
from technical_config import DEFAULTS,validate,parse_xml,export_xml


def test_roundtrip_and_reject_unsafe_xml():
    assert parse_xml(export_xml(DEFAULTS))==DEFAULTS
    for source in ['<!DOCTYPE x><trainer112-settings version="1"/>',
                   '<trainer112-settings version="1"><setting key="PASSWORD">x</setting></trainer112-settings>',
                   '<trainer112-settings version="1"><setting key="max_calls">1</setting><setting key="max_calls">2</setting></trainer112-settings>']:
        with pytest.raises(ValueError):parse_xml(source)
    for values in [{'max_calls':True},{'backend_cpus':0},{'sip_external_address':'host;cmd'},
                   {'allowed_extensions':'201\nEVIL=1'},{'password':'secret'}]:
        with pytest.raises(ValueError):validate(values)


def test_llm_profile_setting():
    """Профиль модели — обычная настройка администратора с проверкой по списку."""
    from technical_config import DEFAULTS, ENV_KEYS, validate, export_xml, parse_xml
    from llm import PROFILES

    assert DEFAULTS['llm_profile'] == 'mock'
    assert ENV_KEYS['llm_profile'] == 'LLM_PROFILE'
    for name in PROFILES:
        assert validate({'llm_profile': name}) == {'llm_profile': name}
    for bad in ('turbo', '', 'MOCK', 'ollama'):
        try:
            validate({'llm_profile': bad})
        except ValueError:
            continue
        raise AssertionError(f'профиль {bad!r} должен быть отклонён')


def test_llm_profile_survives_xml_round_trip():
    from technical_config import DEFAULTS, export_xml, parse_xml
    values = {**DEFAULTS, 'llm_profile': 'standard'}
    assert parse_xml(export_xml(values))['llm_profile'] == 'standard'
