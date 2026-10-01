"""Fetches every TRRAIN call from Raya and pushes it into trrain_mastersheet.

TRRAIN is the post-application service-offer call — see transform_trrain.py for
why it has its own table rather than sharing kkb_mastersheet.

Upserts on call_id (Raya's contact_id), so re-running overwrites rather than
duplicates. Rows are cached to disk after they are built, so a write failure
never costs the fetch again.

    python push_trrain.py --dry-run
    python push_trrain.py
    python push_trrain.py --from-cache     # push what a previous run built
"""
import argparse
import collections
import json
import os
import time

import requests
from dotenv import load_dotenv

from pipeline import agent_names, env_or_arg, jfc_for_agent
from raya_client import fetch_all_batches, fetch_all_contacts, fetch_call_detail
from supabase_client import push_to_supabase
from transform import first_call
from transform_trrain import make_trrain_row

load_dotenv()

TABLE = "trrain_mastersheet"
# The only TRRAIN bot in Raya as of Sept 2026. A list so a second one (Kannada,
# say) is one line rather than a refactor.
TRRAIN_AGENTS = {
    "cf39a59a-3b24-4842-ba03-4248ec245aa1": "Ghaziabad",   # TRRAIN Hindi
}


def check_table(url, key):
    """Fail in seconds if the table is missing, rather than after the fetch."""
    r = requests.get(f"{url.rstrip('/')}/rest/v1/{TABLE}",
                     headers={"apikey": key, "Authorization": f"Bearer {key}"},
                     params={"select": "call_id", "limit": 1}, timeout=60)
    if r.status_code >= 400:
        raise SystemExit(
            f"{TABLE} is not reachable: {r.status_code} {r.text[:200]}\n"
            f"Run sql/create_trrain.sql in the Supabase SQL editor first."
        )


def build_rows(api_key, agent_id, jfc, agent_name, need_transcripts=True):
    batches = fetch_all_batches(api_key, agent_id)
    if not batches:
        return []

    pairs, uuids = [], set()
    for batch in batches:
        contacts = fetch_all_contacts(api_key, batch["id"])
        pairs.append((batch, contacts))
        for c in contacts:
            u = first_call(c).get("uuid")
            if u:
                uuids.add(u)

    details = {}
    if need_transcripts:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        done = 0
        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(fetch_call_detail, api_key, u): u for u in uuids}
            for f in as_completed(futs):
                u = futs[f]
                try:
                    details[u] = f.result()
                except Exception:
                    details[u] = None
                done += 1
                if done % 250 == 0 or done == len(uuids):
                    print(f"    call details: {done}/{len(uuids)}", flush=True)

    rows = []
    for batch, contacts in pairs:
        for c in contacts:
            if not (c.get("calls") or []):
                continue          # never dialled: no call data to record
            u = first_call(c).get("uuid")
            d = details.get(u) or {}
            rows.append(make_trrain_row(
                batch, c,
                raw_transcript=d.get("call_transcript"),
                recording_url=d.get("call_recording_url"),
                jfc=jfc, agent_id=agent_id, agent_name=agent_name,
            ))
    return rows


def summarise(rows):
    def count(field, value=True):
        return sum(1 for r in rows if r.get(field) == value)
    spread = collections.Counter(r.get("trrain_interest") or "(none)" for r in rows)
    print(f"  rows              : {len(rows)}")
    print(f"  answered          : {count('call_answered')}")
    print(f"  offer pitched     : {count('trrain_pitched')}")
    print(f"  interest          : {dict(spread)}")
    print(f"  wrong person      : {sum(1 for r in rows if r.get('right_person') in ('No', 'Proxy'))}")
    print(f"  DO NOT CALL       : {count('do_not_call')}")
    print(f"  callback requested: {sum(1 for r in rows if r.get('callback_requested'))}")
    print(f"  transcripts       : {sum(1 for r in rows if r.get('call_transcript'))}")
    print(f"  recordings        : {sum(1 for r in rows if r.get('call_recording_url'))}")
    promised = count("promised_outcome")
    if promised:
        print(f"  !! promised_outcome=true on {promised} calls - the bot must never promise one")


def main():
    ap = argparse.ArgumentParser(description="Push TRRAIN calls into trrain_mastersheet.")
    ap.add_argument("--agent-id", action="append", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--supabase-key", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-transcripts", action="store_true")
    ap.add_argument("--cache", default="trrain_rows_cache.json")
    ap.add_argument("--from-cache", action="store_true")
    args = ap.parse_args()

    api_key = env_or_arg("RAYA_API_KEY", args.api_key)
    url = env_or_arg("SUPABASE_URL", args.supabase_url)
    key = env_or_arg("SUPABASE_SECRET_KEY", args.supabase_key) or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not args.dry_run:
        check_table(url, key)

    if args.from_cache:
        rows = json.load(open(args.cache, encoding="utf-8"))
        print(f"Loaded {len(rows)} rows from {args.cache}")
        summarise(rows)
        push_to_supabase(rows, url, key, table=TABLE)
        print(f"pushed {len(rows)} rows")
        return

    names = agent_names(api_key)
    all_rows = []
    for aid in (args.agent_id or list(TRRAIN_AGENTS)):
        jfc = TRRAIN_AGENTS.get(aid) or jfc_for_agent(aid)
        name = names.get(str(aid), aid)
        print(f"\n=== {name} ({aid})  jfc={jfc} ===")
        t0 = time.time()
        rows = build_rows(api_key, aid, jfc, name, need_transcripts=not args.no_transcripts)
        print(f"  built in {time.time() - t0:.0f}s")
        summarise(rows)
        all_rows.extend(rows)
        with open(args.cache, "w", encoding="utf-8") as fh:
            json.dump(all_rows, fh, default=str)

    if args.dry_run:
        print(f"\n--dry-run: {len(all_rows)} rows built, nothing written. Sample:")
        if all_rows:
            for k, v in sorted(all_rows[0].items()):
                if k != "call_transcript" and v not in (None, ""):
                    print(f"    {k:<24} {str(v)[:70]}")
        return

    push_to_supabase(all_rows, url, key, table=TABLE)
    print(f"\npushed {len(all_rows)} rows into {TABLE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
