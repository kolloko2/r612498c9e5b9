import importlib.util
from pathlib import Path

import pytest
from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location('prepare', Path(__file__).with_name('prepare.py'))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_private_configuration_and_no_overwrite(tmp_path):
    source, target = tmp_path / 'source.env', tmp_path / 'docker.env'
    source.write_text('LLM_PROVIDER=openrouter\nOPENROUTER_API_KEY=synthetic-key\n', encoding='utf-8')
    prepare.create(source, target)
    values = dotenv_values(target)
    assert values['OPENROUTER_API_KEY'] == 'synthetic-key'
    assert values['LLM_PROVIDER'] == 'openrouter'
    assert values['PIPELINE_MODE'] == 'spike'
    assert values['TOPOLOGY_VERIFIED'] == 'false'
    accounts = prepare.json.loads(values['SIP_ACCOUNTS_JSON'])
    assert list(accounts) == [str(n) for n in range(201, 221)]
    assert len(set(accounts.values())) == 20
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        prepare.create(source, target)
    assert target.read_bytes() == original


def test_no_local_llm_selected_and_reject_multiline_key(tmp_path):
    source = tmp_path / 'source.env'
    source.write_text('LLM_PROVIDER=ollama\n', encoding='utf-8')
    assert prepare.settings(source)['LLM_PROVIDER'] == 'mock'
    source.write_text('OPENROUTER_API_KEY="bad\\nkey"\n', encoding='utf-8')
    with pytest.raises(ValueError):
        prepare.create(source, tmp_path / 'target.env')
