"""One-off maintenance script: recomputes fields on rows already pushed to
Supabase (from before those fields existed / were computed correctly), and
writes them back in bulk via upsert on the call_id unique constraint.
Replaces what used to be two near-identical scripts.

Field groups:
  campaign      - campaign_day, campaign_date. Cheap: no per-call transcript
                  fetch needed, and call_ids are grouped by identical
                  (day, date) to minimize PATCH requests.
  jfc           - jfc_campaign: which Job Facilitation Centre the bot serves
                  (Ghaziabad / Hubli-Dharwad), from pipeline.AGENT_JFC.
                  Cheap: no transcript fetch needed.
  campaign_name - campaign_name, from the callids_to_campaignname
                  sheet (Raya's own batch name is a meaningless auto-generated
                  codename, not the real campaign name). Cheap: no transcript
                  fetch needed.
  intent        - intent_score, intent_score_reasoning, apply_api_success.
                  Slow: fetches each call's transcript (~1 extra API call per
                  contact) so apply_api_success can be read from the actual
                  apply_job tool result.

Usage:
    python backfill_fields.py --agent-id <uuid> [--agent-id <uuid> ...] --fields campaign
    python backfill_fields.py --agent-id <uuid> --fields campaign_name
    python backfill_fields.py --agent-id <uuid> --fields intent
    python backfill_fields.py --agent-id <uuid> --fields campaign_name intent
"""
import argparse
import os
from collections import defaultdict

from dotenv import load_dotenv

from pipeline import build_rows_for_batches, env_or_arg
from raya_client import fetch_all_batches, fetch_all_contacts
from supabase_client import bulk_update_by_call_id, patch_by_call_ids
from transform import make_row

load_dotenv()

FIELD_GROUPS = {
    "campaign": ["campaign_day", "campaign_date"],
    "campaign_name": ["campaign_name"],
    "jfc": ["jfc_campaign"],
    "agent": ["agent_id", "agent_name"],
    "intent": ["intent_score", "intent_score_reasoning", "apply_api_success"],
}
NEEDS_TRANSCRIPT = {"intent"}


def backfill_campaign_only(api_key: str, agent_id: str, supabase_url: str, supabase_key: str) -> None:
    """Fast path: campaign_day/campaign_date don't need transcripts, so skip
    that fetch and group call_ids by identical (day, date) to cut PATCH calls."""
    batches = fetch_all_batches(api_key, agent_id)
    print(f"Agent {agent_id}: {len(batches)} batches")

    groups: dict[tuple, list[str]] = defaultdict(list)
    total_contacts = 0
    skipped = 0
    for batch in batches:
        for contact in fetch_all_contacts(api_key, batch["id"]):
            total_contacts += 1
            row = make_row(batch, contact)
            call_id = row.get("call_id")
            if not call_id or row.get("campaign_date") is None:
                skipped += 1
                continue
            groups[(row["campaign_day"], row["campaign_date"])].append(call_id)

    print(f"  Contacts processed: {total_contacts} | skipped (no call date): {skipped}")
    print(f"  Distinct (campaign_day, campaign_date) groups to patch: {len(groups)}")
    for (day, cdate), call_ids in groups.items():
        patch_by_call_ids(supabase_url, supabase_key, call_ids, {"campaign_day": day, "campaign_date": cdate})
        print(f"  Patched {len(call_ids)} rows -> campaign_day={day}, campaign_date={cdate}")


def backfill_with_fields(
    api_key: str,
    agent_id: str,
    supabase_url: str,
    supabase_key: str,
    columns: list[str],
    need_transcripts: bool,
) -> None:
    """General path: recomputes full rows and bulk-updates the requested
    columns by call_id. Only fetches transcripts (one extra API call per
    contact) when a requested field actually needs them."""
    batches = fetch_all_batches(api_key, agent_id)
    print(f"Agent {agent_id}: {len(batches)} batches")

    def progress(done, total):
        if done % 200 == 0 or done == total:
            print(f"  transcripts fetched: {done}/{total}")

    rows = build_rows_for_batches(api_key, batches, progress=progress, need_transcripts=need_transcripts)
    print(f"  Rows recomputed: {len(rows)}")

    updates_by_call_id = {}
    skipped = 0
    for row in rows:
        call_id = row.get("call_id")
        if not call_id:
            skipped += 1
            continue
        updates_by_call_id[call_id] = {col: row.get(col) for col in columns}

    print(f"  Distinct call_ids to update: {len(updates_by_call_id)} (skipped {skipped} with no call_id)")

    payload = [{"call_id": cid, **cols} for cid, cols in updates_by_call_id.items()]
    sent = bulk_update_by_call_id(payload, supabase_url, supabase_key)
    print(f"  Rows updated: {sent}")


def main():
    parser = argparse.ArgumentParser(description="Backfill computed fields on already-pushed rows.")
    parser.add_argument("--agent-id", action="append", required=True, help="Agent UUID (repeat for multiple)")
    parser.add_argument(
        "--fields", nargs="+", choices=list(FIELD_GROUPS), default=["campaign"],
        help="Which field group(s) to backfill (default: campaign).",
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--supabase-url", default=None)
    parser.add_argument("--supabase-key", default=None)
    args = parser.parse_args()

    raya_api_key = env_or_arg("RAYA_API_KEY", args.api_key)
    supabase_url = env_or_arg("SUPABASE_URL", args.supabase_url)
    supabase_key = env_or_arg("SUPABASE_SECRET_KEY", args.supabase_key)
    if not supabase_key:
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    only_campaign = args.fields == ["campaign"]
    columns = [col for group in args.fields for col in FIELD_GROUPS[group]]
    need_transcripts = any(group in NEEDS_TRANSCRIPT for group in args.fields)

    for agent_id in args.agent_id:
        if only_campaign:
            backfill_campaign_only(raya_api_key, agent_id, supabase_url, supabase_key)
        else:
            backfill_with_fields(raya_api_key, agent_id, supabase_url, supabase_key, columns, need_transcripts)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
