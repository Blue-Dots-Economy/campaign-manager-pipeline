"""Fetches every DKB (employer-facing) call from Raya and pushes it into
Supabase `dkb_mastersheet`.

Upserts on call_id, so it is safe to re-run — existing rows are overwritten
with fresh values rather than duplicated.

    python push_dkb.py                # all DKB agents that have data
    python push_dkb.py --agent-id X   # just one
    python push_dkb.py --dry-run
"""
import argparse
import json
import os
import time

from dotenv import load_dotenv

from pipeline import DKB_AGENT_JFC, build_dkb_rows_for_batches, env_or_arg
from raya_client import fetch_all_agents, fetch_all_batches
from supabase_client import push_to_supabase

load_dotenv()

TABLE = "dkb_mastersheet"


def main():
    ap = argparse.ArgumentParser(description="Push DKB calls into dkb_mastersheet.")
    ap.add_argument("--agent-id", action="append", default=None, help="Agent UUID (repeat). Default: all DKB agents with data.")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--supabase-key", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cache", default="dkb_rows_cache.json",
                    help="Where built rows are saved, so a failed write can be retried without re-fetching.")
    ap.add_argument("--from-cache", action="store_true",
                    help="Skip Raya entirely and push the rows saved by a previous run.")
    args = ap.parse_args()

    api_key = env_or_arg("RAYA_API_KEY", args.api_key)
    url = env_or_arg("SUPABASE_URL", args.supabase_url)
    key = env_or_arg("SUPABASE_SECRET_KEY", args.supabase_key) or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if args.from_cache:
        rows = json.load(open(args.cache, encoding="utf-8"))
        print(f"Loaded {len(rows)} rows from {args.cache} — pushing without touching Raya")
        push_to_supabase(rows, url, key, table=TABLE)
        print(f"pushed {len(rows)} rows")
        return

    agent_ids = args.agent_id or list(DKB_AGENT_JFC)
    all_rows: list[dict] = []
    names = {a["id"]: a.get("name") for a in fetch_all_agents(api_key)}

    for aid in agent_ids:
        batches = fetch_all_batches(api_key, aid)
        if not batches:
            print(f"{names.get(aid, aid)}: no batches, skipping")
            continue
        print(f"\n=== {names.get(aid, aid)} ({aid}) — {len(batches)} batches ===")

        def progress(done, total):
            if done % 500 == 0 or done == total:
                print(f"  call details fetched: {done}/{total}")

        t0 = time.time()
        rows = build_dkb_rows_for_batches(api_key, batches, progress=progress)
        print(f"  rows built: {len(rows)} in {time.time() - t0:.0f}s")

        filled = lambda f: sum(1 for r in rows if r.get(f) not in (None, ""))
        print(f"  updated_vacancies={filled('updated_vacancies')}  update_job_status={filled('update_job_status')}"
              f"  new_job_posted={sum(1 for r in rows if r.get('new_job_posted'))}"
              f"  recording_url={filled('call_recording_url')}")

        all_rows.extend(rows)
        # save after every agent, so a write failure never costs the fetch again
        with open(args.cache, "w", encoding="utf-8") as fh:
            json.dump(all_rows, fh, default=str)

        if args.dry_run:
            print("  --dry-run: nothing written")
            continue

        push_to_supabase(rows, url, key, table=TABLE)
        print(f"  pushed {len(rows)} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
