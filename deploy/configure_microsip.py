"""Create a new portable MicroSIP account or safely stage its TLS settings."""
import argparse
import configparser
import json
import shutil
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def configure(env_path, target, extension):
    env = dotenv_values(env_path)
    password = json.loads(env['SIP_ACCOUNTS_JSON'])[extension]
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config['Settings'] = {
        'accountId': '1', 'enableLocalAccount': '0',
        'audioCodecs': 'PCMA/8000/1 PCMU/8000/1',
        'EC': '1', 'AA': '0', 'autoAnswer': '', 'volumeInput': '90',
    }
    config['Account1'] = {
        'label': 'Training ' + extension, 'server': '127.0.0.1:5060',
        'domain': '127.0.0.1', 'username': extension, 'authID': extension,
        'password': password, 'displayName': 'Training operator ' + extension,
        'transport': 'udp', 'registerRefresh': '60', 'keepAlive': '15',
        'publish': '0', 'ICE': '0', 'allowRewrite': '1', 'disableSessionTimer': '0',
    }
    # Refuse to replace another phone account or the user's existing configuration.
    with target.open('x', encoding='utf-16') as stream:
        config.write(stream, space_around_delimiters=False)


def configure_tls(target, extension):
    """Migrate one existing local account to TLS + mandatory SDES-SRTP."""
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    if not target.is_file():
        raise FileNotFoundError(f'MicroSIP configuration not found: {target}')
    config.read(target, encoding='utf-16')
    if 'Settings' not in config or 'Account1' not in config:
        raise ValueError('MicroSIP configuration must contain Settings and Account1')
    account = config['Account1']
    if account.get('username') != extension:
        raise ValueError(f'Account1 is not extension {extension}; refusing to modify it')
    desired = {
        'server': '127.0.0.1:5061',
        'domain': '127.0.0.1',
        'transport': 'tls',
        'SRTP': 'mandatory',
    }
    settings = config['Settings']
    already_configured = all(account.get(key) == value for key, value in desired.items())
    already_configured = already_configured and settings.get('AA') == '0' and not settings.get('autoAnswer')
    if already_configured:
        return None

    backup = target.with_name(target.name + '.pre-tls.bak')
    if backup.exists():
        raise FileExistsError(f'backup already exists: {backup}')
    shutil.copy2(target, backup)
    for key, value in desired.items():
        account[key] = value
    settings['AA'] = '0'
    settings['autoAnswer'] = ''
    temporary = target.with_name('.' + target.name + '.tls.tmp')
    try:
        with temporary.open('x', encoding='utf-16') as stream:
            config.write(stream, space_around_delimiters=False)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return backup


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extension', choices=[str(n) for n in range(201, 221)], default='201')
    parser.add_argument(
        '--tls-existing', action='store_true',
        help='back up and migrate an existing account to TLS and mandatory SRTP',
    )
    args = parser.parse_args()
    phone = ROOT / 'tools/windows-training/MicroSIP'
    if not (phone / 'MicroSIP.exe').is_file():
        parser.exit(1, 'Extract the official portable MicroSIP distribution first.\n')
    target = phone / 'MicroSIP.ini'
    if args.tls_existing:
        backup = configure_tls(target, args.extension)
        state = 'already configured' if backup is None else 'configured with a local backup'
        print(f'Private portable phone TLS/SRTP {state}; automatic answering is OFF.')
    else:
        configure(ROOT / '.env.docker', target, args.extension)
        print('Private portable phone configuration created; automatic answering is OFF.')
