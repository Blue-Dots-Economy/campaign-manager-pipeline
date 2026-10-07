"""Loads Purple Dots calls from Raya into purple_dots_calls.

Writes to the PURPLE DOTS Supabase project only. The URL and key come from
this folder's own .env, and nothing here imports from the Blue Dots pipeline -
see README for why that separation is physical rather than just a convention.

ONLY CALLS THAT CAME THROUGH A BATCH ARE LOADED.
This walks /api/batch/{id}/contacts, so a call with no batch is not merely
filtered out - it is never fetched. That matters: 933 batch-less pilot and
demo calls were deleted from this table on purpose on 30 Sept 2026, and an
earlier version of this script walked /api/call?agent_id= instead, which
returns every call an agent made, batch or not. It then dropped the unwanted
ones in memory, so the only thing standing between a normal run and
restoring all 933 was remembering to pass --batch. Now there is nothing to
remember. (That older shape made sense when written - the Raya key showed
zero Purple Dots batches, so there was nothing else to walk. There are four
now.)

--batch still narrows to specific batches, and now also skips fetching the
others, which is the difference between four API calls and forty.

    python load_purple.py --dry-run            # builds rows, touches nothing
    python load_purple.py --dry-run --limit 20
    python load_purple.py                      # pushes every batch
    python load_purple.py --batch 3031         # one batch only
    python load_purple.py --agent <id>         # one agent only
    python load_purple.py --batches            # what exists, and what is loaded
    python load_purple.py --agents             # just list what is out there
"""
import argparse
import collections
import csv
import datetime
import io
import json
import os

from dotenv import load_dotenv

# fetch_all_calls returns batch-less calls, so it is reachable ONLY from
# the --inbound path, where batch-less is what an inbound call is. The
# normal outbound run never calls it.
from raya_client import (fetch_all_agents, fetch_all_batches,
                         fetch_all_calls, fetch_all_contacts,
                         fetch_call_detail, fetch_calls, is_inbound)
import db
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
def _env_set(name, default=""):
    """A comma or space separated list from the environment, or the default.

    Values that differ per deployment - which bots, which numbers - read from
    here so a second environment needs a different .env rather than a
    different build.
    """
    raw = os.getenv(name) or default
    return {x.strip() for x in raw.replace(",", " ").split() if x.strip()}


def _env_map(name, default):
    """id:label pairs from the environment, or the default dict."""
    raw = os.getenv(name)
    if not raw:
        return dict(default)
    out = {}
    for part in raw.replace(",", " ").split():
        agent_id, _, label = part.partition(":")
        if agent_id.strip():
            out[agent_id.strip()] = label.strip() or agent_id.strip()
    return out


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

# Bots that answer calls. Kept apart from AGENTS because the two are fetched
# completely differently: an outbound call belongs to a batch, an inbound one
# cannot, so --inbound walks /api/call?agent_id= instead.
#
# ONE BOT, confirmed by the sheet owner on 7 Oct 2026. 'Testing Agent- Purple
# Dots' (1e8faf6a) also answers calls - 225 of them - and is deliberately NOT
# here. It is what its name says. Its configuration settles it rather than
# the name doing so: it has no output_instructions and an empty tool list, so
# its calls produce a transcript and nothing else, while this bot has 8,902
# characters of output instructions, 18,340 of tools, and has already
# recorded 43 provider connections in 26 calls.
#
# Two numbers are involved, which is the thing to check if this ever looks
# wrong: 918044484499 reaches the testing bot, 917946350861 reaches this one.
INBOUND_AGENTS = _env_map("PD_INBOUND_AGENTS", {
    "a1567240-052e-4ec0-be49-be2ca2d58ea6": "Purple-Dots-Inbound-061020261110",
})

# Numbers the team rang the inbound bots from while testing. Their calls are
# real in Raya and must not count as beneficiary traffic, so their rows are
# written with test_flag = true rather than dropped - the same decision the
# Blue Dots pipeline made, and for the same reason: a deleted row makes our
# count disagree with Raya's forever, and a flag set by hand in Supabase is
# overwritten by the next upsert.
#
# Given by the sheet owner on 7 Oct 2026. Everything else counts as a real
# caller - including 7946350287, which made 67 calls in two days and looks
# like testing but was not named, so it is not guessed at.
#
# Extend with PD_TEST_PHONES in .env rather than editing this; the set below
# is the floor, not the whole list.
TEST_PHONES: set[str] = _env_set("PD_TEST_PHONES", "8065295804")


def is_test_caller(call):
    """True if this inbound call came from a known team number.

    Compares on digits only. Raya returns the same number as '8065295804'
    from one bot and '+918065295804' from another, and a string compare
    would quietly flag neither.
    """
    known = TEST_PHONES
    if not known:
        return False
    digits = "".join(ch for ch in str(call.get("caller_no") or "")
                     if ch.isdigit())[-10:]
    return bool(digits) and digits in {
        "".join(ch for ch in p if ch.isdigit())[-10:] for p in known}


def env(need_db=True):
    """--agents only reads from Raya, so it should not need a database."""
    api = os.getenv("RAYA_API_KEY")
    required = [("RAYA_API_KEY", api)]
    if need_db:
        required.append(("DATABASE_URL", os.getenv("DATABASE_URL")))
    missing = [n for n, v in required if not v]
    if missing:
        # Two ways to run this, and telling a container user to create a .env
        # sends them looking for a file that is deliberately not in the image.
        in_docker = os.path.exists("/.dockerenv")
        how = ("pass them in: docker run --env-file .env ..."
               if in_docker else
               "copy .env.example to .env and fill it in.")
        raise SystemExit(f"missing: {', '.join(missing)}\n{how}")
    # A wrong-database write is the failure this whole folder exists to
    # prevent. The old guard compared SUPABASE_URL against the Blue Dots
    # project id; with Postgres there is no equivalent to check against, so
    # the separation now rests entirely on DATABASE_URL pointing somewhere
    # Blue Dots does not use. Blue Dots talks PostgREST to its own Supabase
    # project and reads no DATABASE_URL at all, so the two cannot collide by
    # configuration alone - but there is no longer a check that says so.
    return api


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


def check(api_key):
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

    # Postgres or Supabase, depending on DATABASE_URL. db.describe() names
    # which, so a run can never leave you guessing where the rows went.
    print("Database")
    ok("backend", True, db.describe())
    if os.getenv("DATABASE_URL"):
        for table in (TABLE, CONN_TABLE):
            try:
                n = db.table_count(table)
                ok(table, n is not None,
                   f"{n} rows" if n is not None
                   else "does not exist - run sql/create_purple_dots.sql")
            except Exception as exc:
                ok(table, False, str(exc).strip().splitlines()[0][:60])
        try:
            good, detail = db.can_write(TABLE)
            ok("write permission", good, detail)
        except Exception as exc:
            ok("write permission", False, str(exc).strip().splitlines()[0][:60])

    print()
    print("all checks passed" if not bad else f"{len(bad)} FAILED: {', '.join(bad)}")
    return len(bad)


def list_batches(api_key):
    """Every batch, with whether it has already been loaded.

    Without this the only record of which batches are real lives in a comment
    on AGENTS, which nobody running the container ever sees. So the tool
    answers the question instead of the operator having to know.

    THE COLUMN IS 'answered', NOT 'dialled'
    Raya's completed_contacts counts people who PICKED UP, not people who
    were called. Batch 3155 shows 646 of 1098 and every one of those 1098
    was dialled - 452 of them simply did not answer. Calling it 'dialled'
    cost an afternoon: a batch reading 646/1098 looks half-finished, and
    it is not.

    What it is good for is spotting a trial. A batch nobody answered may
    still be real; a batch with one contact in it is a smoke test. Read it
    alongside 'total'.
    """
    loaded = collections.Counter()
    try:
        for value in db.column_values(TABLE, "batch_id"):
            loaded[str(value)] += 1
    except Exception:
        # A missing table here is not fatal - the listing still shows what
        # Raya has, with 'in db' simply reading zero.
        pass

    print(f"{'batch':<8}{'answered':>9}{'total':>7}{'in db':>8}  "
          f"{'created':<12}name")
    seen, listed = set(), []
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
            # completed_contacts is how many ANSWERED, not how many were
            # called. A batch can have every contact dialled and still read
            # a fraction here.
            answered = b.get("completed_contacts") or 0
            have = loaded.get(bid, 0)
            if not answered and not have:
                note = "   nobody answered - nothing to load"
            elif have:
                note = ""
            else:
                note = "   <-- NOT LOADED"
            print(f"{bid:<8}{answered:>9}{b.get('total_contacts') or 0:>7}"
                  f"{have:>8}  {str(b.get('created_at'))[:10]:<12}"
                  f"{str(b.get('name'))[:30]!r}{note}")
            listed.append({"id": bid, "answered": answered, "loaded": have,
                           "name": str(b.get("name"))})
    orphan = sum(n for b, n in loaded.items() if b not in seen and b != "None")
    if orphan:
        print(f"\n  {orphan} loaded rows belong to a batch this key cannot see")
    return listed


def _ids(name):
    """A comma or space separated batch list from the environment."""
    raw = os.getenv(name, "")
    return {x.strip() for x in raw.replace(",", " ").split() if x.strip()}


def classify(listed):
    """Decide what to load and what to flag. Returns (ids, flagged).

    Every batch anyone answered is loaded. PD_TEST_BATCHES only decides
    whether its rows carry test_flag = true.

        on PD_TEST_BATCHES  ->  loaded, flagged
        anything else       ->  loaded clean

    ONE LIST, NOT TWO
    An earlier version also kept PD_REAL_BATCHES and treated anything in
    neither as suspect. It was safer on paper and wrong in practice: every
    new campaign needed a config edit before its data counted, which is a
    step that gets forgotten exactly when things are busy. A new campaign
    now just works.

    The cost is that a new TEST batch loads until someone adds it to the
    list. That is the right way round - test batches are rare, small and
    known about in advance, while campaigns are the thing the pipeline
    exists for.

    FLAGGED, NOT SKIPPED
    A batch wrongly listed here would otherwise go missing silently, and
    nobody notices absent calls for months. Flagged is recoverable in one
    UPDATE and the rows stay countable against Raya. Reporting reads
    test_flag, so flagged traffic stays out of the numbers either way.

    THE LIST IS SET ONCE
    PD_TEST_BATCHES lives in .env, not here, and is touched only when a new
    test batch appears - which is rare. A new CAMPAIGN needs no edit at all:
    it loads clean and counts immediately.

    Inbound calls from a team number are flagged the same way, but per call
    rather than per batch, in main().
    """
    test = _ids("PD_TEST_BATCHES")

    load, flagged = [], set()
    for b in listed:
        if not b["answered"]:
            continue          # nobody picked up - no call_output to load
        # Deliberately NOT skipping batches that already have rows. A batch
        # is not finished the moment its first call lands: an old one gains
        # calls when Raya retries an unanswered contact, which is how three
        # calls in batch 3018 sat unloaded for a week. Skipping on "has any
        # rows" meant those later calls could never arrive.
        #
        # Nothing is re-fetched as a result. existing_ids() drops every call
        # already stored before any detail request is made, so including a
        # loaded batch costs one contacts listing and nothing else.
        load.append(b["id"])
        if b["id"] in test:
            flagged.add(b["id"])

    if not load:
        print("\nNothing to load.")
        return [], set()

    print()
    for b in listed:
        if b["id"] not in load:
            continue
        mark = "load + test_flag" if b["id"] in flagged else "load"
        print(f"  {b['id']:<8}{b['answered']:>6} answered {b['loaded']:>6} "
              f"in db   {b['name'][:28]:<30}{mark}")

    if flagged:
        print(f"\n  {len(flagged)} batch(es) in PD_TEST_BATCHES carry "
              f"test_flag = true, so reporting ignores them.")

    return load, flagged


def existing_ids():
    """Every call_id already stored, so those calls are never fetched again."""
    return {str(v) for v in db.column_values(TABLE, "call_id")}


def push(rows, size=200, table=TABLE, on_conflict="call_id"):
    """Upsert. db.upsert collapses duplicate keys and squares the rows off.

    Both are needed on either backend for the same reason: one statement
    cannot update a row it just inserted (Postgres 21000, and PostgREST's
    "ON CONFLICT DO UPDATE command cannot affect row a second time"), and
    every row in one statement must carry the same columns.
    """
    done = db.upsert(table, rows, on_conflict, chunk=size)
    if done != len(rows):
        print(f"    {len(rows) - done} duplicate keys collapsed")
    print(f"    {done}/{len(rows)}")
    return done


def batch_walk(args, api_key):
    """Every call that came through a batch, plus its contact record.

    Returns (calls, contacts) where contacts maps a call uuid to its
    (contact, batch) pair. A call that belongs to no batch is not
    reachable from here at all, which is the point - see the module
    docstring.
    """
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

    wanted = {str(b) for b in args.batch} if args.batch else None

    print("Walking batches...")
    contacts = {}          # call uuid -> (contact, batch)
    found = []             # the calls themselves, in batch order
    for aid, name in targets.items():
        for b in fetch_all_batches(api_key, aid):
            if wanted is not None and str(b.get("id")) not in wanted:
                continue
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
                        call["_agent_id"], call["_agent_name"] = aid, name
                        found.append(call)
    print(f"  {len(found)} calls came through a batch")
    if wanted:
        missing = wanted - {str(b["id"]) for _, b in contacts.values()}
        if missing:
            print(f"  no such batch for these agents: {', '.join(sorted(missing))}")
    return found, contacts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--agents", action="store_true",
                    help="list the Purple Dots agents Raya can see, and exit")
    ap.add_argument("--batches", action="store_true",
                    help="list every batch, how many answered and whether it is "
                         "already loaded, then exit. Start here.")
    ap.add_argument("--check", action="store_true",
                    help="verify every connection and table, then exit. "
                         "Non-zero exit if anything fails.")
    ap.add_argument("--agent", action="append", metavar="ID",
                    help="load only this agent; repeatable. Default: every "
                         "agent in AGENTS except the test bots")
    ap.add_argument("--batch", action="append", metavar="ID",
                    help="load only calls from this batch; repeatable.")
    ap.add_argument("--inbound", action="store_true",
                    help="load calls people made TO the bots instead of "
                         "calls the bots made out. These belong to no batch, "
                         "so it is a separate run - never mixed into one.")
    ap.add_argument("--limit", type=int, help="stop after N calls (smoke test)")
    ap.add_argument("--refresh-days", type=int, default=0, metavar="N",
                    help="also re-fetch calls from the last N days that are "
                         "already stored. A call captured while it was still "
                         "running keeps whatever call_output existed at that "
                         "moment, and the per-call skip means it is never "
                         "looked at again; this is how it gets corrected.")
    ap.add_argument("--csv", action="store_true",
                    help="also write the rows to data/ for inspection. Off by "
                         "default: the pipeline's job is to land data in "
                         "Supabase, not to leave copies on disk.")
    args = ap.parse_args()

    # --check reports on missing credentials rather than exiting on them,
    # which is the whole point of a preflight.
    if args.check:
        raise SystemExit(check(os.getenv("RAYA_API_KEY")))

    api_key = env(need_db=not args.agents)
    if args.agents:
        list_agents(api_key)
        return
    if args.batches:
        list_batches(api_key)
        return

    # No --batch: list what exists, then load it all, flagging whatever is
    # named in PD_TEST_BATCHES. Walking only batches removed the 933
    # batch-less calls, but a test BATCH is still a batch, and nothing in the
    # data separates one from a real campaign - 'purplec' had one contact,
    # and batch 2441 had nine and is real. So the list is the judgement, and
    # it is kept in .env rather than here.
    # Empty unless classify() fills it. --batch and --inbound both bypass
    # that branch, and an explicit --batch 2992 is taken at face value: if
    # someone names a test batch by id they mean to load it as it is.
    quarantined = set()
    if not args.batch and not args.inbound:
        args.batch, quarantined = classify(list_batches(api_key))
        if not args.batch:
            return

    if args.inbound:
        targets = dict(INBOUND_AGENTS)
        if args.agent:
            targets = {a: INBOUND_AGENTS.get(a, a) for a in args.agent}
        print(f"Inbound agents: {', '.join(targets.values())}")
        print()
        print("Listing calls...")
        contacts, found = {}, []
        for aid, name in targets.items():
            calls = fetch_all_calls(api_key, aid)
            # The direction test is the whole point: these bots carry both.
            # 'Testing Agent' holds 223 inbound calls and 4 outbound ones, and
            # the outbound ones are test traffic we already exclude.
            inbound = [c for c in calls if is_inbound(c)]
            print(f"  {len(inbound):>5} inbound of {len(calls):>5}  {name}")
            for c in inbound:
                c["_agent_id"], c["_agent_name"] = aid, name
            found.extend(inbound)
        print(f"  {len(found)} inbound calls")
        if not found:
            # Not an error. A day on which nobody rang the bot is a normal
            # day, and a scheduled run that exits non-zero for it would page
            # somebody every quiet night until they stopped believing it.
            print("  nobody called in - nothing to do.")
            return 0
    else:
        found, contacts = batch_walk(args, api_key)
    if not found:
        raise SystemExit(
            "No calls found in any batch. Either this Raya key cannot see the "
            "Purple Dots account - run --agents - or the batch ids given do "
            "not belong to these agents. See --batches for what exists.")

    # The call-level check, and the reason a re-run is cheap: every uuid
    # already stored is dropped here, before the one-request-per-call detail
    # fetch below. It is also what lets a batch be re-walked every time -
    # Raya keeps adding retry calls to batches long after they look finished.
    seen = existing_ids()
    fresh = [c for c in found if str(c.get("uuid")) not in seen]
    print(f"  {len(found) - len(fresh)} already loaded, {len(fresh)} new")

    if args.refresh_days:
        # Upsert makes re-fetching harmless: the row is overwritten with
        # whatever Raya says now, which is the point.
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=args.refresh_days)).isoformat()
        again = [c for c in found
                 if str(c.get("uuid")) in seen
                 and str(c.get("call_start_time") or "") >= cutoff]
        if again:
            print(f"  --refresh-days {args.refresh_days}: re-fetching "
                  f"{len(again)} stored call(s)")
            fresh = again + fresh
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
        # Set here rather than in make_row: whose numbers are the team's, and
        # which batches are trials, is loading policy that changes when
        # someone joins or a campaign runs. The transform stays a pure
        # function of what Raya returned.
        if args.inbound and is_test_caller(detail):
            row["test_flag"] = True
        elif str(row.get("batch_id")) in quarantined:
            row["test_flag"] = True
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
    print(f"done, {push(rows)} rows")
    if conns:
        # after the calls, never before: the FK points at them
        print(f"\nPushing {len(conns)} connections to {CONN_TABLE}...")
        n = push(conns, table=CONN_TABLE,
                 on_conflict="call_id,provider_item_id")
        print(f"done, {n} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
