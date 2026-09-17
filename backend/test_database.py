import os
from uuid import uuid4

import pytest

from database import Database, _postgres_sql


def test_postgres_sql_translation_is_bounded():
    assert _postgres_sql("SELECT json_text(w.body,?) FROM workspace w WHERE id=?") == (
        "SELECT jsonb_extract_path_text(w.body::jsonb, %s) FROM workspace w WHERE id=%s"
    )


def test_sqlite_json_and_upsert_remain_supported():
    db = Database(":memory:")
    try:
        db.execute("CREATE TABLE records (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        with db:
            db.execute("INSERT INTO records VALUES (?,?)", ("a", '{"owner":"one"}'))
        with db:
            db.execute(
                "INSERT INTO records VALUES (?,?) ON CONFLICT (id) DO UPDATE SET body=excluded.body",
                ("a", '{"owner":"two"}'),
            )
        assert db.execute("SELECT json_text(body,'owner') FROM records WHERE id=?", ("a",)).fetchone() == ("two",)
    finally:
        db.close()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured")
def test_postgres_transactions_json_bytes_and_upsert():
    import psycopg
    from psycopg import sql

    url = os.environ["TEST_DATABASE_URL"]
    schema = "codex_test_" + uuid4().hex
    admin = psycopg.connect(url, autocommit=True)
    admin.execute(sql.SQL("CREATE SCHEMA {}") .format(sql.Identifier(schema)))
    try:
        # options is a libpq parameter and avoids touching tables in public.
        separator = "&" if "?" in url else "?"
        scoped_url = url + separator + "options=-csearch_path%3D" + schema
        db = Database(scoped_url)
        try:
            db.execute("CREATE TABLE records (id TEXT PRIMARY KEY, body TEXT NOT NULL, attachment BLOB)")
            with db:
                db.execute("INSERT INTO records VALUES (?,?,?)", ("a", '{"owner":"one"}', b"abc"))
            with pytest.raises(RuntimeError):
                with db:
                    db.execute("INSERT INTO records VALUES (?,?,?)", ("b", '{"owner":"two"}', b"def"))
                    raise RuntimeError("rollback")
            assert db.execute("SELECT json_text(body,'owner'),attachment FROM records WHERE id=?", ("a",)).fetchone() == ("one", b"abc")
            assert db.execute("SELECT 1 FROM records WHERE id=?", ("b",)).fetchone() is None
        finally:
            db.close()
    finally:
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE") .format(sql.Identifier(schema)))
        admin.close()
