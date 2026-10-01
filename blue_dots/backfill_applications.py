"""Populates kkb_applications from the calls already in Supabase.

One row per job a call applied to. Everything needed is already stored on
kkb_mastersheet - jobs_applied, jobs_failed_to_apply, apply_attempts and
recommendations_input - so this reads Supabase only and never touches Raya.

After this, main.py writes applications on every push and the sheet builder
reads kkb_applications_full instead of recomputing from JSON.

    python backfill_applications.py --dry-run
    python backfill_applications.py
"""
import argparse
import collections
import json
import os

import requests
from dotenv import load_dotenv

from transform import _clean, _first, _looks_like_uuid, extract_apply_attempts, job_list

load_dotenv()

SOURCE = "kkb_mastersheet"
TARGET = "kkb_applications"
COLUMNS = ("call_id,jobs_applied,jobs_failed_to_apply,apply_attempts,"
           "recommendations_input")


def page(url, key, table, select, extra=None, order="call_id"):
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        params = {"select": select, "order": order, "offset": offset, "limit": 1000}
        if extra:
            params.update(extra)
        r = requests.get(endpoint, headers=headers, params=params, timeout=300)
        if r.status_code >= 400:
            raise SystemExit(f"{table}: {r.status_code} {r.text[:300]}")
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += len(chunk)
    return rows


def rows_for(call):
    """The same logic as transform.make_application_rows, but reading the
    stored columns rather than a live Raya contact."""
    offered = {}
    reco = call.get("recommendations_input")
    if isinstance(reco, str):
        try:
            reco = json.loads(reco)
        except Exception:
            reco = None
    for job in (reco or []):
        if isinstance(job, dict) and job.get("job_id"):
            offered[str(job["job_id"])] = job

    applied = job_list(call.get("jobs_applied")) or []
    failed = job_list(call.get("jobs_failed_to_apply")) or []
    attempts = [a for a in (call.get("apply_attempts") or []) if isinstance(a, dict)]

    if not applied and not failed:
        for a in attempts:
            if not a.get("job_id"):
                continue
            job = {"job_id": a["job_id"]}
            if a.get("ok"):
                applied.append(job)
            else:
                failed.append(dict(job, failure_reason=a.get("error") or "api_failed"))

    errors = {str(a["job_id"]): a.get("error") for a in attempts if a.get("job_id")}

    out, seen = [], set()
    for jobs, ok in ((applied, True), (failed, False)):
        for job in jobs:
            if not isinstance(job, dict):
                continue
            jid = str(job.get("job_id") or "").strip()
            # the unique key is (call_id, job_id, applied); a call listing the
            # same job twice would otherwise fail the whole batch
            if not jid or (jid, ok) in seen:
                continue
            seen.add((jid, ok))
            shown = offered.get(jid, {})
            head = jid.split("_", 1)[0] if "_" in jid else ""
            out.append({
                "call_id": str(call["call_id"]),
                "job_id": jid,
                "applied": ok,
                "failure_reason": None if ok else _first(
                    errors.get(jid), job.get("failure_reason"), "api_failed"),
                "synthetic_job_id": not _looks_like_uuid(jid),
                "job_role": _first(shown.get("role"), job.get("role")),
                "company_name": _first(shown.get("company"), job.get("company_name")),
                "provider_phone": head if head.isdigit() else None,
                "job_location": _first(shown.get("location"), job.get("company_location")),
                "vacancies": _first(shown.get("vacancy")),
                "salary": _first(shown.get("salary"), job.get("salary_offered")),
                "qualification_required": _first(shown.get("qualification"),
                                                 job.get("qualification_required")),
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    print("Reading calls that applied to something...")
    calls = page(url, key, SOURCE, COLUMNS,
                 extra={"or": "(jobs_applied.not.is.null,"
                              "jobs_failed_to_apply.not.is.null,"
                              "apply_attempts.not.is.null)"})
    print(f"  {len(calls)} calls")

    rows = []
    for c in calls:
        rows.extend(rows_for(c))

    ok = sum(1 for r in rows if r["applied"])
    synth = sum(1 for r in rows if r["synthetic_job_id"])
    named = sum(1 for r in rows if r["company_name"])
    reasons = collections.Counter(r["failure_reason"] for r in rows if not r["applied"])
    print(f"\n  application rows   : {len(rows)}")
    print(f"    succeeded        : {ok}")
    print(f"    failed           : {len(rows) - ok}")
    print(f"    synthetic job_id : {synth}")
    print(f"    with a company   : {named} ({100 * named / max(len(rows), 1):.0f}%)")
    print("  top failure reasons:")
    for reason, n in reasons.most_common(6):
        print(f"    {n:>5}  {reason}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    endpoint = f"{url.rstrip('/')}/rest/v1/{TARGET}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    done = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        r = requests.post(endpoint, headers=headers,
                          params={"on_conflict": "call_id,job_id,applied"},
                          json=chunk, timeout=300)
        if r.status_code >= 400:
            print(f"  FAILED at {start + 1}: {r.status_code} {r.text[:300]}")
            break
        done += len(chunk)
        if (start // 500) % 4 == 0 or done == len(rows):
            print(f"  {done}/{len(rows)}")
    print(f"\nwrote {done} application rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
