import configparser
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('configure_microsip', Path(__file__).with_name('configure_microsip.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_new_account_keeps_autoanswer_off_and_never_overwrites(tmp_path):
    env, target = tmp_path / 'private.env', tmp_path / 'MicroSIP.ini'
    env.write_text('SIP_ACCOUNTS_JSON=\'{"201":"synthetic-phone-password"}\'\n', encoding='utf-8')
    module.configure(env, target, '201')
    config = configparser.ConfigParser(interpolation=None)
    config.read(target, encoding='utf-16')
    assert config['Account1']['username'] == '201'
    assert config['Account1']['server'] == '127.0.0.1:5060'
    assert config['Account1']['password'] == 'synthetic-phone-password'
    assert config['Settings']['AA'] == '0'
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        module.configure(env, target, '201')
    assert target.read_bytes() == original


def test_tls_migration_backs_up_preserves_secret_and_is_idempotent(tmp_path):
    target = tmp_path / 'MicroSIP.ini'
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config['Settings'] = {'AA': '1', 'autoAnswer': '201'}
    config['Account1'] = {
        'server': '127.0.0.1:5060', 'domain': '127.0.0.1',
        'username': '201', 'password': 'never-print-this-secret',
        'transport': 'udp',
    }
    with target.open('x', encoding='utf-16') as stream:
        config.write(stream, space_around_delimiters=False)
    original = target.read_bytes()

    backup = module.configure_tls(target, '201')
    assert backup.read_bytes() == original
    migrated = configparser.ConfigParser(interpolation=None)
    migrated.optionxform = str
    migrated.read(target, encoding='utf-16')
    assert migrated['Account1']['password'] == 'never-print-this-secret'
    assert migrated['Account1']['server'] == '127.0.0.1:5061'
    assert migrated['Account1']['transport'] == 'tls'
    assert migrated['Account1']['SRTP'] == 'mandatory'
    assert migrated['Settings']['AA'] == '0'
    assert migrated['Settings']['autoAnswer'] == ''
    assert module.configure_tls(target, '201') is None


def test_tls_migration_refuses_wrong_account(tmp_path):
    target = tmp_path / 'MicroSIP.ini'
    config = configparser.ConfigParser(interpolation=None)
    config['Settings'] = {'AA': '0'}
    config['Account1'] = {'username': '202'}
    with target.open('x', encoding='utf-16') as stream:
        config.write(stream)
    with pytest.raises(ValueError, match='not extension 201'):
        module.configure_tls(target, '201')
    assert not target.with_name(target.name + '.pre-tls.bak').exists()
