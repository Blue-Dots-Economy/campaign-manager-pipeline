"""Builds aggregated_provider_journey: one row per Blue Dot PROVIDER, with
their call behaviour attached.

The linkage runs through job postings, not phone numbers:

    dkb_mastersheet.job_id  ->  items.item_id (item_domain = provider)
                            ->  items.created_by  =  the provider's Blue Dot user id

The Blue Dot dumps carry no phone numbers, so the phone on each row comes from
our own call data. Where both sides happen to have one they agree 480/521, which
is what validates the chain.

Every Blue Dot provider gets a row whether or not we called them. DKB calls that
don't resolve to a posting (job_id missing or unmatched) become call-only rows
keyed phone:<number> and flagged in_bluedot = false.

Re-runnable: upserts on provider_id, and clears rows left behind by the older
phone-keyed version of this table.

    python build_provider_journey.py [--dry-run]
"""
import argparse
import collections
import csv
import gzip
import io
import json
import os
import re
import sys

import requests
from dotenv import load_dotenv

from pipeline import env_or_arg

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
load_dotenv()

DUMPS = ("UP", "KA")
CALL_COLUMNS = (
    "call_id,contact_phone,company_name,campaign_name,campaign_date,jfc_campaign,job_id,"
    "call_duration_seconds,call_outcome,call_status,drop_reason,phases_reached,"
    "update_job_status,fields_updated,new_job_mentioned,new_job_posted,total_jobs_posted,"
    "updated_vacancies,intent_score"
)


def norm_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else None


def truthy(v):
    return v is True or str(v).strip().lower() in ("true", "yes", "1")


def jsonl(path):
    """The dumps are gzip despite the .jsonl name, so sniff the magic bytes."""
    with open(path, "rb") as fh:
        magic = fh.read(2)
        fh.seek(0)
        stream = gzip.open(fh, "rt", encoding="utf-8") if magic == b"\x1f\x8b" else io.TextIOWrapper(fh, encoding="utf-8")
        for line in stream:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_bluedot():
    """Provider users and their postings, from the non-PII dumps."""
    postings, users = {}, {}
    for instance in DUMPS:
        items_path = f"dumps/{instance}/items.jsonl"
        user_path = f"dumps/{instance}/user.jsonl"
        if not os.path.exists(items_path):
            print(f"  WARNING: {items_path} missing, skipping {instance}")
            continue
        for r in jsonl(items_path):
            if r.get("item_domain") == "provider":
                r["_instance"] = instance
                postings[r["item_id"]] = r
        for r in jsonl(user_path):
            if "provider" in (r.get("domains") or []):
                r["_instance"] = instance
                users[r["id"]] = r
    return users, postings


def fetch_calls(url, key, page_size=1000):
    endpoint = f"{url.rstrip('/')}/rest/v1/dkb_mastersheet"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        r = requests.get(endpoint, headers=headers,
                         params={"select": CALL_COLUMNS, "order": "call_id",
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


def ints(group, field):
    out = []
    for x in group:
        v = x.get(field)
        if v is None:
            continue
        try:
            out.append(int(float(v)))
        except (TypeError, ValueError):
            pass
    return out


def call_behaviour(group):
    group = sorted(group, key=lambda x: str(x.get("campaign_date") or ""), reverse=True)

    def last_where(pred):
        ds = [x.get("campaign_date") for x in group if x.get("campaign_date") and pred(x)]
        return max(ds) if ds else None

    answered = lambda x: str(x.get("call_status") or "").lower() == "answered"
    # in DKB, getting past phase 1 is the engagement signal
    engaged = lambda x: (x.get("phases_reached") or 0) and int(x["phases_reached"]) >= 2
    scores = [float(x["intent_score"]) for x in group if x.get("intent_score") is not None]
    phases = ints(group, "phases_reached")

    return {
        "call_ids": [str(x["call_id"]) for x in group if x.get("call_id")],
        "ever_called": True,
        "total_calls_made": len(group),
        "total_campaigns": len({x.get("campaign_name") for x in group if x.get("campaign_name")}),
        "last_call_date": last_where(lambda x: True),
        "last_call_answered": last_where(answered),
        "last_call_engaged": last_where(engaged),
        "drop_reason": next((x.get("drop_reason") for x in group if x.get("drop_reason")), None),
        "avg_intent_score": round(sum(scores) / len(scores), 2) if scores else None,
        "max_intent_score": max(scores) if scores else None,
        "latest_job_status": next((x.get("update_job_status") for x in group if x.get("update_job_status")), None),
        "ever_verified_active": any(str(x.get("update_job_status") or "") == "Active" for x in group),
        "ever_new_job_mentioned": any(truthy(x.get("new_job_mentioned")) for x in group),
        "ever_new_job_posted": any(truthy(x.get("new_job_posted")) for x in group),
        "total_new_jobs_posted": sum(ints(group, "total_jobs_posted")),
        "total_fields_updated": sum(ints(group, "fields_updated")),
        "total_vacancies": sum(ints(group, "updated_vacancies")),
        "max_phase_reached": max(phases) if phases else None,
        "ever_answered": any(answered(x) for x in group),
        "company_name": next((x.get("company_name") for x in group if x.get("company_name")), None),
        "phone": next((norm_phone(x.get("contact_phone")) for x in group if norm_phone(x.get("contact_phone"))), None),
        "jfc_campaign": group[0].get("jfc_campaign"),
    }


NEVER_CALLED = {
    "call_ids": [], "ever_called": False, "total_calls_made": 0, "total_campaigns": 0,
    "last_call_date": None, "last_call_answered": None, "last_call_engaged": None,
    "drop_reason": None, "avg_intent_score": None, "max_intent_score": None,
    "latest_job_status": None, "ever_verified_active": False,
    "ever_new_job_mentioned": False, "ever_new_job_posted": False,
    "total_new_jobs_posted": 0, "total_fields_updated": 0, "total_vacancies": 0,
    "max_phase_reached": None, "ever_answered": False, "company_name": None,
    "phone": None, "jfc_campaign": None,
}


def build(users, postings, calls):
    # every provider's postings
    jobs_by_owner = collections.defaultdict(list)
    for item_id, posting in postings.items():
        owner = posting.get("created_by")
        if owner:
            jobs_by_owner[owner].append(item_id)

    # route each call to a provider via its job_id
    calls_by_owner = collections.defaultdict(list)
    unresolved = collections.defaultdict(list)
    for c in calls:
        job_id = str(c.get("job_id") or "")
        posting = postings.get(job_id)
        owner = posting.get("created_by") if posting else None
        if owner:
            calls_by_owner[owner].append(c)
        else:
            phone = norm_phone(c.get("contact_phone"))
            if phone:
                unresolved[phone].append(c)

    rows = []
    for owner, posting_ids in jobs_by_owner.items():
        user = users.get(owner, {})
        row = {
            "provider_id": owner,
            "job_ids": sorted(posting_ids),
            "total_postings": len(posting_ids),
            "total_jobs": len(posting_ids),
            "instance": user.get("_instance") or postings[posting_ids[0]].get("_instance"),
            "onboarded_at": user.get("created_at"),
            "in_bluedot": True,
            "source_id": None,
        }
        group = calls_by_owner.get(owner)
        row.update(call_behaviour(group) if group else dict(NEVER_CALLED))
        rows.append(row)

    # providers in Blue Dot with no posting at all
    for uid, user in users.items():
        if uid in jobs_by_owner:
            continue
        row = {
            "provider_id": uid, "job_ids": [], "total_postings": 0, "total_jobs": 0,
            "instance": user.get("_instance"), "onboarded_at": user.get("created_at"),
            "in_bluedot": True, "source_id": None,
        }
        row.update(dict(NEVER_CALLED))
        rows.append(row)

    # calls we could not tie to a Blue Dot provider
    for phone, group in unresolved.items():
        row = {
            "provider_id": f"phone:{phone}", "job_ids": [], "total_postings": 0,
            "total_jobs": len({x.get("job_id") for x in group if x.get("job_id")}),
            "instance": None, "onboarded_at": None, "in_bluedot": False, "source_id": None,
        }
        row.update(call_behaviour(group))
        rows.append(row)
    return rows


def upsert(rows, url, key, chunk_size=500):
    endpoint = f"{url.rstrip('/')}/rest/v1/aggregated_provider_journey"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        r = requests.post(endpoint, headers=headers, params={"on_conflict": "provider_id"},
                          json=chunk, timeout=180)
        if r.status_code >= 400:
            print(r.text[:700])
            raise RuntimeError(f"upsert failed at {start + 1}-{start + len(chunk)}")
        print(f"  {start + len(chunk)}/{len(rows)} -> {r.status_code}")


def drop_stale(url, key):
    """Rows from the older phone-keyed build have no provider_id, so they can
    never conflict with the new keys and would otherwise sit alongside them."""
    endpoint = f"{url.rstrip('/')}/rest/v1/aggregated_provider_journey"
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    counted = dict(headers, **{"Prefer": "count=exact"})
    n = int(requests.get(endpoint, headers=counted,
                         params={"select": "provider_id", "provider_id": "is.null", "limit": 1},
                         timeout=180).headers["content-range"].split("/")[-1])
    if n:
        r = requests.delete(endpoint, headers=headers, params={"provider_id": "is.null"}, timeout=300)
        print(f"  removed {n} rows from the old phone-keyed build -> {r.status_code}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--supabase-key", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url = env_or_arg("SUPABASE_URL", args.supabase_url)
    key = env_or_arg("SUPABASE_SECRET_KEY", args.supabase_key) or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    print("Loading Blue Dot provider dumps...")
    users, postings = load_bluedot()
    print(f"  provider users: {len(users)}  postings: {len(postings)}")

    print("Reading dkb_mastersheet...")
    calls = fetch_calls(url, key)
    print(f"  calls: {len(calls)}")

    rows = build(users, postings, calls)
    called = [r for r in rows if r["ever_called"]]
    print(f"\n  total rows       : {len(rows)}")
    print(f"  in Blue Dot      : {sum(1 for r in rows if r['in_bluedot'])}")
    print(f"  call-only        : {sum(1 for r in rows if not r['in_bluedot'])}")
    print(f"  ever called      : {len(called)}")
    print(f"  never called     : {len(rows) - len(called)}")
    print(f"  calls attributed : {sum(len(r['call_ids']) for r in rows)} of {len(calls)}")
    print(f"  total vacancies  : {sum(r['total_vacancies'] for r in rows)}")

    if args.dry_run:
        print("\n--dry-run: nothing written. Samples:")
        for r in sorted(called, key=lambda x: -(x["max_intent_score"] or 0))[:2]:
            print("   ", {k: v for k, v in r.items() if v not in (None, [], 0, False)})
        return

    print("\nUpserting...")
    upsert(rows, url, key)
    drop_stale(url, key)
    print(f"Done. {len(rows)} providers written.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
