"""Postgres access. DATABASE_URL is the only setting.

The Supabase build is on `master`; keep the two in step.
"""
import os
from contextlib import contextmanager

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.sql as pgsql
except ImportError:
    psycopg2 = pgsql = None


def describe():
    """One line naming the database."""
    url = os.getenv("DATABASE_URL", "")
    if not url:
        return "(DATABASE_URL not set)"
    # Never print the password: postgresql://user:pass@host/db
    return f"Postgres  {url.split('@')[-1] if '@' in url else url}"


def require_url():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set.\n"
            '  $env:DATABASE_URL = "postgresql://purple:<password>@localhost:5433/purple"\n'
            "  (or put it in .env - see .env.example)")
    return url


# Bounded so a wedged connection cannot hang a scheduled run indefinitely.
CONNECT_TIMEOUT = int(os.getenv("PD_CONNECT_TIMEOUT", "10"))
STATEMENT_TIMEOUT_MS = int(os.getenv("PD_STATEMENT_TIMEOUT_MS", "300000"))


def connect():
    if psycopg2 is None:
        raise SystemExit("psycopg2 is not installed.\n"
                         "  pip install psycopg2-binary")
    return psycopg2.connect(
        require_url(),
        connect_timeout=CONNECT_TIMEOUT,
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}")


def expected_database():
    """(ok, detail) for PD_EXPECTED_DATABASE against current_database().

    The old Supabase build refused to write to the Blue Dots project. This
    is the Postgres equivalent: a DATABASE_URL pasted from the wrong
    environment names a different database, and the run stops before it
    writes anything. Unset means no check (local use).
    """
    want = os.getenv("PD_EXPECTED_DATABASE")
    if not want:
        return True, "not checked (PD_EXPECTED_DATABASE unset)"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("select current_database()")
        have = cur.fetchone()[0]
    if have != want:
        return False, f"connected to {have!r}, PD_EXPECTED_DATABASE is {want!r}"
    return True, have


def assert_expected_database():
    good, detail = expected_database()
    if not good:
        raise SystemExit(f"Wrong database: {detail}. Check DATABASE_URL.")


@contextmanager
def _conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_JSON_COLUMNS = {}


def json_columns(cur, table):
    """Which columns are json/jsonb. Cached per table.

    Needed because a Python list means two different things here:
    purple_dots_calls has text[] columns (tools_used,
    disability_category_mapped, ...) which psycopg2 adapts natively, while
    the platform tables have jsonb which needs Json(). Wrapping everything
    gave 'malformed array literal: "[\"Low Vision\"]"' on every call that
    used a tool - which is nearly all of them.
    """
    if table not in _JSON_COLUMNS:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = %s "
            "  and data_type in ('json', 'jsonb')", (table,))
        _JSON_COLUMNS[table] = {r[0] for r in cur.fetchall()}
    return _JSON_COLUMNS[table]


def table_count(table):
    """Rows in the table, or None if it does not exist."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("select to_regclass(%s)", (f"public.{table}",))
        if cur.fetchone()[0] is None:
            return None
        cur.execute(pgsql.SQL("select count(*) from public.{}").format(
            pgsql.Identifier(table)))
        return cur.fetchone()[0]


def column_values(table, column):
    """Every non-null value of one column. Used for 'what is already loaded'."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(pgsql.SQL(
            "select {col} from public.{tbl} where {col} is not null").format(
                col=pgsql.Identifier(column), tbl=pgsql.Identifier(table)))
        return [row[0] for row in cur.fetchall()]


def can_write(table):
    """(ok, detail). INSERTs and rolls back.

    Must be an INSERT: an UPDATE matching no rows evaluates no policy or
    column privilege, so it passes for a user who cannot insert.
    """
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                # every NOT NULL column without a default
                cur.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema = 'public' and table_name = %s "
                    "  and is_nullable = 'NO' and column_default is null",
                    (table,))
                cols = [r[0] for r in cur.fetchall()] or ["call_id"]
                cur.execute(
                    pgsql.SQL("insert into public.{} ({}) values ({})").format(
                        pgsql.Identifier(table),
                        pgsql.SQL(", ").join(map(pgsql.Identifier, cols)),
                        pgsql.SQL(", ").join(pgsql.Placeholder() * len(cols))),
                    tuple("__preflight_probe__" for _ in cols))
            conn.rollback()
            return True, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc).strip().splitlines()[0][:80]


def upsert(table, rows, conflict, chunk=200, preserve=()):
    """Insert rows, updating on conflict. Returns how many were sent.

    `conflict` is a comma-separated key list. `preserve` names columns that
    keep their stored value on conflict - set on insert, never overwritten.
    test_flag and call_value_score are decided outside this pipeline, so a
    re-fetch must not reset them to the NULL make_row produces.
    """
    if not rows:
        return 0
    cols = conflict.split(",")
    done = 0
    with _conn() as conn, conn.cursor() as cur:
        jsonb = json_columns(cur, table)
        for start in range(0, len(rows), chunk):
            batch = rows[start:start + chunk]
            # Every row in one statement must carry the same columns, and a
            # key may not repeat within it - ON CONFLICT cannot update a row
            # the same statement just inserted (Postgres raises 21000).
            keys = sorted({k for r in batch for k in r})
            seen, squared = set(), []
            for r in batch:
                ident = tuple(r.get(c) for c in cols)
                if ident in seen:
                    continue
                seen.add(ident)
                squared.append(tuple(
                    psycopg2.extras.Json(r[k])
                    if k in jsonb and isinstance(r.get(k), (dict, list))
                    else r.get(k)
                    for k in keys))
            updates = [pgsql.SQL("{0} = excluded.{0}").format(
                pgsql.Identifier(k))
                for k in keys if k not in cols and k not in preserve]
            statement = pgsql.SQL(
                "insert into public.{tbl} ({cols}) values %s "
                "on conflict ({keys}) {action}").format(
                    tbl=pgsql.Identifier(table),
                    cols=pgsql.SQL(", ").join(map(pgsql.Identifier, keys)),
                    keys=pgsql.SQL(", ").join(map(pgsql.Identifier, cols)),
                    action=(pgsql.SQL("do update set ")
                            + pgsql.SQL(", ").join(updates)
                            if updates else pgsql.SQL("do nothing")))
            psycopg2.extras.execute_values(
                cur, statement.as_string(cur), squared, page_size=chunk)
            done += len(squared)
    return done


def disable_rls(table):
    """Turn RLS off if on. True when changed. Owner only."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("select relrowsecurity from pg_class where relname = %s",
                    (table,))
        row = cur.fetchone()
        if not row or not row[0]:
            return False
        cur.execute(pgsql.SQL(
            "alter table public.{} disable row level security").format(
                pgsql.Identifier(table)))
        return True


def ensure_supabase_roles():
    """Create service_role, anon, authenticated as NOLOGIN grant targets.

    sql/ grants to them; Supabase provides them, plain Postgres does not.
    """
    with _conn() as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role"):
            cur.execute("select 1 from pg_roles where rolname = %s", (role,))
            if not cur.fetchone():
                cur.execute(pgsql.SQL("create role {} nologin").format(
                    pgsql.Identifier(role)))


def run_sql_file(path):
    """Apply a .sql file."""
    with open(path, encoding="utf-8") as handle:
        sql = handle.read()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
    return path
