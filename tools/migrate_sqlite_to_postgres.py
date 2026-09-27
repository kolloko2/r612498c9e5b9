"""Safely copy a stopped Backend SQLite database into an empty PostgreSQL DB.

The destination schema must already exist (normally by starting the Backend once).
The command is read-only unless --apply is supplied and refuses a non-empty target.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


ALLOWED_TABLES = {
    "sessions", "scenarios", "scenario_owners", "workspace", "rubrics",
    "rubric_history", "lessons", "account_users", "account_sessions",
    "account_login_failures", "account_audit", "learning_groups",
    "group_members", "assignments", "generation_drafts", "teacher_corrections", "materials",
    "assessment_policies", "assessment_policy_history", "expert_reviews",
    "group_insights", "directory_links", "arm_vis_deliveries", "arm_vis_audit", "arm_recipient_settings",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="PostgreSQL URL (defaults to DATABASE_URL; it is never printed)")
    parser.add_argument("--apply", action="store_true", help="perform the copy after all safety checks")
    return parser.parse_args()


def quoted(name: str) -> str:
    if name not in ALLOWED_TABLES and not name.replace("_", "").isalnum():
        raise ValueError("unsafe SQL identifier")
    return '"' + name.replace('"', '""') + '"'


def sqlite_tables(source):
    names = {
        row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    unknown = names - ALLOWED_TABLES
    if unknown:
        raise RuntimeError("source contains unsupported tables: " + ", ".join(sorted(unknown)))
    return sorted(names)


def main():
    args = parse_args()
    if not args.database_url:
        raise SystemExit("Set DATABASE_URL or pass --database-url")
    if not args.database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("DATABASE_URL must be a PostgreSQL URL")
    if not args.sqlite_path.is_file():
        raise SystemExit("SQLite source file does not exist")
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise SystemExit("Install backend/requirements.txt before migration") from None

    # URI read-only mode prevents accidental changes and reads a consistent stopped
    # database. The operator must stop Backend first so WAL contents are checkpointed.
    source_uri = args.sqlite_path.resolve().as_uri() + "?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    try:
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        tables = sqlite_tables(source)
        counts = {table: source.execute(f"SELECT COUNT(*) FROM {quoted(table)}").fetchone()[0] for table in tables}

        with psycopg.connect(args.database_url, autocommit=not args.apply) as target:
            existing = {
                row[0] for row in target.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
                )
            }
            missing = set(tables) - existing
            if missing:
                raise RuntimeError("destination schema is not initialized; missing: " + ", ".join(sorted(missing)))

            target_tables = sorted(existing & ALLOWED_TABLES)
            target_counts = {
                table: target.execute(sql.SQL("SELECT COUNT(*) FROM {}") .format(sql.Identifier(table))).fetchone()[0]
                for table in target_tables
            }
            seed_id = json.loads((Path(__file__).parents[1] / "backend" / "scenario.json").read_text(encoding="utf-8"))["id"]
            unsafe = {table: count for table, count in target_counts.items() if count and table != "scenarios"}
            if target_counts.get("scenarios", 0):
                ids = [row[0] for row in target.execute("SELECT id FROM scenarios")]
                if ids != [seed_id] or source.execute("SELECT 1 FROM scenarios WHERE id=?", (seed_id,)).fetchone() is None:
                    unsafe["scenarios"] = len(ids)
            if unsafe:
                raise RuntimeError("destination is not empty; refusing to overwrite application data")

            total = sum(counts.values())
            print(f"Preflight OK: {len(tables)} tables, {total} rows; destination credentials were not displayed.")
            if not args.apply:
                print("Dry run only. Re-run with --apply while both Backend instances are stopped.")
                return

            for table in tables:
                columns = [row[1] for row in source.execute(f"PRAGMA table_info({quoted(table)})")]
                if not columns:
                    continue
                pg_columns = {
                    row[0] for row in target.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND table_name=%s", (table,)
                    )
                }
                if set(columns) != pg_columns:
                    raise RuntimeError(f"schema mismatch for {table}")
                placeholders = sql.SQL(",").join(sql.Placeholder() for _ in columns)
                insert = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
                    sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    placeholders,
                )
                if table == "scenarios":
                    target.execute("DELETE FROM scenarios WHERE id=%s", (seed_id,))
                rows = source.execute(f"SELECT * FROM {quoted(table)}")
                with target.cursor() as cursor:
                    cursor.executemany(insert, rows)
            copied = sum(target.execute(sql.SQL("SELECT COUNT(*) FROM {}") .format(sql.Identifier(t))).fetchone()[0] for t in tables)
            if copied != total:
                raise RuntimeError(f"row-count verification failed ({copied} != {total})")
        print(f"Migration complete: {total} rows copied and verified.")
    finally:
        source.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, sqlite3.Error) as exc:
        print(f"Migration refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as exc:
        if exc.__class__.__module__.startswith("psycopg"):
            print("Migration refused: PostgreSQL operation failed (connection details hidden)", file=sys.stderr)
            raise SystemExit(2) from None
        raise
