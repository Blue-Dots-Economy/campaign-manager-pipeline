"""Loads Purple Dots calls from Raya into purple_dots_calls.

Writes to the PURPLE DOTS Supabase project only. The URL and key come from
this folder's own .env, and nothing here imports from the Blue Dots pipeline -
see README for why that separation is physical rather than just a convention.

Purple Dots has no batches in the Raya account we can currently see, so this
walks /api/call?agent_id= per agent rather than /api/batch/{id}/contacts.
That endpoint returns every call an agent made, batch or no batch, so it is
the right one either way.

    python load_purple.py --dry-run            # writes a CSV, touches nothing
    python load_purple.py --dry-run --limit 20
    python load_purple.py                      # pushes to Supabase
    python load_purple.py --batch 2385         # one batch only
    python load_purple.py --agent <id>         # one agent only
    python load_purple.py --agents             # just list what is out there
"""
import argparse
import collections
import csv
import io
import json
import os

import requests
from dotenv import load_dotenv

from raya_client import (fetch_all_agents, fetch_all_batches, fetch_all_calls,
                         fetch_all_contacts, fetch_call_detail, fetch_calls)
from transform_purple import make_connections, make_row

load_dotenv()

TABLE = "purple_dots_calls"
CONN_TABLE = "purple_dots_connections"
OUT_DIR = "data"

# The Purple Dots bots, as they exist in the Raya account the Blue Dots key
# can see. Named explicitly rather than matched on "purple" in the name: a
# rename or a new "Purple-dots-v3" should be a decision someone makes, not
# something a substring quietly picks up and loads into a live table.
#
# None of these has a batch, and Basti Launch Agent emits no call_output at
# all, so this list almost certainly does NOT cover Purple Dots production.
# See README - resolving that needs the right Raya key, not more code.
AGENTS = {
    # The live bot, 956 calls across 6 batches.
    #
    # ONLY BATCH 2385 IS REAL DATA. The sheet owner confirmed on 30 Sept 2026
    # that everything else on this agent is testing - including the 933 calls
    # that came through no batch at all, which between them held 151 completed
    # journeys and 235 provider connections. Those were deleted deliberately.
    #
    # So run this scoped, or it will put the test data back:
    #     python load_purple.py --batch 2385
    "db21effb-91a9-4af6-b3d2-8efed46c0415": "Purple-dots-with-APIs-V2-Latest",
    "7ecc138a-8f9d-40e8-b35c-e7b6971dbd01": "Purple-Dots-Outbound-290720261133",
    "1e8faf6a-db15-4796-81f1-55b9e9aab358": "Testing Agent- Purple Dots",
}

# test bots: real calls in Raya, meaningless here
EXCLUDED = {
    "1e8faf6a-db15-4796-81f1-55b9e9aab358",   # Testing Agent- Purple Dots
}


def env(need_supabase=True):
    """--agents only reads from Raya, so it should not need Supabase creds."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    api = os.getenv("RAYA_API_KEY")
    required = [("RAYA_API_KEY", api)]
    if need_supabase:
        required += [("SUPABASE_URL", url), ("SUPABASE_SECRET_KEY", key)]
    missing = [n for n, v in required if not v]
    if missing:
        raise SystemExit(
            f"missing from .env: {', '.join(missing)}\n"
            f"copy .env.example to .env and fill it in.")
    # a wrong-project write is the failure this whole folder exists to prevent,
    # so it is checked rather than trusted
    if url and "vqvonmoktpvfvzlhtiqo" in url:
        raise SystemExit(
            "SUPABASE_URL points at the Blue Dots project. This loader writes "
            "Purple Dots data; set it to the Purple Dots project.")
    return url, key, api


def list_agents(api_key):
    """Every Raya agent, so a Purple Dots bot missing from AGENTS is visible."""
    known = set(AGENTS)
    print(f"{'calls':>7}  {'in AGENTS':<11}agent")
    for a in sorted(fetch_all_agents(api_key), key=lambda x: (x.get("name") or "")):
        aid, name = a.get("id"), a.get("name") or ""
        if not any(t in name.lower() for t in ("purple", "basti", "dots")):
            continue
        try:
            _, total = fetch_calls(api_key, aid, limit=1)
        except Exception as exc:
            total = f"ERR {exc}"
        mark = "yes" if aid in known else "NO  <-- unmapped"
        print(f"{str(total):>7}  {mark:<11}{name!r}  {aid}")


def existing_ids(url, key):
    ids, offset = set(), 0
    while True:
        r = requests.get(f"{url.rstrip('/')}/rest/v1/{TABLE}",
                         headers={"apikey": key, "Authorization": f"Bearer {key}"},
                         params={"select": "call_id", "order": "call_id",
                                 "offset": offset, "limit": 1000}, timeout=300)
        if r.status_code == 404:
            raise SystemExit(f"{TABLE} does not exist yet - "
                             f"run sql/create_purple_dots.sql first.")
        if r.status_code >= 400:
            raise SystemExit(f"{TABLE}: {r.status_code} {r.text[:300]}")
        chunk = r.json()
        if not chunk:
            break
        ids.update(str(c["call_id"]) for c in chunk)
        if len(chunk) < 1000:
            break
        offset += len(chunk)
    return ids


def push(url, key, rows, size=200, table=TABLE, on_conflict="call_id"):
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    # PostgREST refuses a batch that upserts the same key twice - "ON CONFLICT
    # DO UPDATE command cannot affect row a second time" - so duplicates are
    # collapsed before they are sent rather than after a 500.
    keyed, seen = [], set()
    for r in rows:
        k = tuple(r.get(c) for c in on_conflict.split(","))
        if k in seen:
            continue
        seen.add(k)
        keyed.append(r)
    if len(keyed) != len(rows):
        print(f"    {len(rows) - len(keyed)} duplicate keys collapsed")
    rows = keyed

    done = 0
    for start in range(0, len(rows), size):
        chunk = rows[start:start + size]
        # PostgREST rejects the whole batch unless every object carries the
        # same keys, so the rows are squared off before they go
        keys = sorted({k for r in chunk for k in r})
        squared = [{k: r.get(k) for k in keys} for r in chunk]
        r = requests.post(endpoint, headers=headers,
                          params={"on_conflict": on_conflict},
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
    ap.add_argument("--agents", action="store_true",
                    help="list the Purple Dots agents Raya can see, and exit")
    ap.add_argument("--agent", action="append", metavar="ID",
                    help="load only this agent; repeatable. Default: every "
                         "agent in AGENTS except the test bots")
    ap.add_argument("--batch", action="append", metavar="ID",
                    help="load only calls from this batch; repeatable. "
                         "Batch-less calls are excluded when this is set.")
    ap.add_argument("--limit", type=int, help="stop after N calls (smoke test)")
    args = ap.parse_args()

    url, key, api_key = env(need_supabase=not (args.agents or args.dry_run))
    if args.agents:
        list_agents(api_key)
        return

    # Batches first. A batched call carries a contact record, and that is the
    # only place section A of the sheet lives - contact_name, contact_reference,
    # the referring support centre. It also reports status as "Unanswered"
    # where the per-call endpoint says "Failure" for the same call, and the
    # master sheet uses the contact's wording.
    # --agent overrides the map, and deliberately ignores EXCLUDED: asking
    # for a bot by id is explicit enough that silently dropping it would
    # be the surprising behaviour.
    if args.agent:
        targets = {a: AGENTS.get(a, a) for a in args.agent}
        unknown = [a for a in args.agent if a not in AGENTS]
        if unknown:
            print(f"  not in AGENTS, loading anyway: {', '.join(unknown)}")
    else:
        targets = {a: n for a, n in AGENTS.items() if a not in EXCLUDED}
    print(f"Agents: {', '.join(targets.values())}")
    print()

    print("Walking batches...")
    contacts = {}          # call uuid -> (contact, batch)
    for aid, name in targets.items():
        for b in fetch_all_batches(api_key, aid):
            try:
                cs = fetch_all_contacts(api_key, b["id"])
            except Exception as exc:
                print(f"  batch {b.get('id')}: {exc}")
                continue
            print(f"  {len(cs):>5} contacts  batch {b.get('id')}  {b.get('name')!r}")
            for c in cs:
                for call in (c.get("calls") or []):
                    if isinstance(call, dict) and call.get("uuid"):
                        contacts[str(call["uuid"])] = (c, b)
    print(f"  {len(contacts)} calls came through a batch")

    print()
    print("Listing calls per agent...")
    found = []
    for aid, name in targets.items():
        try:
            calls = fetch_all_calls(api_key, aid)
        except Exception as exc:
            print(f"  {name}: {exc}")
            continue
        print(f"  {len(calls):>5}  {name}")
        for c in calls:
            c["_agent_id"], c["_agent_name"] = aid, name
        found.extend(calls)
    print(f"  {len(found)} calls total")
    if not found:
        raise SystemExit(
            "No calls found. This Raya key may not see the Purple Dots "
            "account - run --agents, and see README.")

    # --batch narrows to specific batches. Applied here, before the
    # already-loaded check, so a scoped run cannot quietly widen: without it a
    # re-run puts back every call that was deliberately deleted.
    if args.batch:
        wanted = {str(b) for b in args.batch}
        before = len(found)
        found = [c for c in found
                 if str((contacts.get(str(c.get("uuid"))) or (None, {}))[1]
                        .get("id", "")) in wanted]
        print(f"  --batch {', '.join(sorted(wanted))}: "
              f"{len(found)} of {before} calls kept")
        if not found:
            raise SystemExit("no calls in those batches - check the batch ids "
                             "against the 'Walking batches' list above.")

    if key:
        seen = existing_ids(url, key)
        fresh = [c for c in found if str(c.get("uuid")) not in seen]
        print(f"  {len(found) - len(fresh)} already loaded, {len(fresh)} new")
    else:
        # dry run with no Supabase configured yet: build everything anyway
        seen, fresh = set(), found
        print(f"  no Supabase configured - treating all {len(fresh)} as new")
    if args.limit:
        fresh = fresh[:args.limit]
        print(f"  --limit: keeping {len(fresh)}")

    print("\nFetching call detail (call_output and transcript)...")
    rows, conns, thin = [], [], 0
    for n, call in enumerate(fresh, start=1):
        try:
            detail = fetch_call_detail(api_key, call["uuid"]) or {}
        except Exception:
            detail = {}
        if not detail:
            detail = call
        detail.setdefault("uuid", call.get("uuid"))
        detail.setdefault("agent_id", call["_agent_id"])
        contact, batch = contacts.get(str(call.get("uuid")), (None, None))
        row = make_row(detail, agent_name=call["_agent_name"],
                       contact=contact, batch=batch)
        conns.extend(make_connections(detail, row["call_id"]))
        if not row.get("call_summary") and not row.get("call_status"):
            thin += 1
        rows.append(row)
        if n % 50 == 0:
            print(f"  {n}/{len(fresh)}")
    print(f"  built {len(rows)} rows ({thin} with no call_output at all)")
    print(f"  {len(conns)} provider connections across "
          f"{len({c['call_id'] for c in conns})} calls")

    if rows:
        print(f"\n  by agent   : "
              f"{dict(collections.Counter(r['agent_name'] for r in rows))}")
        print(f"  by channel : "
              f"{dict(collections.Counter(r['channel'] for r in rows))}")
        print(f"  from a batch: "
              f"{sum(1 for r in rows if r.get('batch_id'))}")
        answered = sum(1 for r in rows if r.get("call_answered"))
        engaged = sum(1 for r in rows if r.get("call_engaged"))
        journey = sum(1 for r in rows if r.get("full_journey_completed"))
        print(f"  answered {answered}, engaged {engaged}, "
              f"completed the journey {journey}")

        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, "purple_dots_rows.csv")
        cols = sorted({k for r in rows for k in r})
        with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                w.writerow([json.dumps(r[c], ensure_ascii=False)
                            if isinstance(r.get(c), (list, dict)) else r.get(c)
                            for c in cols])
        print(f"  wrote {path}")
        if conns:
            cpath = os.path.join(OUT_DIR, "purple_dots_connections.csv")
            ccols = sorted({k for c in conns for k in c})
            with io.open(cpath, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(ccols)
                for c in conns:
                    w.writerow([c.get(k) for k in ccols])
            print(f"  wrote {cpath}")

    if args.dry_run:
        print("\n--dry-run: nothing written to Supabase.")
        return
    if not rows:
        print("\nnothing new to push.")
        return
    print(f"\nPushing {len(rows)} rows to {TABLE}...")
    print(f"done, {push(url, key, rows)} rows")
    if conns:
        # after the calls, never before: the FK points at them
        print(f"\nPushing {len(conns)} connections to {CONN_TABLE}...")
        n = push(url, key, conns, table=CONN_TABLE,
                 on_conflict="call_id,provider_item_id")
        print(f"done, {n} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
