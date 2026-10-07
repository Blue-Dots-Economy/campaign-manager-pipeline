"""Postgres access. DATABASE_URL is the only setting.

The Supabase build is on `master`; keep the two in step.
"""
import os
from contextlib import contextmanager

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None


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
    """(ok, detail). INSERTs and rolls back.

    Must be an INSERT: an UPDATE matching no rows evaluates no policy or
    column privilege, so it passes for a user who cannot insert.
    """
    try:
        if psycopg2 is None:
            return False, "psycopg2 is not installed"
        conn = psycopg2.connect(require_url())
        try:
            with conn.cursor() as cur:
                # every NOT NULL column without a default
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
    """Turn RLS off if on. True when changed. Owner only."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("select relrowsecurity from pg_class where relname = %s",
                    (table,))
        row = cur.fetchone()
        if not row or not row[0]:
            return False
        cur.execute(f'alter table public."{table}" disable row level security')
        return True


def ensure_supabase_roles():
    """Create service_role, anon, authenticated as NOLOGIN grant targets.

    sql/ grants to them; Supabase provides them, plain Postgres does not.
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
