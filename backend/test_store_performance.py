"""Storage configuration checks supporting the synthetic load acceptance test."""

import os
import sqlite3

os.environ.setdefault("DIALOGUE_DB", ":memory:")
os.environ.setdefault("LLM_PROVIDER", "mock")

from server import Store


def test_file_store_uses_wal_without_weakening_synchronous(tmp_path):
    store = Store(str(tmp_path / "durable.sqlite3"))
    try:
        assert store.db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store.db.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
        assert store.db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        store.db.close()


def test_wal_allows_a_reader_while_a_write_transaction_is_open(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    store = Store(str(path))
    reader = sqlite3.connect(path, timeout=0.1)
    try:
        store.db.execute("INSERT INTO sessions VALUES ('existing','{}')")
        store.db.commit()
        store.db.execute("BEGIN IMMEDIATE")
        store.db.execute("INSERT INTO sessions VALUES ('pending','{}')")

        # A rollback journal can make read/write phases interfere; WAL readers
        # retain the last committed snapshot and never observe the pending row.
        assert reader.execute("SELECT id FROM sessions ORDER BY id").fetchall() == [("existing",)]
    finally:
        store.db.rollback()
        reader.close()
        store.db.close()
