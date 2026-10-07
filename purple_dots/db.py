"""Supabase access for the Purple Dots pipeline, over PostgREST.

Two settings:

    SUPABASE_URL         https://blzzscjqvaqfwxzlqtfn.supabase.co
    SUPABASE_SECRET_KEY  the service key

THIS IS THE SUPABASE BRANCH
The `postgres` branch is the same pipeline writing to a local Postgres
instead. Everything above this layer is identical on both; only these
functions differ. Keep them in step: a fix to the loader belongs on both
branches, and the two will drift the moment one is forgotten.

WHY A LAYER AND NOT requests EVERYWHERE
PostgREST's behaviour is not obvious and is relied on in several places:

  * `Prefer: resolution=merge-duplicates` makes a POST an upsert, and leaves
    columns absent from the payload alone rather than nulling them. That is
    what lets columns we add ourselves survive a re-sync.
  * A page caps at 1000 rows however large the limit asks for, so reading a
    whole column means walking offsets.
  * Every object in one POST must carry the same keys, and a key may not
    repeat within it - "ON CONFLICT DO UPDATE command cannot affect row a
    second time".

Reproducing those at each call site is where the bugs would be. They are
written once, here.
"""
import os

import requests


def describe():
    """One line naming the project, for the top of a run's output."""
    return f"Supabase  {os.getenv('SUPABASE_URL') or '(SUPABASE_URL not set)'}"


def credentials():
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = (os.getenv("SUPABASE_SECRET_KEY")
           or os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    if not (url and key):
        raise SystemExit(
            "SUPABASE_URL and SUPABASE_SECRET_KEY must be set.\n"
            "  copy .env.example to .env and fill it in")
    # A wrong-project write is the failure this whole folder exists to
    # prevent, so it is checked rather than trusted.
    if "vqvonmoktpvfvzlhtiqo" in url:
        raise SystemExit(
            "SUPABASE_URL points at the Blue Dots project. This writes "
            "Purple Dots data; set it to the Purple Dots project.")
    return url, key


def _rest(table):
    url, key = credentials()
    return (f"{url}/rest/v1/{table}",
            {"apikey": key, "Authorization": f"Bearer {key}"})


# --------------------------------------------------------------------------


def table_count(table):
    """Rows in the table, or None if it does not exist."""
    endpoint, headers = _rest(table)
    r = requests.get(endpoint, headers={**headers, "Prefer": "count=exact"},
                     params={"select": "*", "limit": 1}, timeout=60)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise RuntimeError(f"{table}: {r.status_code} {r.text[:120]}")
    return int(r.headers.get("content-range", "0-0/0").split("/")[-1])


def column_values(table, column):
    """Every non-null value of one column. Used for 'what is already loaded'."""
    endpoint, headers = _rest(table)
    out, offset = [], 0
    while True:
        # The 1000-row cap is why this walks offsets rather than asking for
        # everything at once.
        r = requests.get(endpoint, headers=headers, timeout=300,
                         params={"select": column, "order": column,
                                 "offset": offset, "limit": 1000})
        if r.status_code == 404:
            raise SystemExit(f"{table} does not exist yet - run the SQL in "
                             f"sql/ in the Supabase SQL editor first.")
        if r.status_code >= 400:
            raise RuntimeError(f"{table}: {r.status_code} {r.text[:200]}")
        page = r.json()
        if not page:
            break
        out += [row[column] for row in page if row.get(column) is not None]
        if len(page) < 1000:
            break
        offset += len(page)
    return out


def can_write(table):
    """(ok, detail). Proves the key can write WITHOUT writing.

    A PATCH whose filter matches nothing changes no row but still fails with
    403 on a read-only key - which is what we want to learn now rather than
    after twenty minutes of fetching.
    """
    endpoint, headers = _rest(table)
    r = requests.patch(endpoint, timeout=60,
                       headers={**headers, "Content-Type": "application/json",
                                "Prefer": "return=minimal"},
                       params={"call_id": "eq.__preflight_no_match__"},
                       json={"test_flag": True})
    return r.status_code < 400, ("" if r.status_code < 400
                                 else f"{r.status_code} - key is read-only")


def upsert(table, rows, conflict, chunk=200):
    """Insert rows, updating on conflict. Returns how many were sent."""
    if not rows:
        return 0
    endpoint, headers = _rest(table)
    headers = {**headers, "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    cols = conflict.split(",")
    done = 0
    for start in range(0, len(rows), chunk):
        batch = rows[start:start + chunk]
        # Same keys on every object, and no key twice in one request.
        keys = sorted({k for r in batch for k in r})
        seen, squared = set(), []
        for r in batch:
            ident = tuple(r.get(c) for c in cols)
            if ident in seen:
                continue
            seen.add(ident)
            squared.append({k: r.get(k) for k in keys})
        resp = requests.post(endpoint, headers=headers, timeout=300,
                             params={"on_conflict": conflict}, json=squared)
        if resp.status_code >= 400:
            raise SystemExit(f"push failed at row {start}: "
                             f"{resp.status_code} {resp.text[:400]}")
        done += len(squared)
    return done


def run_sql_file(path):
    """Not possible here. Supabase exposes no API for DDL."""
    raise SystemExit(
        f"Supabase has no API for DDL, so {path} cannot be applied from here.\n"
        f"Paste it into the SQL editor in the Supabase dashboard instead.\n"
        f"(The postgres branch applies these files directly, via init_db.py.)")
