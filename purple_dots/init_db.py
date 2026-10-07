"""Create the Purple Dots tables in a local Postgres.

    docker compose up -d
    $env:DATABASE_URL = "postgresql://purple:purple@localhost:5433/purple"
    python init_db.py

The .sql files in sql/ are the only description of the schema; this applies
them. Re-runnable - every statement is CREATE ... IF NOT EXISTS.
"""
import os
import pathlib

import db

FILES = ["sql/create_purple_dots.sql", "sql/create_purple_dots_s3.sql"]


def main():
    db.require_url()
    print(db.describe())

    # The .sql files grant to service_role, anon and authenticated - roles
    # Supabase provided and a bare Postgres does not. Creating them NOLOGIN
    # is cheaper than editing every grant out, and keeps the files readable
    # as the one description of the schema.
    db.ensure_supabase_roles()
    print("  ok    supabase roles (service_role, anon, authenticated)")

    here = pathlib.Path(__file__).resolve().parent
    for name in FILES:
        path = here / name
        if not path.exists():
            print(f"  skip  {name} (missing)")
            continue
        db.run_sql_file(path)
        print(f"  ok    {name}")
    print()
    for table in ("purple_dots_calls", "purple_dots_connections",
                  "purple_users", "purple_items", "purple_actions"):
        count = db.table_count(table)
        print(f"  {table:<26}{'missing' if count is None else f'{count} rows'}")


if __name__ == "__main__":
    main()
