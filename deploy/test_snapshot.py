import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location('snapshot_sqlite', Path(__file__).with_name('snapshot_sqlite.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_wal_copy_is_standalone_without_changing_source(tmp_path):
    source_path, target_path = tmp_path / 'source.sqlite3', tmp_path / 'copy.sqlite3'
    source = sqlite3.connect(source_path)
    source.execute('PRAGMA journal_mode=WAL')
    source.execute('CREATE TABLE sample (data BLOB)')
    source.execute('INSERT INTO sample VALUES (?)', (b'example',))
    source.commit()
    module.snapshot(source_path, target_path)
    assert source.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    with sqlite3.connect(target_path.as_uri() + '?mode=ro', uri=True) as copy:
        assert copy.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert copy.execute('SELECT data FROM sample').fetchone()[0] == b'example'
    with pytest.raises(FileExistsError):
        module.snapshot(source_path, target_path)
    source.close()
