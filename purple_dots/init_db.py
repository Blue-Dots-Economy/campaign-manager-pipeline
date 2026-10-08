"""Create the Purple Dots tables.

    docker compose up -d
    python init_db.py

Re-runnable. Applies sql/, and turns off row level security on tables
created before 7 Oct 2026 - CREATE TABLE IF NOT EXISTS will not revisit
them, and RLS with no policy blocks every INSERT from a non-owner.
"""
import pathlib

from dotenv import load_dotenv

# Same .env as the loader. Loaded before `import db` reads PD_* settings.
load_dotenv(pathlib.Path(__file__).resolve().parent / ".env")

import db  # noqa: E402

# Order matters: the dashboard views read purple_dots_calls.
FILES = ["sql/create_purple_dots.sql", "sql/create_purple_dots_s3.sql",
         "sql/create_dashboard.sql"]

DASHBOARD_TABLES = (
    "app_users", "campaign_requests", "launched_batch_inputs",
    "launched_batches", "north_star_config", "program_agents",
    "program_export_targets", "program_sync_state", "reviewers",
    "sheet_connections", "transcript_reviews",
    "call_rows", "call_rows_np", "kkb_grid",
)


def main():
    db.require_url()
    print(db.describe())
    db.assert_expected_database()

    # sql/ grants to roles Supabase provides and plain Postgres does not.
    db.ensure_supabase_roles()
    print("  ok    supabase roles (service_role, anon, authenticated)")

    for table in ("purple_dots_calls", "purple_dots_connections"):
        if db.disable_rls(table):
            print(f"  ok    row level security off on {table}")

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
                  "purple_users", "purple_items", "purple_actions",
                  *DASHBOARD_TABLES):
        count = db.table_count(table)
        print(f"  {table:<26}{'missing' if count is None else f'{count} rows'}")


if __name__ == "__main__":
    main()
