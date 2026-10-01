"""Backfills jobs_applied and jobs_failed_to_apply on kkb_mastersheet.

Both fields come straight from Raya's call_output. jobs_failed_to_apply lists
each job whose apply errored, WITH its failure_reason ("HTTP 404 - Job not
found"). It was only added to transform.py on 21 Sept, so rows pushed before
that have apply_api_success telling us an apply failed while the job itself is
unknown - and the applications sheet needs the job to write a row. Of 564 known
failures only 7 had the detail before this ran.

Cheap compared with a push: call_output is on the batch-contacts endpoint, so
no per-call transcript fetch. Read-only against Raya; the only writes are a
PATCH of these two columns.

    python backfill_jobs.py --dry-run
    python backfill_jobs.py
"""
import argparse
import collections
import json
import os
import time

import requests
from dotenv import load_dotenv

from pipeline import AGENT_JFC
from raya_client import fetch_all_batches, fetch_all_contacts
from transform import job_list, merged_output

load_dotenv()

TABLE = "kkb_mastersheet"
CACHE = "jobs_backfill_cache.json"
FIELDS = ("jobs_applied", "jobs_failed_to_apply")


def gather(api_key):
    """contact_id -> {jobs_applied, jobs_failed_to_apply}, for contacts that
    actually have one of them."""
    found = {}
    batches = []
    for aid in AGENT_JFC:
        for b in fetch_all_batches(api_key, aid):
            batches.append(b)
    print(f"  {len(batches)} batches across {len(AGENT_JFC)} mapped agents")

    for i, b in enumerate(batches, 1):
        try:
            contacts = fetch_all_contacts(api_key, b["id"])
        except Exception as exc:
            print(f"    batch {b.get('id')}: {exc}")
            continue
        for c in contacts:
            cid = c.get("contact_id")
            if cid is None:
                continue
            co = merged_output(c)          # all attempts, newest value wins
            applied = job_list(co.get("jobs_applied"))
            failed = job_list(co.get("jobs_failed_to_apply"))
            if applied or failed:
                found[str(cid)] = {"jobs_applied": applied,
                                   "jobs_failed_to_apply": failed}
        if i % 25 == 0:
            print(f"    {i}/{len(batches)} batches, {len(found)} contacts with job detail",
                  flush=True)
    return found


def current(url, key):
    """What the table already holds, so we only PATCH real changes."""
    endpoint = f"{url.rstrip('/')}/rest/v1/{TABLE}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    have, offset = {}, 0
    while True:
        r = requests.get(endpoint, headers=headers,
                         params={"select": "call_id,jobs_applied,jobs_failed_to_apply",
                                 "order": "call_id", "offset": offset, "limit": 1000},
                         timeout=300)
        page = r.json()
        if not isinstance(page, list) or not page:
            break
        for row in page:
            have[str(row["call_id"])] = row
        if len(page) < 1000:
            break
        offset += len(page)
    return have


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-cache", action="store_true",
                    help="reuse the Raya walk saved by a previous run")
    args = ap.parse_args()

    api_key = os.getenv("RAYA_API_KEY")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if args.from_cache and os.path.exists(CACHE):
        found = json.load(open(CACHE, encoding="utf-8"))
        print(f"Loaded {len(found)} contacts from {CACHE}")
    else:
        print("Walking Raya for call_output job detail...")
        t0 = time.time()
        found = gather(api_key)
        json.dump(found, open(CACHE, "w", encoding="utf-8"))
        print(f"  {len(found)} contacts with job detail in {time.time() - t0:.0f}s"
              f"  (cached to {CACHE})")

    print("Reading what the table already has...")
    have = current(url, key)
    print(f"  {len(have)} rows")

    updates = []
    stats = collections.Counter()
    for cid, jobs in found.items():
        row = have.get(cid)
        if row is None:
            stats["not in table"] += 1
            continue
        patch = {}
        for f in FIELDS:
            if jobs.get(f) and not row.get(f):
                patch[f] = jobs[f]
        if patch:
            patch["call_id"] = cid
            updates.append(patch)
            for f in patch:
                if f != "call_id":
                    stats[f] += 1

    print(f"\n  rows to update            : {len(updates)}")
    print(f"    gaining jobs_applied    : {stats['jobs_applied']}")
    print(f"    gaining jobs_failed     : {stats['jobs_failed_to_apply']}")
    print(f"    Raya contacts not in db : {stats['not in table']}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return
    if not updates:
        print("nothing to do")
        return

    endpoint = f"{url.rstrip('/')}/rest/v1/{TABLE}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    # PostgREST rejects a bulk upsert whose objects have different key sets
    # ("All object keys must match"), and some rows gain only one of the two
    # columns - so send one batch per key signature rather than padding the
    # missing column with null, which would erase a value we meant to keep.
    groups = collections.defaultdict(list)
    for patch in updates:
        groups[tuple(sorted(patch))].append(patch)

    done = 0
    for sig, rows in groups.items():
        cols = ", ".join(c for c in sig if c != "call_id")
        print(f"  {len(rows)} rows setting [{cols}]")
        for start in range(0, len(rows), 200):
            chunk = rows[start:start + 200]
            r = requests.post(endpoint, headers=headers,
                              params={"on_conflict": "call_id"}, json=chunk, timeout=300)
            if r.status_code >= 400:
                print(f"    FAILED at {start + 1}: {r.status_code} {r.text[:300]}")
                break
            done += len(chunk)
    print(f"\nupdated {done} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
