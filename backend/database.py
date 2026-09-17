"""Small DB-API compatibility layer for local SQLite and deployed PostgreSQL."""

from __future__ import annotations

import json
import re
import sqlite3
import threading


def _sqlite_json_text(body: str, key: str):
    try:
        value = json.loads(body).get(key)
    except (AttributeError, TypeError, ValueError):
        return None
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _postgres_sql(sql: str) -> str:
    """Translate the deliberately small portable SQL dialect used by Backend."""
    sql = re.sub(
        r"json_text\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,\s*(\?|%s)\s*\)",
        lambda m: f"jsonb_extract_path_text({m.group(1)}::jsonb, {m.group(2)})",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"json_text\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,\s*'([^']+)'\s*\)",
        lambda m: f"jsonb_extract_path_text({m.group(1)}::jsonb, '{m.group(2)}')",
        sql,
        flags=re.IGNORECASE,
    )
    # No application query contains a question mark inside a SQL string literal.
    return sql.replace("?", "%s")


class Database:
    """Connection facade with consistent execute/context semantics.

    PostgreSQL runs in autocommit mode for standalone reads and schema setup.
    ``with db`` opens a real transaction. The one exceptional legacy explicit
    BEGIN transaction is also serialized by the same process lock.
    """

    def __init__(self, target: str):
        self.target = target
        self.is_postgres = target.startswith(("postgresql://", "postgres://"))
        self._lock = threading.RLock()
        self._transactions = []
        self._explicit_lock = False
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - configuration failure
                raise RuntimeError(
                    "PostgreSQL requires psycopg; install backend/requirements.txt"
                ) from exc
            self.connection = psycopg.connect(target, autocommit=True)
        else:
            self.connection = sqlite3.connect(target, check_same_thread=False)
            self.connection.create_function("json_text", 2, _sqlite_json_text)
            if target != ":memory:":
                self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA busy_timeout=5000")

    def execute(self, sql: str, parameters=()):
        if self.is_postgres:
            normalized = sql.strip().upper()
            if normalized == "BEGIN IMMEDIATE":
                self._lock.acquire()
                self._explicit_lock = True
                try:
                    self.connection.execute("BEGIN")
                    # SQLite BEGIN IMMEDIATE excludes a competing first-admin
                    # writer. Preserve that invariant across Backend processes.
                    return self.connection.execute("LOCK TABLE account_users IN EXCLUSIVE MODE")
                except Exception:
                    self._explicit_lock = False
                    self._lock.release()
                    raise
            sql = _postgres_sql(sql).replace(" BLOB", " BYTEA")
        with self._lock:
            return self.connection.execute(sql, parameters)

    def commit(self):
        try:
            return self.connection.commit()
        finally:
            if self._explicit_lock:
                self._explicit_lock = False
                self._lock.release()

    def rollback(self):
        try:
            return self.connection.rollback()
        finally:
            if self._explicit_lock:
                self._explicit_lock = False
                self._lock.release()

    def close(self):
        return self.connection.close()

    def __enter__(self):
        self._lock.acquire()
        try:
            if self.is_postgres:
                transaction = self.connection.transaction()
                transaction.__enter__()
                self._transactions.append(transaction)
            else:
                self.connection.__enter__()
        except Exception:
            self._lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.is_postgres:
                return self._transactions.pop().__exit__(exc_type, exc, traceback)
            return self.connection.__exit__(exc_type, exc, traceback)
        finally:
            self._lock.release()


def connect_database(target: str) -> Database:
    return Database(target)
