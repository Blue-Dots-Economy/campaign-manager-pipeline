"""Loads inbound calls into kkb_mastersheet.

Inbound calls never came through the normal pipeline. Everything else is
found by walking batches, and an inbound call has no batch and no contact
record - the seeker simply rang in - so /api/batch/{id}/contacts cannot see
them. /api/call?agent_id= can, which is what this uses.

Direction is inferred, because Raya records none: inbound means caller_no is
set and to_number is empty. See raya_client.is_inbound.

These rows differ from outbound ones in three visible ways, all deliberate:

    channel      "Inbound"
    call_id      the call's uuid, since there is no contact_id to key on
    batch_id     null, and campaign_day null with it

Everything else - intent score, apply attempts, job lists - is produced by
exactly the same code as outbound, because make_inbound_row adapts the call
into the shape make_row expects rather than reimplementing it.

    python load_inbound.py --dry-run          # fetch, build, write a CSV
    python load_inbound.py --dry-run --limit 50
    python load_inbound.py                    # push to Supabase
"""
import argparse
import collections
import csv
import io
import json
import os

import requests
from dotenv import load_dotenv

from pipeline import (AGENT_JFC, DKB_AGENT_JFC, EXCLUDED_AGENTS,
                      INBOUND_AGENT_JFC, TEST_PHONES, agent_names,
                      jfc_for_agent)
from push_sheets import norm_phone
from raya_client import fetch_all_calls, fetch_call_detail, is_inbound
from transform import make_inbound_row
from transform_dkb import make_dkb_inbound_row

load_dotenv()

TABLE = "kkb_mastersheet"
OUT_DIR = "data"
# Every agent that has taken an inbound call: the inbound-only bots, plus the
# outbound KKB bots people ring back on - 1,515 calls came in that way, and
# leaving them out would have looked complete while missing a third of them.
#
# The DKB bots also take inbound calls (~277). Those belong in
# dkb_mastersheet, which has a different shape and a different transform, so
# they are handled separately rather than forced into this table.
CANDIDATE_AGENTS = tuple(INBOUND_AGENT_JFC) + tuple(AGENT_JFC)
# --dkb runs the same walk against the employer bots and writes the other
# table. Employers ring back too, and their calls were just as invisible.
DKB_TABLE = "dkb_mastersheet"
DKB_AGENTS = tuple(DKB_AGENT_JFC)


def campaign_name_for(agent_name, jfc):
    """inbound_gzb_hindi style, matching the lowercase campaign convention."""
    region = {"Ghaziabad": "gzb", "Hubli-Dharwad": "hubli"}.get(jfc, "unknown")
    lang = "kannada" if "kannada" in (agent_name or "").lower() else "hindi"
    return f"inbound_{region}_{lang}"


def existing_call_ids(url, key, table=TABLE):
    """Everything already in the table, so a re-run adds only what is new."""
    ids, offset = set(), 0
    while True:
        r = requests.get(f"{url.rstrip('/')}/rest/v1/{table}",
                         headers={"apikey": key, "Authorization": f"Bearer {key}"},
                         params={"select": "call_id", "order": "call_id",
                                 "offset": offset, "limit": 1000}, timeout=300)
        if r.status_code >= 400:
            raise SystemExit(f"{table}: {r.status_code} {r.text[:300]}")
        chunk = r.json()
        if not chunk:
            break
        ids.update(str(c["call_id"]) for c in chunk)
        if len(chunk) < 1000:
            break
        offset += len(chunk)
    return ids


def push(url, key, rows, size=200, table=TABLE):
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    done = 0
    for start in range(0, len(rows), size):
        chunk = rows[start:start + size]
        # PostgREST rejects the whole batch unless every object has the same
        # keys, so the rows are squared off before they go
        keys = sorted({k for r in chunk for k in r})
        squared = [{k: r.get(k) for k in keys} for r in chunk]
        r = requests.post(endpoint, headers=headers,
                          params={"on_conflict": "call_id"},
                          json=squared, timeout=300)
        if r.status_code >= 400:
            raise SystemExit(f"push failed at row {start}: "
                             f"{r.status_code} {r.text[:400]}")
        done += len(chunk)
        print(f"    {done}/{len(rows)}")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dkb", action="store_true",
                    help="load employer call-backs into dkb_mastersheet "
                         "instead (needs sql/alter_dkb_channel.sql first)")
    ap.add_argument("--limit", type=int, help="stop after N inbound calls (for a smoke test)")
    ap.add_argument("--min-duration", type=int, default=0,
                    help="skip calls shorter than this many seconds")
    args = ap.parse_args()

    api_key = os.getenv("RAYA_API_KEY")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    names = agent_names(api_key)
    table = DKB_TABLE if args.dkb else TABLE
    agents = DKB_AGENTS if args.dkb else CANDIDATE_AGENTS
    print(f"Target table: {table}")

    print("Listing calls per agent...")
    found = []
    for aid in agents:
        if aid in EXCLUDED_AGENTS:
            continue
        try:
            calls = fetch_all_calls(api_key, aid)
        except Exception as exc:
            print(f"  {aid}: {exc}")
            continue
        inbound = [c for c in calls if is_inbound(c)]
        print(f"  {len(inbound):>5} inbound of {len(calls):>5}   "
              f"{names.get(aid, aid)}")
        for c in inbound:
            c["_agent_id"] = aid
        found.extend(inbound)
    print(f"  {len(found)} inbound calls total")

    if args.min_duration:
        before = len(found)
        found = [c for c in found
                 if (c.get("call_duration") or 0) >= args.min_duration]
        print(f"  {before - len(found)} dropped under {args.min_duration}s")

    seen = existing_call_ids(url, key, table)
    fresh = [c for c in found if str(c.get("uuid")) not in seen]
    print(f"  {len(found) - len(fresh)} already in {table}, {len(fresh)} new")
    if args.limit:
        fresh = fresh[:args.limit]
        print(f"  --limit: keeping {len(fresh)}")

    print("\nFetching call detail (call_output and transcript)...")
    rows, failed = [], 0
    for n, call in enumerate(fresh, start=1):
        aid = call["_agent_id"]
        try:
            detail = fetch_call_detail(api_key, call["uuid"]) or {}
        except Exception:
            detail = {}
        if not detail:
            failed += 1
            detail = call
        detail.setdefault("uuid", call.get("uuid"))
        jfc = jfc_for_agent(aid)
        name = names.get(aid)
        if args.dkb:
            row = make_dkb_inbound_row(detail, campaign_name_for(name, jfc),
                                       jfc=jfc)
        else:
            row = make_inbound_row(detail, campaign_name_for(name, jfc),
                                   jfc=jfc, agent_id=aid, agent_name=name)
        # set here, not patched afterwards: this row is upserted on every
        # run and would otherwise lose the flag each time
        if norm_phone(row.get('phone') or row.get('contact_phone')) in TEST_PHONES:
            row['test_flag'] = True
        rows.append(row)
        if n % 250 == 0:
            print(f"  {n}/{len(fresh)}")
    print(f"  built {len(rows)} rows ({failed} without detail)")

    if rows:
        spread = collections.Counter(r["campaign_name"] for r in rows)
        print("\n  by campaign:")
        for k, v in spread.most_common():
            print(f"    {v:>6}  {k}")
        flagged = sum(1 for r in rows if r.get("test_flag"))
        print(f"  {flagged} rows flagged as test calls")
        if args.dkb:
            # the employer side has no apply step; the signal is the score
            scored = [r.get("intent_score") or 0 for r in rows]
            print(f"  median intent {sorted(scored)[len(scored) // 2]}, "
                  f"{sum(1 for s in scored if s >= 5)} scoring 5+")
        else:
            answered = sum(1 for r in rows if r.get("call_answered"))
            applied = sum(1 for r in rows if r.get("applied_to_job"))
            print(f"  answered {answered}, applied_to_job {applied}")

        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR,
                            "inbound_dkb_rows.csv" if args.dkb
                            else "inbound_rows.csv")
        cols = sorted({k for r in rows for k in r})
        with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                w.writerow([json.dumps(r[c]) if isinstance(r.get(c), (list, dict))
                            else r.get(c) for c in cols])
        print(f"  wrote {path}")

    if args.dry_run:
        print("\n--dry-run: nothing written to Supabase.")
        return
    if not rows:
        print("\nnothing new to push.")
        return
    print(f"\nPushing {len(rows)} rows to {table}...")
    print(f"done, {push(url, key, rows, table=table)} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
