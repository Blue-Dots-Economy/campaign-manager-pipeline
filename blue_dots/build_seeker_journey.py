"""Builds aggregated_seeker_journey: one row per Blue Dot SEEKER, with their
call behaviour attached.

Three sources:
  * seeker_csv_dumps_*/user.csv     - the seeker roster, and the only place a
                                      phone number appears (the bluedot jsonl
                                      dumps are non-PII and have none)
  * seeker_csv_dumps_*/profile.csv  - user_id -> profile id, so a seeker can be
                                      tied to their bluedot profile records
  * kkb_mastersheet                 - the calls, joined on phone

Every Blue Dot seeker gets a row whether or not we ever called them. Anyone we
called who has no Blue Dot record also gets a row, keyed phone:<number> and
flagged in_bluedot = false.

Re-runnable: upserts on seeker_id.

    python build_seeker_journey.py [--dry-run]
"""
import argparse
import collections
import csv
import io
import os
import re
import sys

import requests
from dotenv import load_dotenv

from pipeline import env_or_arg

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
load_dotenv()

DUMP_DIRS = {
    "KA": "seeker_csv_dumps_20260828-100001/seeker_csv_dumps_20260828-100001",
    "UP": "seeker_csv_dumps_up_20260828-100001/seeker_csv_dumps_up_20260828-100001",
}

CALL_COLUMNS = (
    "call_id,phone,phone_number,seeker_name,campaign_name,campaign_date,jfc_campaign,agent_name,"
    "intent_score,applications_count,applied_to_job,call_answered,call_engaged,drop_reasom"
)


def norm_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else None


def truthy(v):
    return v is True or str(v).strip().lower() in ("true", "yes", "1")


def load_roster_from_db(url, key, page_size=1000):
    """The Blue Dot roster, recovered from a previous run of this script.

    seeker_csv_dumps_*/user.csv is the only file that carries phone numbers,
    and it is a manual export that has already been lost once. Every row this
    script writes stores the same pairing, so the table itself is a usable
    roster when the export is missing: it covers everyone previously matched,
    and misses only seekers who registered since the last export.
    """
    endpoint = f"{url.rstrip('/')}/rest/v1/aggregated_seeker_journey"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    seekers, offset = {}, 0
    while True:
        r = requests.get(endpoint, headers=headers, params={
            "select": "seeker_id,phone,bluedot_name,onboarded_at,instance,profile_ids",
            "in_bluedot": "is.true", "order": "seeker_id",
            "offset": offset, "limit": page_size}, timeout=180)
        if r.status_code >= 400:
            raise RuntimeError(f"roster read failed: {r.status_code} {r.text[:200]}")
        page = r.json()
        if not page:
            break
        for row in page:
            uid = (row.get("seeker_id") or "").strip()
            if not uid or uid.startswith("phone:"):
                continue
            seekers[uid] = {
                "seeker_id": uid,
                "phone": norm_phone(row.get("phone")),
                "bluedot_name": row.get("bluedot_name"),
                "onboarded_at": row.get("onboarded_at"),
                "instance": row.get("instance"),
                "profile_ids": row.get("profile_ids") or [],
            }
        if len(page) < page_size:
            break
        offset += len(page)
    return seekers


def load_bluedot():
    """Blue Dot seekers keyed on user id, each with phone and profile ids."""
    seekers = {}
    for instance, directory in DUMP_DIRS.items():
        user_csv = os.path.join(directory, "user.csv")
        profile_csv = os.path.join(directory, "profile.csv")
        if not os.path.exists(user_csv):
            print(f"  WARNING: {user_csv} missing, skipping {instance}")
            continue
        for row in csv.DictReader(io.open(user_csv, encoding="utf-8")):
            uid = (row.get("id") or "").strip()
            if not uid:
                continue
            seekers[uid] = {
                "seeker_id": uid,
                "phone": norm_phone(row.get("phone_number")),
                "bluedot_name": (row.get("name") or "").strip() or None,
                "onboarded_at": (row.get("created_at") or "").strip() or None,
                "instance": instance,
                "profile_ids": [],
            }
        for row in csv.DictReader(io.open(profile_csv, encoding="utf-8")):
            uid = (row.get("user_id") or "").strip()
            pid = (row.get("id") or "").strip()
            if uid in seekers and pid:
                seekers[uid]["profile_ids"].append(pid)
    return seekers


def fetch_calls(url, key, page_size=1000):
    endpoint = f"{url.rstrip('/')}/rest/v1/kkb_mastersheet"
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


def call_behaviour(group):
    """The call-derived half of a seeker row."""
    group = sorted(group, key=lambda x: str(x.get("campaign_date") or ""), reverse=True)

    def last_where(pred):
        ds = [x.get("campaign_date") for x in group if x.get("campaign_date") and pred(x)]
        return max(ds) if ds else None

    scores = [float(x["intent_score"]) for x in group if x.get("intent_score") is not None]
    apps = [int(x["applications_count"]) for x in group if x.get("applications_count") is not None]
    return {
        "call_ids": [str(x["call_id"]) for x in group if x.get("call_id")],
        "ever_called": True,
        "total_calls_made": len(group),
        "total_campaigns": len({x.get("campaign_name") for x in group if x.get("campaign_name")}),
        "total_application": sum(apps) if apps else 0,
        "last_call_eng": last_where(lambda x: truthy(x.get("call_engaged"))),
        "last_call_answered": last_where(lambda x: truthy(x.get("call_answered"))),
        "last_call_date": last_where(lambda x: True),
        "drop_reason": next((x.get("drop_reasom") for x in group if x.get("drop_reasom")), None),
        "avg_intent_score": round(sum(scores) / len(scores), 2) if scores else None,
        "max_intent_score": max(scores) if scores else None,
        "ever_applied": any(truthy(x.get("applied_to_job")) for x in group),
        "ever_answered": any(truthy(x.get("call_answered")) for x in group),
        "ever_engaged": any(truthy(x.get("call_engaged")) for x in group),
        "jfc_campaign": group[0].get("jfc_campaign"),
        "agent_name": group[0].get("agent_name"),
        "seeker_name": next((x.get("seeker_name") for x in group if x.get("seeker_name")), None),
    }


TRRAIN_COLUMNS = ("phone,campaign_name,campaign_date,trrain_pitched,trrain_interest,"
                  "do_not_call,callback_requested,right_person")


def fetch_trrain(url, key, page_size=1000):
    """TRRAIN is a later stage of the same funnel - the service-offer call made
    to seekers who already applied. It joins on phone, like the calls do."""
    endpoint = f"{url.rstrip('/')}/rest/v1/trrain_mastersheet"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        r = requests.get(endpoint, headers=headers,
                         params={"select": TRRAIN_COLUMNS, "order": "call_id",
                                 "offset": offset, "limit": page_size}, timeout=180)
        if r.status_code >= 400:
            print(f"  WARNING: trrain_mastersheet unreadable ({r.status_code}), skipping")
            return []
        page = r.json()
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    return rows


# interest is ranked, so the strongest answer across attempts wins
INTEREST_RANK = {"Yes": 3, "Maybe": 2, "No": 1}


def trrain_behaviour(group):
    """The TRRAIN half of a seeker row."""
    interests = [g.get("trrain_interest") for g in group if g.get("trrain_interest")]
    best = max(interests, key=lambda x: INTEREST_RANK.get(x, 0)) if interests else None
    dates = [g.get("campaign_date") for g in group if g.get("campaign_date")]
    return {
        "trrain_calls": len(group),
        "trrain_pitched": any(truthy(g.get("trrain_pitched")) for g in group),
        "trrain_interest": best,
        # an opt-out on ANY call stands for good - never let a later call clear it
        "trrain_do_not_call": any(truthy(g.get("do_not_call")) for g in group),
        "trrain_last_date": max(dates) if dates else None,
        "trrain_callback_requested": next(
            (g.get("callback_requested") for g in group if g.get("callback_requested")), None),
    }


NO_TRRAIN = {
    "trrain_calls": 0, "trrain_pitched": False, "trrain_interest": None,
    "trrain_do_not_call": False, "trrain_last_date": None,
    "trrain_callback_requested": None,
}


NEVER_CALLED = {
    "call_ids": [], "ever_called": False, "total_calls_made": 0, "total_campaigns": 0,
    "total_application": 0, "last_call_eng": None, "last_call_answered": None,
    "last_call_date": None, "drop_reason": None, "avg_intent_score": None,
    "max_intent_score": None, "ever_applied": False, "ever_answered": False,
    "ever_engaged": False, "jfc_campaign": None, "agent_name": None, "seeker_name": None,
}


def primary_user_per_phone(seekers):
    """1,191 phones carry more than one Blue Dot user - the same person
    registered twice (sometimes once per instance). Calls must be attributed to
    exactly one of them or every aggregate double-counts, so the earliest
    registration wins and the rest are left with no calls."""
    by_phone = collections.defaultdict(list)
    for uid, s in seekers.items():
        if s["phone"]:
            by_phone[s["phone"]].append(uid)
    primary = {}
    for phone, uids in by_phone.items():
        uids.sort(key=lambda u: str(seekers[u].get("onboarded_at") or "9999"))
        primary[phone] = uids[0]
    return primary


def build(seekers, calls, trrain=None):
    by_phone = collections.defaultdict(list)
    for c in calls:
        p = norm_phone(c.get("phone") or c.get("phone_number"))
        if p:
            by_phone[p].append(c)
    tr_phone = collections.defaultdict(list)
    for t in (trrain or []):
        p = norm_phone(t.get("phone"))
        if p:
            tr_phone[p].append(t)
    primary = primary_user_per_phone(seekers)

    rows = []
    matched_phones = set()
    for uid, s in seekers.items():
        row = dict(s)
        # only the canonical record for a shared phone receives the calls
        group = by_phone.get(s["phone"]) if (s["phone"] and primary.get(s["phone"]) == uid) else None
        row.update(call_behaviour(group) if group else dict(NEVER_CALLED))
        tr = tr_phone.get(s["phone"]) if (s["phone"] and primary.get(s["phone"]) == uid) else None
        row.update(trrain_behaviour(tr) if tr else dict(NO_TRRAIN))
        # claim the phone for EITHER kind of call, so the loops below never
        # attach the same TRRAIN rows to a second phone:<number> row
        if group or tr:
            matched_phones.add(s["phone"])
            # keep the Blue Dot name; fall back to what the bot heard
            row["seeker_name"] = s["bluedot_name"] or row["seeker_name"]
        row["in_bluedot"] = True
        row["source_id"] = None
        rows.append(row)

    # people we called who aren't in Blue Dot at all
    for phone, group in by_phone.items():
        if phone in matched_phones:
            continue
        row = {
            "seeker_id": f"phone:{phone}", "phone": phone, "profile_ids": [],
            "bluedot_name": None, "onboarded_at": None, "instance": None,
            "in_bluedot": False, "source_id": None,
        }
        row.update(call_behaviour(group))
        row.update(trrain_behaviour(tr_phone[phone]) if tr_phone.get(phone) else dict(NO_TRRAIN))
        rows.append(row)

    # someone TRRAIN called who has no KKB call and no Blue Dot record
    for phone, group in tr_phone.items():
        if phone in matched_phones or phone in by_phone:
            continue
        row = {
            "seeker_id": f"phone:{phone}", "phone": phone, "profile_ids": [],
            "bluedot_name": None, "onboarded_at": None, "instance": None,
            "in_bluedot": False, "source_id": None,
        }
        row.update(dict(NEVER_CALLED))
        row.update(trrain_behaviour(group))
        rows.append(row)
    return rows


def upsert(rows, url, key, chunk_size=500):
    endpoint = f"{url.rstrip('/')}/rest/v1/aggregated_seeker_journey"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        r = requests.post(endpoint, headers=headers, params={"on_conflict": "seeker_id"},
                          json=chunk, timeout=180)
        if r.status_code >= 400:
            print(r.text[:700])
            raise RuntimeError(f"upsert failed at {start + 1}-{start + len(chunk)}")
        if (start // chunk_size) % 20 == 0 or start + len(chunk) == len(rows):
            print(f"  {start + len(chunk)}/{len(rows)} -> {r.status_code}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--supabase-key", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url = env_or_arg("SUPABASE_URL", args.supabase_url)
    key = env_or_arg("SUPABASE_SECRET_KEY", args.supabase_key) or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    print("Loading Blue Dot seeker dumps...")
    seekers = load_bluedot()
    if seekers:
        print(f"  from CSV exports: {len(seekers)} seekers")
    else:
        print("  no CSV exports found - recovering the roster from aggregated_seeker_journey")
        seekers = load_roster_from_db(url, key)
        print(f"  from the database: {len(seekers)} seekers")
    print(f"  with phone: {sum(1 for s in seekers.values() if s['phone'])}")

    # Without a roster every caller becomes a phone:<number> row and the real
    # seeker rows keep stale call data - worse than not running at all.
    if not seekers:
        raise SystemExit(
            "no seeker roster available: the CSV exports are missing and "
            "aggregated_seeker_journey returned no Blue Dot rows. "
            "Re-export seeker_csv_dumps_* before running this."
        )

    print("Reading kkb_mastersheet...")
    calls = fetch_calls(url, key)
    print(f"  calls: {len(calls)}")

    print("Reading trrain_mastersheet...")
    trrain = fetch_trrain(url, key)
    print(f"  trrain calls: {len(trrain)}")

    rows = build(seekers, calls, trrain)
    called = [r for r in rows if r["ever_called"]]
    print(f"\n  total rows       : {len(rows)}")
    print(f"  in Blue Dot      : {sum(1 for r in rows if r['in_bluedot'])}")
    print(f"  call-only        : {sum(1 for r in rows if not r['in_bluedot'])}")
    print(f"  ever called      : {len(called)}")
    print(f"  never called     : {len(rows) - len(called)}")
    print(f"  calls attributed : {sum(len(r['call_ids']) for r in rows)}")
    print(f"  trrain attached  : {sum(r['trrain_calls'] for r in rows)}")
    print(f"  DO NOT CALL      : {sum(1 for r in rows if r['trrain_do_not_call'])}")

    if args.dry_run:
        print("\n--dry-run: nothing written. Samples:")
        for r in (called[0], next(r for r in rows if not r["ever_called"]),
                  next(r for r in rows if not r["in_bluedot"])):
            print("   ", {k: v for k, v in r.items() if v not in (None, [], 0, False)})
        return

    print("\nUpserting...")
    upsert(rows, url, key)
    print(f"Done. {len(rows)} seekers written.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
