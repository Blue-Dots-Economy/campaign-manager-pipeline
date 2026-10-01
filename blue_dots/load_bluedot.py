"""Loads the Blue Dot S3 dumps into the `bluedot` schema over the REST API.

The psycopg path in push_to_supabase.py needs SUPABASE_DB_URL (a session-pooler
password). This does the same job with the service key we already use for every
other table, so no database password is involved.

PostgREST only reaches a non-public schema when it is listed under
Settings -> API -> Exposed schemas, and each request names it:

    Accept-Profile:  bluedot     (reads)
    Content-Profile: bluedot     (writes)

Load order is fixed by the foreign keys - items references users, and
item_actions references items - so the three tables cannot be parallelised.
Referential integrity is checked against the dumps BEFORE anything is written,
because a mid-load FK rejection would leave the schema half-populated.

Every row upserts on its composite primary key, so re-running after a fresh
download updates in place rather than duplicating.

    python load_bluedot.py --check      # parse + validate, write nothing
    python load_bluedot.py              # load every instance under dumps/
    python load_bluedot.py --instance UP
"""
import argparse
import collections
import gzip
import json
import os
import pathlib

import requests
from dotenv import load_dotenv

load_dotenv()

GZIP_MAGIC = b"\x1f\x8b"
CHUNK = 500

# Coordinates that are client defaults, not real places. (28.64, 77.34) appears in
# both deployments; (20.59, 78.96) is the standard centroid-of-India placeholder.
SENTINEL_GEO = {(28.64, 77.34), (20.59, 78.96), (0.0, 0.0)}

# dumps/<dir> -> the `instance` value stored in Postgres. The old directory names
# are aliased so a stale checkout cannot load a second copy of the same rows
# under a different label.
INSTANCE_LABELS = {"gzb": "UP", "up": "UP", "dharwad": "KA", "ka": "KA"}


def instance_label(dirname):
    return INSTANCE_LABELS.get(dirname.lower(), dirname)


def open_maybe_gzip(path):
    """The dump files are gzip but named .jsonl, so sniff rather than trust."""
    with open(path, "rb") as probe:
        packed = probe.read(2) == GZIP_MAGIC
    return (gzip.open if packed else open)(path, "rt", encoding="utf-8")


def read_records(path):
    with open_maybe_gzip(path) as handle:
        for lineno, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path.name} line {lineno}: bad JSON: {exc}")


def blank_to_none(value):
    return value if value not in ("", []) else None


def user_rows(records, instance):
    for r in records:
        yield {
            "instance": instance, "id": r["id"],
            "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
            "domains": r.get("domains") or None,
            "onboarded_by_org_id": r.get("onboarded_by_org_id"),
            "onboarded_via": r.get("onboarded_via"),
            "onboarded_source_id": r.get("onboarded_source_id"),
            "onboarded_at": r.get("onboarded_at"),
            "tags": r.get("tags") or {},
        }


def item_rows(records, instance):
    for r in records:
        locations = r.get("item_locations") or []
        lat = lng = None
        if locations:
            lat, lng = locations[0].get("lat"), locations[0].get("lng")
        is_default = None
        if lat is not None:
            is_default = (round(lat, 2), round(lng, 2)) in SENTINEL_GEO
        yield {
            "instance": instance, "item_id": r["item_id"],
            "item_network": r.get("item_network"), "item_domain": r.get("item_domain"),
            "item_type": r.get("item_type"), "lifecycle_status": r.get("lifecycle_status"),
            "created_by": r.get("created_by"),
            "lat": lat, "lng": lng, "is_default_geo": is_default,
            "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
            "item_state": r.get("item_state") or {},
        }


def action_rows(records, instance):
    for r in records:
        yield {
            "instance": instance, "action_id": r["action_id"],
            "partition_network": r.get("partition_network"),
            "action_type": r.get("action_type"), "action_status": r.get("action_status"),
            "update_count": r.get("update_count"),
            "source_item_network": r.get("source_item_network"),
            "source_item_domain": r.get("source_item_domain"),
            "source_item_type": r.get("source_item_type"),
            "source_item_id": r.get("source_item_id"),
            "source_item_owner": r.get("source_item_owner"),
            "target_item_network": r.get("target_item_network"),
            "target_item_domain": r.get("target_item_domain"),
            "target_item_type": r.get("target_item_type"),
            "target_item_id": r.get("target_item_id"),
            "target_item_owner": r.get("target_item_owner"),
            "performed_by_org_id": blank_to_none(r.get("performed_by_org_id")),
            "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
        }


# file stem -> (table, conflict key, row builder). Order matters and is load order.
SPECS = (
    ("user",         "users",        "instance,id",        user_rows),
    ("items",        "items",        "instance,item_id",   item_rows),
    ("item_actions", "item_actions", "instance,action_id", action_rows),
)


def target(table, public):
    """Same tables, two possible homes: bluedot.users, or public.bluedot_users
    when the bluedot schema is not served by the REST API."""
    return f"bluedot_{table}" if public else table


def gather(root, only=None):
    """Read every instance directory under dumps/ into memory, keyed by table."""
    built = {stem: [] for stem, _, _, _ in SPECS}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        instance = instance_label(directory.name)
        if only and instance != only:
            continue
        for stem, _, _, build in SPECS:
            path = directory / f"{stem}.jsonl"
            if not path.exists():
                print(f"  WARNING: {path} missing")
                continue
            rows = list(build(read_records(path), instance))
            built[stem].extend(rows)
            print(f"  {instance:<3} {stem:<13} {len(rows):>7} rows")
    return built


def validate(built):
    """The schema carries two foreign keys. A rejection mid-load would leave the
    tables half-populated, so check the dumps against each other first."""
    problems = []
    user_keys = {(r["instance"], r["id"]) for r in built["user"]}
    item_keys = {(r["instance"], r["item_id"]) for r in built["items"]}

    orphan_items = [r for r in built["items"]
                    if r["created_by"] and (r["instance"], r["created_by"]) not in user_keys]
    if orphan_items:
        by_inst = collections.Counter(r["instance"] for r in orphan_items)
        problems.append(f"items.created_by -> users: {len(orphan_items)} orphans {dict(by_inst)}")

    orphan_actions = [r for r in built["item_actions"]
                      if r["target_item_id"] and (r["instance"], r["target_item_id"]) not in item_keys]
    if orphan_actions:
        by_inst = collections.Counter(r["instance"] for r in orphan_actions)
        problems.append(f"item_actions.target_item_id -> items: {len(orphan_actions)} orphans {dict(by_inst)}")

    # the source side has no FK by design - 13 actions reference a missing profile
    missing_source = sum(1 for r in built["item_actions"]
                         if r["source_item_id"] and (r["instance"], r["source_item_id"]) not in item_keys)

    for stem, _, _, _ in SPECS:
        key = {"user": "id", "items": "item_id", "item_actions": "action_id"}[stem]
        seen = collections.Counter((r["instance"], r[key]) for r in built[stem])
        dupes = [k for k, n in seen.items() if n > 1]
        if dupes:
            problems.append(f"{stem}: {len(dupes)} duplicate primary keys in the dump")

    return problems, missing_source


def upsert(rows, table, conflict, url, key, public=False, chunk=CHUNK):
    endpoint = f"{url.rstrip('/')}/rest/v1/{target(table, public)}"
    headers = {
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    if not public:
        headers["Content-Profile"] = "bluedot"
    for start in range(0, len(rows), chunk):
        batch = rows[start:start + chunk]
        r = requests.post(endpoint, headers=headers, params={"on_conflict": conflict},
                          json=batch, timeout=300)
        if r.status_code >= 400:
            print(f"\n  {r.status_code} {r.text[:600]}")
            raise RuntimeError(f"{table}: failed at rows {start + 1}-{start + len(batch)}")
        done = start + len(batch)
        if (start // chunk) % 10 == 0 or done == len(rows):
            print(f"    {done}/{len(rows)}")


def verify(url, key, public=False):
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}
    if not public:
        headers["Accept-Profile"] = "bluedot"
    prefix = "public.bluedot_" if public else "bluedot."
    for _, table, _, _ in SPECS:
        r = requests.get(f"{url.rstrip('/')}/rest/v1/{target(table, public)}", headers=headers,
                         params={"select": "instance", "limit": 1}, timeout=120)
        if r.status_code >= 400:
            print(f"  {prefix}{table:<13} unreadable: {r.status_code} {r.text[:120]}")
            continue
        print(f"  {prefix}{table:<13} {r.headers['content-range'].split('/')[-1]:>7} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumps-dir", default="dumps")
    ap.add_argument("--instance", default=None, help="UP or KA; default is both")
    ap.add_argument("--check", action="store_true", help="parse and validate, write nothing")
    ap.add_argument("--public", action="store_true",
                    help="load public.bluedot_* instead of the bluedot schema")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--supabase-key", default=None)
    args = ap.parse_args()

    root = pathlib.Path(args.dumps_dir)
    if not root.is_dir():
        raise SystemExit(f"{root} not found - run fetchdata.py first")

    url = args.supabase_url or os.getenv("SUPABASE_URL")
    key = (args.supabase_key or os.getenv("SUPABASE_SECRET_KEY")
           or os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SECRET_KEY must be set")

    print(f"Reading {root}/ ...")
    built = gather(root, args.instance)
    total = sum(len(v) for v in built.values())
    if not total:
        raise SystemExit("nothing to load")

    print("\nValidating against the schema's foreign keys...")
    problems, missing_source = validate(built)
    if missing_source:
        print(f"  note: {missing_source} actions reference a source profile absent from the "
              f"items dump - expected, that side carries no FK")
    if problems:
        for p in problems:
            print(f"  BLOCKER: {p}")
        raise SystemExit("refusing to load - fix the above or the FK will reject it mid-write")
    print("  clean")

    if args.check:
        print(f"\n--check: {total} rows parsed and valid. Nothing written.")
        return

    prefix = "public.bluedot_" if args.public else "bluedot."
    print(f"\nLoading into {prefix}* (users -> items -> item_actions)...")
    for stem, table, conflict, _ in SPECS:
        rows = built[stem]
        if not rows:
            continue
        print(f"  {prefix}{table}")
        upsert(rows, table, conflict, url, key, public=args.public)

    print("\nIn the database now:")
    verify(url, key, public=args.public)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"ERROR: {exc}")
