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
    # The live bots.
    #
    # THREE BATCHES ARE REAL, as of 5 Oct 2026:
    #     2385  Purple Dots_Basti_Day1_19thAug      16 calls
    #     3018  Basti_1_Final                      660 calls
    #     3031  Tmf_Basti_1oct                     148 calls
    # and 2441 (Basti_Day2_25thAug, 6 calls) is real but not yet loaded.
    #
    # Everything else is testing - the 'purplec', 'testpurplec' and
    # '_with_reference' batches, plus 933 calls that came through no batch at
    # all. Those 933 were deleted deliberately on 30 Sept 2026, confirmed by
    # the sheet owner.
    #
    # So run this scoped, or it puts the test data back:
    #     python load_purple.py --batch 3031
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
        # Two ways to run this, and telling a container user to create a .env
        # sends them looking for a file that is deliberately not in the image.
        in_docker = os.path.exists("/.dockerenv")
        how = ("pass them in: docker run --env-file .env ..."
               if in_docker else
               "copy .env.example to .env and fill it in.")
        raise SystemExit(f"missing: {', '.join(missing)}\n{how}")
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


def check(api_key, url, key):
    """Every connection this loader needs, verified before it is needed.

    A run fetches for minutes before it writes anything, so a bad key or a
    missing table otherwise surfaces at the end of a long wait - or worse,
    halfway through. This answers in about two seconds.

    Returns the number of failures, so it is usable as an exit code.
    """
    bad = []

    def ok(label, passed, detail=""):
        print(f"  [{'ok ' if passed else 'FAIL'}] {label:<34}{detail}")
        if not passed:
            bad.append(label)
        return passed

    print("Raya")
    ok("RAYA_API_KEY set", bool(api_key), f"{str(api_key)[:9]}..." if api_key else "missing")
    agents = []
    if api_key:
        try:
            agents = fetch_all_agents(api_key)
            ok("api reachable", True, f"{len(agents)} agents visible")
        except Exception as exc:
            ok("api reachable", False, str(exc)[:60])
    seen = {str(a.get("id")) for a in agents}
    want = [a for a in AGENTS if a not in EXCLUDED]
    found = [a for a in want if a in seen]
    ok("Purple Dots agents", len(found) == len(want),
       f"{len(found)} of {len(want)} found"
       + ("" if len(found) == len(want)
          else " - this key may be the wrong account, see README"))

    print("Supabase")
    # The URL is printed, so which project it is can be read off directly -
    # no separate line for it. The real guard is in env(), which refuses to
    # run against Blue Dots rather than merely reporting on it.
    ok("SUPABASE_URL set", bool(url), url or "missing")
    ok("SUPABASE_SECRET_KEY set", bool(key), "***" if key else "missing")

    if url and key:
        h = {"apikey": key, "Authorization": f"Bearer {key}"}
        for table in (TABLE, CONN_TABLE):
            try:
                r = requests.get(f"{url.rstrip('/')}/rest/v1/{table}",
                                 headers={**h, "Prefer": "count=exact"},
                                 params={"select": "call_id", "limit": 1}, timeout=60)
                if r.status_code == 404:
                    ok(table, False, "does not exist - run sql/create_purple_dots.sql")
                elif r.status_code >= 400:
                    ok(table, False, f"{r.status_code} {r.text[:40]}")
                else:
                    n = r.headers.get("content-range", "0-0/0").split("/")[-1]
                    ok(table, True, f"{n} rows")
            except Exception as exc:
                ok(table, False, str(exc)[:60])
        # Writable? A PATCH whose filter matches nothing changes no data but
        # still fails with 403 on a read-only key, which is what we want to
        # learn now rather than after twenty minutes of fetching.
        try:
            r = requests.patch(f"{url.rstrip('/')}/rest/v1/{TABLE}",
                               headers={**h, "Content-Type": "application/json",
                                        "Prefer": "return=minimal"},
                               params={"call_id": "eq.__preflight_no_match__"},
                               json={"test_flag": True}, timeout=60)
            ok("write permission", r.status_code < 400,
               "" if r.status_code < 400 else f"{r.status_code} - key is read-only")
        except Exception as exc:
            ok("write permission", False, str(exc)[:60])

    print()
    print("all checks passed" if not bad else f"{len(bad)} FAILED: {', '.join(bad)}")
    return len(bad)


def list_batches(api_key, url, key):
    """Every batch, with whether it has already been loaded.

    Without this the only record of which batches are real lives in a comment
    on AGENTS, which nobody running the container ever sees - and getting it
    wrong is expensive: an unscoped run restores roughly 950 deliberately
    deleted test rows. So the tool answers the question instead of the
    operator having to know.

    'dialled' is the number to read, not 'loaded'. A batch can hold hundreds
    of contacts and have called none of them.
    """
    loaded = collections.Counter()
    r = requests.get(f"{url.rstrip('/')}/rest/v1/{TABLE}",
                     headers={"apikey": key, "Authorization": f"Bearer {key}"},
                     params={"select": "batch_id", "limit": 10000}, timeout=300)
    if r.status_code < 400:
        for row in r.json():
            if row.get("batch_id"):
                loaded[str(row["batch_id"])] += 1

    print(f"{'batch':<8}{'dialled':>8}{'total':>7}{'in db':>8}  "
          f"{'created':<12}name")
    seen = set()
    for aid, agent in AGENTS.items():
        if aid in EXCLUDED:
            continue
        try:
            batches = fetch_all_batches(api_key, aid)
        except Exception as exc:
            print(f"  {agent}: {exc}")
            continue
        for b in sorted(batches, key=lambda x: str(x.get("created_at") or "")):
            bid = str(b.get("id"))
            seen.add(bid)
            done = b.get("completed_contacts") or 0
            have = loaded.get(bid, 0)
            if not done and not have:
                note = "   never dialled - nothing to load"
            elif have:
                note = ""
            else:
                note = "   <-- NOT LOADED"
            print(f"{bid:<8}{done:>8}{b.get('total_contacts') or 0:>7}"
                  f"{have:>8}  {str(b.get('created_at'))[:10]:<12}"
                  f"{str(b.get('name'))[:30]!r}{note}")
    orphan = sum(n for b, n in loaded.items() if b not in seen and b != "None")
    if orphan:
        print(f"\n  {orphan} loaded rows belong to a batch this key cannot see")
    print("\nLoad one with:  --batch <id>.  Without --batch every call is "
          "loaded, including test data.")


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
    ap.add_argument("--batches", action="store_true",
                    help="list every batch, what it dialled and whether it is "
                         "already loaded, then exit. Start here.")
    ap.add_argument("--check", action="store_true",
                    help="verify every connection and table, then exit. "
                         "Non-zero exit if anything fails.")
    ap.add_argument("--agent", action="append", metavar="ID",
                    help="load only this agent; repeatable. Default: every "
                         "agent in AGENTS except the test bots")
    ap.add_argument("--batch", action="append", metavar="ID",
                    help="load only calls from this batch; repeatable. "
                         "Batch-less calls are excluded when this is set.")
    ap.add_argument("--limit", type=int, help="stop after N calls (smoke test)")
    ap.add_argument("--csv", action="store_true",
                    help="also write the rows to data/ for inspection. Off by "
                         "default: the pipeline's job is to land data in "
                         "Supabase, not to leave copies on disk.")
    args = ap.parse_args()

    # --check reports on missing credentials rather than exiting on them,
    # which is the whole point of a preflight.
    if args.check:
        url = os.getenv("SUPABASE_URL", "").rstrip("/")
        key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        raise SystemExit(check(os.getenv("RAYA_API_KEY"), url, key))

    url, key, api_key = env(need_supabase=not args.agents)
    if args.agents:
        list_agents(api_key)
        return
    if args.batches:
        list_batches(api_key, url, key)
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

        # Opt-in, and off by default. This used to write on every run,
        # including real pushes, which left call data on whatever machine
        # happened to run the loader. The rows carry no names or phone
        # numbers, but disability_category_mapped against a profile id is
        # still health information about a person, and it does not need a
        # second home on a laptop. Supabase is where this data lives; a CSV
        # is for reading once and deleting.
        if args.csv:
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
            print(f"  wrote {path}  - delete it when you are done")
            if conns:
                cpath = os.path.join(OUT_DIR, "purple_dots_connections.csv")
                ccols = sorted({k for c in conns for k in c})
                with io.open(cpath, "w", encoding="utf-8-sig", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(ccols)
                    for c in conns:
                        w.writerow([c.get(k) for k in ccols])
                print(f"  wrote {cpath}  - delete it when you are done")
        else:
            sample = rows[0]
            filled = {k: v for k, v in sample.items() if v not in (None, "", [], {})}
            print(f"  sample row: {len(filled)} of {len(sample)} columns filled")
            print(f"    {dict(list(filled.items())[:6])}")
            print("  (--csv writes the full rows to a file)")

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
