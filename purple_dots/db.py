"""Postgres access for the Purple Dots pipeline.

One connection string, DATABASE_URL:

    postgresql://purple:purple@localhost:5433/purple

WHY THERE IS NO SUPABASE PATH ANY MORE
There was one until 7 Oct 2026, and the two backends were kept in step and
verified field-for-field. Purple Dots then moved off Supabase entirely, and
a second path nobody exercises is not a safety net - it is code that rots
until the day someone relies on it. The git history has it if it is ever
needed again.

What that decision cost, and what to keep in mind:

  * There is no hosted copy. docker-compose.yml names a volume so the data
    survives `docker compose down`, but `down -v` still removes it and
    nothing is backed up anywhere.
  * Nothing is lost if it goes. Raya holds every call, so a destroyed
    database is a re-run of run_pipeline.py - hours, not data.
  * DDL runs from here now. Supabase had no API for it, so its tables were
    created by pasting sql/ into a dashboard; init_db.py applies the same
    files directly.

The functions below are what the pipeline uses. Nothing else in it builds
SQL or opens a connection.
"""
import os
from contextlib import contextmanager

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None


def describe():
    """One line naming the database, for the top of a run's output."""
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
            '  $env:DATABASE_URL = "postgresql://purple:purple@localhost:5433/purple"\n'
            "  (or put it in .env - see .env.example)")
    return url


@contextmanager
def _conn():
    if psycopg2 is None:
        raise SystemExit("psycopg2 is not installed.\n"
                         "  pip install psycopg2-binary")
    conn = psycopg2.connect(require_url())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_count(table):
    """Rows in the table, or None if it does not exist."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("select to_regclass(%s)", (f"public.{table}",))
        if cur.fetchone()[0] is None:
            return None
        cur.execute(f'select count(*) from public."{table}"')
        return cur.fetchone()[0]


def column_values(table, column):
    """Every non-null value of one column. Used for 'what is already loaded'."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f'select "{column}" from public."{table}" '
                    f'where "{column}" is not null')
        return [row[0] for row in cur.fetchall()]


def can_write(table):
    """(ok, detail). Proves the connection can INSERT, then rolls it back.

    It has to be an insert. The first version ran an update matching no
    rows, which passes for a user who cannot insert at all: an UPDATE whose
    WHERE matches nothing touches no row, so no policy and no column
    privilege is ever evaluated. Against a table with row level security
    enabled, that probe reported "write permission ok" while every real
    write failed with "new row violates row-level security policy". A
    preflight that passes when the run will fail is worse than no preflight.

    Nothing is left behind: the insert is rolled back, and the sentinel
    call_id would be unique anyway.
    """
    try:
        if psycopg2 is None:
            return False, "psycopg2 is not installed"
        conn = psycopg2.connect(require_url())
        try:
            with conn.cursor() as cur:
                # Fill every NOT NULL column that has no default. Asked of
                # the catalogue rather than hardcoded: the schema gains
                # columns, and a fixed list would start failing for the
                # wrong reason.
                cur.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema = 'public' and table_name = %s "
                    "  and is_nullable = 'NO' and column_default is null",
                    (table,))
                cols = [r[0] for r in cur.fetchall()] or ["call_id"]
                names = ", ".join(f'"{c}"' for c in cols)
                marks = ", ".join(["%s"] * len(cols))
                cur.execute(
                    f'insert into public."{table}" ({names}) values ({marks})',
                    tuple("__preflight_probe__" for _ in cols))
            conn.rollback()
            return True, ""
        finally:
            conn.close()
    except Exception as exc:
        return False, str(exc).strip().splitlines()[0][:80]


def upsert(table, rows, conflict, chunk=200):
    """Insert rows, updating on conflict. Returns how many were sent.

    `conflict` is a comma-separated key list.
    """
    if not rows:
        return 0
    cols = conflict.split(",")
    done = 0
    with _conn() as conn, conn.cursor() as cur:
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
                squared.append(tuple(r.get(k) for k in keys))
            quoted = ", ".join(f'"{k}"' for k in keys)
            updates = ", ".join(f'"{k}" = excluded."{k}"'
                                for k in keys if k not in cols)
            target = ", ".join(f'"{c}"' for c in cols)
            sql = (f'insert into public."{table}" ({quoted}) values %s '
                   f'on conflict ({target}) '
                   + (f'do update set {updates}' if updates else 'do nothing'))
            psycopg2.extras.execute_values(cur, sql, squared, page_size=chunk)
            done += len(squared)
    return done


def disable_rls(table):
    """Turn row level security off, if it is on. True when it changed.

    Only the owner may do this, so it belongs in init_db.py rather than in
    the loader's path - the loader runs as a least-privilege user and would
    only fail.
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("select relrowsecurity from pg_class where relname = %s",
                    (table,))
        row = cur.fetchone()
        if not row or not row[0]:
            return False
        cur.execute(f'alter table public."{table}" disable row level security')
        return True


def ensure_supabase_roles():
    """Create the roles the sql/ files grant to.

    The schema was written for Supabase, where service_role, anon and
    authenticated exist already. They are kept as grant targets - NOLOGIN,
    so nothing can connect as them - rather than editing the grants out,
    because the same files still describe the schema and a local copy that
    has drifted is worse than none.
    """
    with _conn() as conn, conn.cursor() as cur:
        for role in ("anon", "authenticated", "service_role"):
            cur.execute(
                "do $$ begin "
                f"  if not exists (select 1 from pg_roles where rolname = '{role}') then "
                f"    create role {role} nologin; "
                "  end if; "
                "end $$;")


def run_sql_file(path):
    """Apply a .sql file."""
    with open(path, encoding="utf-8") as handle:
        sql = handle.read()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
    return path
