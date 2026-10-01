"""Writes call_confidence_score (and its reasoning) onto the aggregator tables.

    python score_confidence.py --dry-run          # distribution only, nothing written
    python score_confidence.py                    # seekers
    python score_confidence.py --table provider   # providers

Scoring lives in call_confidence.py; this file only moves data.
"""
import argparse
import collections
import os

import requests
from dotenv import load_dotenv

from call_confidence import (compute_call_confidence, compute_provider_confidence,
                             confidence_bucket)
from pipeline import env_or_arg

load_dotenv()

TABLES = {
    "seeker": {
        "table": "aggregated_seeker_journey",
        "key": "seeker_id",
        "columns": ("seeker_id,ever_called,ever_answered,ever_engaged,ever_applied,"
                    "total_calls_made,max_intent_score,drop_reason,last_call_date,onboarded_at,"
                    "trrain_do_not_call,trrain_interest"),
        "score": "seeker",
    },
    "provider": {
        "table": "aggregated_provider_journey",
        "key": "provider_id",
        "columns": ("provider_id,ever_called,ever_answered,total_calls_made,drop_reason,"
                    "last_call_date,onboarded_at,total_postings,ever_verified_active,"
                    "ever_new_job_mentioned,ever_new_job_posted,max_phase_reached,"
                    "total_fields_updated"),
        "score": "provider",
    },
}


def fetch(url, key, spec, page_size=1000):
    endpoint = f"{url.rstrip('/')}/rest/v1/{spec['table']}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        r = requests.get(endpoint, headers=headers,
                         params={"select": spec["columns"], "order": spec["key"],
                                 "offset": offset, "limit": page_size}, timeout=180)
        if r.status_code >= 400:
            raise RuntimeError(f"read failed: {r.status_code} {r.text[:300]}")
        page = r.json()
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    return rows


def upsert(rows, url, key, spec, chunk_size=500):
    endpoint = f"{url.rstrip('/')}/rest/v1/{spec['table']}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        r = requests.post(endpoint, headers=headers, params={"on_conflict": spec["key"]},
                          json=chunk, timeout=180)
        if r.status_code >= 400:
            print(r.text[:700])
            raise RuntimeError(f"write failed at {start + 1}-{start + len(chunk)}")
        if (start // chunk_size) % 20 == 0 or start + len(chunk) == len(rows):
            print(f"  {start + len(chunk)}/{len(rows)} -> {r.status_code}")


SEGMENTS = {
    "seeker": (
        ("never called", lambda row: not row.get("ever_called")),
        ("called, never answered", lambda row: row.get("ever_called") and not row.get("ever_answered")),
        ("answered, not applied", lambda row: row.get("ever_answered") and not row.get("ever_applied")),
        ("applied", lambda row: row.get("ever_applied")),
        ("intent >= 7, not applied", lambda row: (row.get("max_intent_score") or 0) and
            float(row["max_intent_score"]) >= 7 and not row.get("ever_applied")),
    ),
    "provider": (
        ("never called", lambda row: not row.get("ever_called")),
        ("called, never answered", lambda row: row.get("ever_called") and not row.get("ever_answered")),
        ("answered, unverified", lambda row: row.get("ever_answered") and not row.get("ever_verified_active")),
        ("confirmed a job active", lambda row: row.get("ever_verified_active")),
        ("has postings, never called", lambda row: not row.get("ever_called") and (row.get("total_postings") or 0)),
    ),
}


def report(scored, kind):
    buckets = collections.Counter(confidence_bucket(s) for _, s, _ in scored)
    print("\nDistribution")
    for b in ("6.1-10 (call now)", "4.1-6 (high)", "2.1-4 (medium)",
              "0.1-2 (low)", "0 (do not call)"):
        n = buckets.get(b, 0)
        print(f"  {b:<20} {n:>7}  {100 * n / len(scored):5.1f}%")

    print("\nWhy the zeros")
    for reason, n in collections.Counter(r for _, s, r in scored if s == 0).most_common(6):
        print(f"  {n:>7}  {reason}")

    print("\nBy segment (mean score / count)")
    for label, pred in SEGMENTS[kind]:
        vals = [s for row, s, _ in scored if pred(row)]
        if vals:
            print(f"  {label:<26} {sum(vals) / len(vals):5.2f}  n={len(vals)}")

    print("\nTop of the queue")
    for row, s, reason in sorted(scored, key=lambda x: -x[1])[:5]:
        print(f"  {s:>5}  {reason}")
    print("\nBottom of the callable queue")
    for row, s, reason in sorted((x for x in scored if x[1] > 0), key=lambda x: x[1])[:5]:
        print(f"  {s:>5}  {reason}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", choices=sorted(TABLES), default="seeker")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--supabase-key", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    spec = TABLES[args.table]
    url = env_or_arg("SUPABASE_URL", args.supabase_url)
    key = env_or_arg("SUPABASE_SECRET_KEY", args.supabase_key) or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    print(f"Reading {spec['table']}...")
    rows = fetch(url, key, spec)
    print(f"  {len(rows)} rows")

    compute = compute_provider_confidence if spec["score"] == "provider" else compute_call_confidence
    scored = []
    for row in rows:
        score, reason = compute(row)
        scored.append((row, score, reason))
    report(scored, spec["score"])

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    payload = [{spec["key"]: row[spec["key"]], "call_confidence_score": score,
                "call_confidence_reason": reason} for row, score, reason in scored]
    print(f"\nWriting {len(payload)} scores...")
    upsert(payload, url, key, spec)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"ERROR: {exc}")
