"""Create a private Compose ENV file without replacing existing credentials."""
import argparse
import json
import os
from pathlib import Path
import secrets

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def settings(source):
    previous = dotenv_values(source) if source.exists() else {}
    # Контур поставки — локальный: модель обслуживает Ollama на хосте. Если
    # прежний ENV ничего не задавал, берётся 'ollama': занятие всё равно идёт и
    # без модели, но выбор по умолчанию должен быть локальным, а не внешним.
    provider = previous.get('LLM_PROVIDER', 'ollama')
    if provider not in ('mock', 'ollama', 'openrouter'):
        provider = 'ollama'
    accounts = {str(n): secrets.token_hex(20) for n in range(201, 221)}
    return {
        'POSTGRES_PASSWORD': secrets.token_hex(32),
        'DIALOGUE_TOKEN': secrets.token_hex(32),
        'VOICE_API_TOKEN': secrets.token_hex(32),
        'ARI_PASSWORD': secrets.token_hex(24),
        'MEDIA_PASSWORD': secrets.token_hex(24),
        'SIP_ACCOUNTS_JSON': json.dumps(accounts, separators=(',', ':')),
        'ALLOWED_EXTENSIONS': ','.join(accounts),
        'LLM_PROVIDER': provider,
        'OPENROUTER_API_KEY': previous.get('OPENROUTER_API_KEY') or '',
        'OPENROUTER_MODEL': previous.get('OPENROUTER_MODEL') or 'openai/gpt-4o-mini',
        # Адрес Ollama на хосте и профиль модели. Профили описаны в
        # docs/LOCAL_MODEL.md; 'mock' в LLM_PROVIDER отключает модель целиком.
        'OLLAMA_URL': previous.get('OLLAMA_URL') or 'http://host.docker.internal:11434',
        'LLM_PROFILE': previous.get('LLM_PROFILE') or 'standard',
        'WEB_BIND_ADDRESS': '127.0.0.1',
        'ALLOWED_ORIGINS': 'http://127.0.0.1:3000,http://localhost:3000',
        'COOKIE_SECURE': 'false',
        'SIP_BIND_ADDRESS': '127.0.0.1',
        'SIP_EXTERNAL_ADDRESS': '127.0.0.1',
        'SIP_LOCAL_NET': '172.16.0.0/12',
        'PIPELINE_MODE': 'spike',
        'TOPOLOGY_VERIFIED': 'false',
        'INSTALL_LOCAL_PROVIDERS': 'false',
        'STT_PROVIDER': 'mock',
        'TTS_PROVIDER': 'mock',
    }


def create(source, output):
    values = settings(source)
    for key, value in values.items():
        if any(c in value for c in "\r\n'\\"):
            raise ValueError(f'Unsafe single-line ENV value for {key}')
    # Single quotes disable Compose interpolation inside values, including keys.
    content = '# Private deployment configuration. Never commit or share.\n'
    content += ''.join(f"{key}='{value}'\n" for key, value in values.items())
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(content)
    from full_backup import ensure_key
    ensure_key(ROOT)


def report_assets():
    """Сообщить, каких необязательных ресурсов не хватает, и как их получить.

    Стенд поднимается без них: без речевых моделей не работает распознавание и
    синтез в SIP, без модели генерация сценариев, без карты — окно карты.
    Занятие, карточки, нормативы и доклад дежурному идут в любом случае.
    """
    models = ROOT / 'deploy' / 'models'
    missing = []
    if not (models / 'vosk-model-small-ru-0.22' / 'am').exists():
        missing.append('vosk')
    if not (models / 'sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19' / 'model.int8.onnx').exists():
        missing.append('gigaam')
    if not (models / 'v5_5_ru.pt').is_file():
        missing.append('silero')
    if missing:
        print(f"Речевые модели не найдены ({', '.join(missing)}): python tools/fetch_models.py")
    if not (ROOT / 'deploy' / 'maps' / 'regional.sqlite').is_file():
        print('Карта не найдена: python tools/prepare_regional_map.py --download')
    print('Локальная модель: python tools/fetch_llm.py')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT / '.env')
    parser.add_argument('--out', type=Path, default=ROOT / '.env.docker')
    args = parser.parse_args()
    try:
        create(args.source, args.out)
    except FileExistsError:
        parser.exit(1, 'Deployment ENV already exists; left unchanged.\n')
    (ROOT / 'deploy' / 'models').mkdir(exist_ok=True)
    print('Created private deployment ENV. Existing .env/database unchanged; no secrets printed.')
    report_assets()
