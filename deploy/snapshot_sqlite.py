"""Make a standalone migration backup. Stop the native Backend before using it."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def snapshot(source_path, target_path):
    if not source_path.is_file():
        raise ValueError('Source database does not exist')
    target_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation prevents accidental overwrite of an earlier backup.
    with target_path.open('xb'):
        pass
    source = sqlite3.connect(source_path.resolve().as_uri() + '?mode=ro', uri=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
        # A single read-only Docker bind mount cannot create WAL/SHM sidecars.
        # Change only the COPY to rollback-journal mode, not the live source.
        assert target.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed')
    finally:
        target.close()
        source.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'backend/dialogue.sqlite3')
    parser.add_argument('--out', type=Path, default=ROOT / 'deploy/backups' /
                        ('sqlite-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3'))
    args = parser.parse_args()
    snapshot(args.source, args.out)
    print(f'Verified standalone SQLite snapshot: {args.out}')
