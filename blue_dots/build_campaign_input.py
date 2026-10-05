"""Builds the contact list for a seeker voice campaign, ready to upload to Raya.

    python build_campaign_input.py --jfc Hubli-Dharwad --limit 500
    python build_campaign_input.py --jfc Ghaziabad --min-confidence 4 --limit 1000

Writes a CSV to data/. It does NOT touch Raya - this whole pipeline only ever
reads from that API, and a batch is created by a person uploading the file.
Generating a list and dialling it are deliberately separate steps.

TWO SCORES DECIDE THE LIST

    call_confidence_score   who is worth calling     aggregated_seeker_journey
    match_score             what to offer them       seeker_job_matches

Neither alone is enough: a high-confidence seeker with no matching job wastes
a call, and a perfect job match on someone who never answers wastes a dial.
The list is ordered by call confidence and every row carries that seeker's own
matched jobs.

WHAT THE BOT NEEDS
From bot_schemas.py, the KKB Signals bots take three agent_args:

    contact_memory   what we already know, or 'Not Available'
    recommendations  JSON string: [{job_id, role, company, qualification}]
    location         free text address, optional

recommendations is the one that matters - transform.py reads it back out of
agent_args as recommendations_input, and it is the most trustworthy record of
what the seeker was actually offered.
"""
import argparse
import collections
import csv
import io
import json
import os

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

OUT_DIR = "data"
# How many jobs to hand the bot per seeker. The bot reads these aloud, and the
# transcripts show people leaving during long preambles - three is what the
# live campaigns actually use.
JOBS_PER_SEEKER = 3


def page(url, key, table, select, **extra):
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        resp = requests.get(f"{url}/rest/v1/{table}", headers=headers, timeout=300,
                            params={"select": select, "offset": offset,
                                    "limit": 1000, **extra})
        chunk = resp.json()
        if not isinstance(chunk, list) or not chunk:
            break
        rows += chunk
        if len(chunk) < 1000:
            break
        offset += len(chunk)
    return rows


def contact_memory(seeker):
    """What we already know, in the one short line the bot can use.

    'Not Available' is the documented value for a seeker we have never
    reached, and it matters: the bot opens differently when it has nothing,
    and a fabricated memory makes it claim things that were never said.
    """
    bits = []
    calls = seeker.get("total_calls_made") or 0
    if calls:
        bits.append(f"called {calls} time{'s' if calls != 1 else ''}")
    if seeker.get("ever_answered"):
        bits.append("has answered before")
    if seeker.get("ever_engaged"):
        bits.append("engaged in a previous call")
    if seeker.get("ever_applied"):
        bits.append("has applied to a job before")
    elif seeker.get("max_intent_score") and float(seeker["max_intent_score"]) >= 7:
        bits.append("was interested previously but never applied")
    if seeker.get("trrain_callback_requested"):
        bits.append("asked for a callback")
    return "; ".join(bits) if bits else "Not Available"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jfc", help="Ghaziabad or Hubli-Dharwad")
    ap.add_argument("--instance", help="UP or KA")
    ap.add_argument("--limit", type=int, default=500, help="contacts in the batch")
    ap.add_argument("--min-confidence", type=float, default=0.0, dest="min_conf",
                    help="skip seekers below this call_confidence_score")
    ap.add_argument("--min-match", type=float, default=4.0, dest="min_match",
                    help="ignore matches below this score; 2 and below are filler")
    ap.add_argument("--jobs", type=int, default=JOBS_PER_SEEKER)
    args = ap.parse_args()

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    extra = {}
    if args.jfc:
        extra["jfc_campaign"] = f"eq.{args.jfc}"
    if args.instance:
        extra["instance"] = f"eq.{args.instance}"

    seekers = page(url, key, "aggregated_seeker_journey",
                   "seeker_id,seeker_name,bluedot_name,phone,profile_ids,instance,"
                   "jfc_campaign,call_confidence_score,call_confidence_reason,"
                   "total_calls_made,ever_answered,ever_engaged,ever_applied,"
                   "max_intent_score,trrain_do_not_call,trrain_callback_requested",
                   **extra)
    print(f"{len(seekers)} seekers in scope")

    matches = page(url, key, "seeker_job_matches",
                   "instance,seeker_item_id,job_item_id,match_score,match_rank",
                   order="match_score.desc")
    jobs = page(url, key, "bluedot_items", "instance,item_id,item_state",
                item_domain="eq.provider", pause_status="eq.N")
    catalogue = {(j["instance"], str(j["item_id"])): (j.get("item_state") or {})
                 for j in jobs}
    by_profile = collections.defaultdict(list)
    for m in matches:
        if float(m["match_score"]) >= args.min_match:
            by_profile[(m["instance"], m["seeker_item_id"])].append(m)
    print(f"{len(matches)} match rows, {len(by_profile)} profiles with a match "
          f"at or above {args.min_match}")

    dropped = collections.Counter()
    built = []
    for s in seekers:
        if s.get("trrain_do_not_call"):
            dropped["asked us to stop"] += 1
            continue
        if not s.get("phone"):
            dropped["no phone number"] += 1
            continue
        score = s.get("call_confidence_score")
        if score is None or float(score) < args.min_conf:
            dropped[f"confidence below {args.min_conf}"] += 1
            continue

        # profile_ids is an array: one seeker can hold several Blue Dot profiles
        found = []
        for pid in (s.get("profile_ids") or []):
            found += by_profile.get((s["instance"], str(pid)), [])
        if not found:
            dropped["no job match"] += 1
            continue
        found.sort(key=lambda m: -float(m["match_score"]))

        seen, recs = set(), []
        for m in found:
            jid = str(m["job_item_id"])
            if jid in seen:
                continue
            seen.add(jid)
            state = catalogue.get((m["instance"], jid)) or {}
            recs.append({
                "job_id": jid,
                "role": state.get("role"),
                "company": state.get("jobProviderName"),
                "qualification": state.get("minEducationalInstitute"),
            })
            if len(recs) >= args.jobs:
                break

        built.append({
            "name": s.get("bluedot_name") or s.get("seeker_name") or "",
            "phone": str(s["phone"]),
            # agent_args, exactly as bot_schemas.py documents them
            "contact_memory": contact_memory(s),
            "recommendations": json.dumps(recs, ensure_ascii=False),
            "location": "",
            # not sent to the bot - for whoever reviews the list
            "_call_confidence": score,
            "_best_match": found[0]["match_score"],
            "_why_called": s.get("call_confidence_reason") or "",
        })

    built.sort(key=lambda r: (-float(r["_call_confidence"]), -float(r["_best_match"])))
    batch = built[:args.limit]

    print(f"\n  eligible      {len(built)}")
    for reason, n in dropped.most_common():
        print(f"  dropped       {n:>6}  {reason}")
    print(f"  in this batch {len(batch)}")
    if batch:
        conf = [float(r["_call_confidence"]) for r in batch]
        mat = [float(r["_best_match"]) for r in batch]
        print(f"\n  call confidence in batch: {min(conf):.1f} - {max(conf):.1f}")
        print(f"  best match in batch     : {min(mat):.1f} - {max(mat):.1f}")

    os.makedirs(OUT_DIR, exist_ok=True)
    tag = (args.jfc or args.instance or "all").lower().replace(" ", "_")
    path = os.path.join(OUT_DIR, f"campaign_input_{tag}.csv")
    cols = ["name", "phone", "contact_memory", "recommendations", "location",
            "_call_confidence", "_best_match", "_why_called"]
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(batch)
    print(f"\n  wrote {path}")
    print("  upload it to Raya by hand - this script never writes to the API.")

    if batch:
        print("\n  first row:")
        for k in cols:
            print(f"    {k:<18}{str(batch[0][k])[:96]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
