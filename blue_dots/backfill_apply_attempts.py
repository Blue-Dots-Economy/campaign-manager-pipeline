"""Backfills apply_attempts on kkb_mastersheet.

The apply_job tool call is the only place a failed apply's job_id and its real
upstream cause (PROFILE_NOT_LIVE, ACTION_LIMIT_REACHED) exist. transform.py
read it at push time to set apply_api_success, then discarded it - so 81
successful applies ended up with no job_id at all.

Only rows that actually attempted an apply are worth fetching, which is ~1,451
transcripts rather than 71,546.

    python backfill_apply_attempts.py --dry-run
    python backfill_apply_attempts.py
"""
import argparse
import collections
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

from pipeline import AGENT_JFC
from raya_client import fetch_all_batches, fetch_all_contacts, fetch_call_detail
from transform import extract_apply_attempts, first_call

load_dotenv()

TABLE = "kkb_mastersheet"
CACHE = "apply_attempts_cache.json"


def targets(url, key):
    """call_ids that attempted an apply - the only rows worth a transcript."""
    endpoint = f"{url.rstrip('/')}/rest/v1/{TABLE}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    want, offset = set(), 0
    while True:
        r = requests.get(endpoint, headers=headers,
                         params={"select": "call_id", "order": "call_id",
                                 "apply_api_success": "not.is.null",
                                 "offset": offset, "limit": 1000}, timeout=300)
        page = r.json()
        if not isinstance(page, list) or not page:
            break
        want.update(str(x["call_id"]) for x in page)
        if len(page) < 1000:
            break
        offset += len(page)
    return want


def collect(api_key, want, workers=12):
    """contact_id -> apply_attempts, for the contacts we care about."""
    uuids = {}
    for aid in AGENT_JFC:
        for b in fetch_all_batches(api_key, aid):
            try:
                contacts = fetch_all_contacts(api_key, b["id"])
            except Exception as exc:
                print(f"    batch {b.get('id')}: {exc}")
                continue
            for c in contacts:
                cid = str(c.get("contact_id"))
                if cid in want and cid not in uuids:
                    u = first_call(c).get("uuid")
                    if u:
                        uuids[cid] = u
    print(f"  {len(uuids)} of {len(want)} target rows have a call uuid")

    out, done = {}, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_call_detail, api_key, u): cid
                for cid, u in uuids.items()}
        for f in as_completed(futs):
            cid = futs[f]
            try:
                detail = f.result() or {}
            except Exception:
                detail = {}
            attempts = extract_apply_attempts(detail.get("call_transcript"))
            if attempts:
                out[cid] = attempts
            done += 1
            if done % 250 == 0:
                print(f"    {done}/{len(uuids)} transcripts", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-cache", action="store_true")
    args = ap.parse_args()

    api_key = os.getenv("RAYA_API_KEY")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if args.from_cache and os.path.exists(CACHE):
        found = json.load(open(CACHE, encoding="utf-8"))
        print(f"Loaded {len(found)} rows from {CACHE}")
    else:
        want = targets(url, key)
        print(f"{len(want)} rows attempted an apply")
        t0 = time.time()
        found = collect(api_key, want)
        json.dump(found, open(CACHE, "w", encoding="utf-8"))
        print(f"  {len(found)} with apply tool calls in {time.time() - t0:.0f}s")

    jobs = sum(1 for v in found.values() for a in v if a.get("job_id"))
    errs = collections.Counter(a.get("error") for v in found.values()
                               for a in v if a.get("error"))
    print(f"\n  apply attempts captured : {sum(len(v) for v in found.values())}")
    print(f"  with a job_id           : {jobs}")
    print("  failure causes:")
    for e, c in errs.most_common(10):
        print(f"    {c:>5}  {e}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    endpoint = f"{url.rstrip('/')}/rest/v1/{TABLE}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    rows = [{"call_id": cid, "apply_attempts": v} for cid, v in found.items()]
    done = 0
    for start in range(0, len(rows), 200):
        chunk = rows[start:start + 200]
        r = requests.post(endpoint, headers=headers,
                          params={"on_conflict": "call_id"}, json=chunk, timeout=300)
        if r.status_code >= 400:
            print(f"  FAILED at {start + 1}: {r.status_code} {r.text[:300]}")
            break
        done += len(chunk)
    print(f"\nupdated {done} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
