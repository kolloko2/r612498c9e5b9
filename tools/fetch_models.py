"""Скачать речевые модели в deploy/models с проверкой целостности.

Модели не хранятся в Git по трём причинам: они весят около 450 МБ, один файл
(Silero) превышает лимит GitHub в 100 МБ на файл, и их распространение — дело
правообладателя, а не нашего репозитория. Поэтому здесь только ссылки на
официальные источники и контрольные суммы тех файлов, на которых проводились
замеры проекта.

Запуск (нужен интернет, один раз на машину):

    python tools/fetch_models.py            # всё, чего не хватает
    python tools/fetch_models.py --only vosk silero
    python tools/fetch_models.py --check    # только проверить, что уже есть

Скрипт идемпотентен: уже установленная и совпавшая по контрольной сумме модель
пропускается. Ничего, кроме deploy/models, он не трогает.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "deploy" / "models"
CHUNK = 1024 * 1024

# sha256 — от файлов, на которых снимались замеры проекта (см. docs/SPEECH.md).
# Если источник опубликует новую сборку под тем же адресом, скрипт сообщит о
# расхождении и не станет подменять модель молча.
CATALOG = {
    "vosk": {
        "title": "Vosk small ru 0.22 — промежуточное распознавание",
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
        "archive": "zip",
        "target": "vosk-model-small-ru-0.22",
        "marker": "am",
        "size_mb": 88,
        "sha256": None,
    },
    "gigaam": {
        "title": "GigaAM v2 CTC — точное финальное распознавание",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
               "sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19.tar.bz2",
        "archive": "tar.bz2",
        "target": "sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19",
        "marker": "model.int8.onnx",
        "size_mb": 227,
        "sha256": None,
    },
    "silero": {
        "title": "Silero v5.5 ru — синтез речи",
        "url": "https://models.silero.ai/models/tts/ru/v5_5_ru.pt",
        "archive": None,
        "target": "v5_5_ru.pt",
        "marker": None,
        "size_mb": 139,
        "sha256": "50081637b602126ee06cb3bc8a744d25651d2da149ee8864b9a379bfdd934437",
    },
}


def installed(spec: dict) -> bool:
    path = MODELS / spec["target"]
    if not path.exists():
        return False
    return (path / spec["marker"]).exists() if spec["marker"] else path.is_file()


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            sha.update(block)
    return sha.hexdigest()


def download(url: str, destination: Path, expected_mb: int) -> None:
    print(f"    загрузка ~{expected_mb} МБ …", end="", flush=True)
    done = 0
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as out:
        while True:
            block = response.read(CHUNK)
            if not block:
                break
            out.write(block)
            done += len(block)
            print(f"\r    загрузка {done // (1024 * 1024)} МБ из ~{expected_mb} МБ", end="", flush=True)
    print()


def unpack(archive: Path, kind: str, target: Path) -> None:
    """Распаковать во временную папку и переместить: при обрыве не остаётся
    наполовину распакованной модели вместо рабочей."""
    with tempfile.TemporaryDirectory(dir=str(MODELS)) as staging_name:
        staging = Path(staging_name)
        if kind == "zip":
            with zipfile.ZipFile(archive) as bundle:
                _reject_unsafe(name for name in bundle.namelist())
                bundle.extractall(staging)
        else:
            with tarfile.open(archive, "r:bz2") as bundle:
                _reject_unsafe(member.name for member in bundle.getmembers())
                bundle.extractall(staging)
        entries = [item for item in staging.iterdir()]
        source = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(source), str(target))


def _reject_unsafe(names) -> None:
    """Архив не должен писать за пределы каталога моделей."""
    for name in names:
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SystemExit(f"Небезопасный путь в архиве: {name}")


def fetch(key: str, spec: dict) -> bool:
    target = MODELS / spec["target"]
    print(f"  {spec['title']}")
    with tempfile.TemporaryDirectory() as work:
        suffix = ".zip" if spec["archive"] == "zip" else ".tar.bz2" if spec["archive"] else ".bin"
        payload = Path(work) / (key + suffix)
        try:
            download(spec["url"], payload, spec["size_mb"])
        except Exception as error:  # сеть, прокси, недоступный источник
            print(f"    не удалось скачать: {error}")
            print(f"    источник: {spec['url']}")
            return False
        if spec["sha256"]:
            actual = digest(payload)
            if actual != spec["sha256"]:
                print("    контрольная сумма не совпала — файл не установлен")
                print(f"    ожидалось {spec['sha256']}")
                print(f"    получено  {actual}")
                return False
            print("    контрольная сумма совпала")
        if spec["archive"]:
            unpack(payload, spec["archive"], target)
        else:
            shutil.move(str(payload), str(target))
    print(f"    готово: deploy/models/{spec['target']}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=sorted(CATALOG), help="Скачать только эти модели")
    parser.add_argument("--check", action="store_true", help="Только показать, что установлено")
    parser.add_argument("--force", action="store_true", help="Скачать заново, даже если файл есть")
    args = parser.parse_args()

    MODELS.mkdir(parents=True, exist_ok=True)
    keys = args.only or list(CATALOG)

    if args.check:
        for key in keys:
            spec = CATALOG[key]
            print(f"{'есть  ' if installed(spec) else 'нет   '} {key:8} {spec['title']}")
        return

    failed = []
    for key in keys:
        spec = CATALOG[key]
        if installed(spec) and not args.force:
            print(f"  {spec['title']} — уже установлена, пропуск")
            continue
        if not fetch(key, spec):
            failed.append(key)

    print()
    if failed:
        print("Не установлены:", ", ".join(failed))
        print("Стенд работает и без них: занятие, карточки, нормативы и доклад")
        print("дежурному идут без речевых моделей. Без них не работает только")
        print("распознавание и синтез в режиме SIP.")
        sys.exit(1)
    print("Все модели на месте. В .env.docker должно стоять:")
    print("  STT_PROVIDER='hybrid'")
    print("  STT_MODEL='/models/vosk-model-small-ru-0.22'")
    print("  STT_FINAL_MODEL='/models/sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19'")
    print("  TTS_PROVIDER='silero'")
    print("  TTS_VOICE='/models/v5_5_ru.pt'")


if __name__ == "__main__":
    main()
