"""Create the Purple Dots tables. Re-runnable.

    docker compose up -d
    python init_db.py

Shared database (public owned by the dashboard): sets up schema platform only.
"""
import pathlib

from dotenv import load_dotenv

# Same .env as the loader. Loaded before `import db` reads PD_* settings.
load_dotenv(pathlib.Path(__file__).resolve().parent / ".env")

import db  # noqa: E402

CALL_TABLES = ("purple_dots_calls", "purple_dots_connections")
PLATFORM_TABLES = ("purple_users", "purple_items", "purple_actions")
HERE = pathlib.Path(__file__).resolve().parent


def apply(name):
    db.run_sql_file(HERE / name)
    print(f"  ok    {name}")


def main():
    db.require_url()
    print(db.describe())
    db.assert_expected_database()

    if not db.is_shared_database():
        db.ensure_supabase_roles()
        print("  ok    supabase roles (service_role, anon, authenticated)")
        # RLS with no policy blocks non-owner INSERTs on pre-7-Oct tables.
        for table in CALL_TABLES:
            if db.disable_rls(table):
                print(f"  ok    row level security off on {table}")
        apply("sql/create_purple_dots.sql")
    else:
        print("  skip  sql/create_purple_dots.sql (public is owned by the dashboard's migrations)")
    apply("sql/create_purple_dots_s3.sql")

    print()
    missing = []
    for schema, tables in (("public", CALL_TABLES), (db.PLATFORM_SCHEMA, PLATFORM_TABLES)):
        for table in tables:
            count = db.table_count(table, schema)
            if count is None:
                missing.append(f"{schema}.{table}")
            print(f"  {schema + '.' + table:<34}{'missing' if count is None else f'{count} rows'}")
    if missing:
        raise SystemExit(f"\nMissing: {', '.join(missing)}. On the shared database, run the "
                         "dashboard's migrations (bun run db:migrate) first.")


if __name__ == "__main__":
    main()
