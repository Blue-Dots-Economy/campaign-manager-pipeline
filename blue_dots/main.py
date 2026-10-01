"""Orchestration + CLI: fetch Raya batch/contact data (raya_client), transform
it (transform), and push the resulting rows into Supabase (supabase_client).

All batches under an agent are treated the same — no name-based filtering.

Two modes:
  - Non-interactive: `python main.py --agent-id <uuid>` (pushes every batch
    for that agent).
  - Interactive: `python main.py` with no --agent-id — lists agents, then
    lists that agent's batches showing which are already pushed, and lets
    you pick which of the remaining ones to push.
"""
import argparse
import os

from dotenv import load_dotenv

from pipeline import (AGENT_JFC, agent_names, build_rows_for_batches, env_or_arg,
                      get_batches_with_status, jfc_for_agent, list_agents)
from raya_client import fetch_all_batches
from supabase_client import push_to_supabase

load_dotenv()


def prompt_choice(items: list[str], title: str) -> list[int]:
    """Print a numbered list and let the user pick one, several ("1,3,5"),
    a range ("1-5"), or "all". Returns 0-based indices."""
    print(f"\n{title}")
    for i, label in enumerate(items, start=1):
        print(f"  {i}. {label}")
    raw = input("\nSelect number(s) (comma/range, or 'all'): ").strip().lower()

    if raw == "all":
        return list(range(len(items)))

    indices: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            indices.update(range(int(start) - 1, int(end)))
        else:
            indices.add(int(part) - 1)
    return sorted(i for i in indices if 0 <= i < len(items))


def run_interactive(raya_api_key: str, supabase_url: str, supabase_key: str) -> None:
    agents = list_agents(raya_api_key)
    if not agents:
        print("No agents found.")
        return

    agent_labels = [f"{a.get('name')}  ({a.get('id')})" for a in agents]
    agent_idx = prompt_choice(agent_labels, "Agents:")
    if not agent_idx:
        print("No agent selected. Exiting.")
        return
    agent = agents[agent_idx[0]]
    agent_id = agent.get("id")
    print(f"\nUsing agent: {agent.get('name')} ({agent_id})")

    print("\nFetching batches and checking what's already in Supabase...")
    batches = get_batches_with_status(raya_api_key, agent_id, supabase_url, supabase_key)
    if not batches:
        print("No batches found for this agent.")
        return

    pending = [b for b in batches if not b["already_pushed"]]
    pushed_count = len(batches) - len(pending)
    print(f"Total batches: {len(batches)} | Already pushed: {pushed_count} | Remaining: {len(pending)}")

    if not pending:
        print("Nothing left to push for this agent.")
        return

    pending_labels = [
        f"{b.get('name')}  (id={b.get('id')}, contacts={b.get('total_contacts')}, created={b.get('created_at')})"
        for b in pending
    ]
    chosen_idx = prompt_choice(pending_labels, "Remaining (not yet pushed) batches:")
    if not chosen_idx:
        print("No batches selected. Exiting.")
        return
    chosen_batches = [pending[i] for i in chosen_idx]

    rows = build_rows_for_batches(raya_api_key, chosen_batches)
    print(f"\nRows prepared: {len(rows)}")
    push_to_supabase(rows, supabase_url, supabase_key)


def run_non_interactive(raya_api_key: str, agent_id: str, supabase_url: str,
                        supabase_key: str, dry_run: bool = False) -> None:
    batches = fetch_all_batches(raya_api_key, agent_id)
    rows = build_rows_for_batches(raya_api_key, batches)
    print(f"Matched batches: {len(batches)}")
    print(f"Rows prepared: {len(rows)}")
    if dry_run:
        print("--dry-run: nothing written.")
        return
    push_to_supabase(rows, supabase_url, supabase_key)


def check_columns(supabase_url: str, supabase_key: str, table: str = "kkb_mastersheet") -> None:
    """Fail now, not after twenty minutes of fetching.

    PostgREST rejects an entire write if it carries one unknown column, so a
    field added to transform.py without its ALTER TABLE kills the push at the
    first chunk — after every transcript has already been fetched. Comparing
    the row shape against the live table up front turns that into a two-second
    error with the exact SQL to run.
    """
    import requests
    from transform import make_row

    probe = make_row({"id": 0}, {"contact_id": 0, "calls": []})
    resp = requests.get(
        f"{supabase_url.rstrip('/')}/rest/v1/{table}",
        headers={"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"},
        params={"select": "*", "limit": 1}, timeout=60,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"cannot read {table}: {resp.status_code} {resp.text[:200]}")
    rows = resp.json()
    if not rows:
        return  # empty table tells us nothing about its shape
    missing = sorted(set(probe) - set(rows[0]))
    if missing:
        cols = "\n".join(f"  add column if not exists {c} jsonb," for c in missing)
        raise SystemExit(
            f"{table} is missing {len(missing)} column(s) that transform.py writes: "
            f"{', '.join(missing)}\n\nRun this first (check the types):\n\n"
            f"alter table public.{table}\n{cols.rstrip(',')};\n\nnotify pgrst, 'reload schema';"
        )


def run_batch_ids(raya_api_key: str, batch_ids: list[str], supabase_url: str,
                  supabase_key: str, dry_run: bool = False,
                  campaign_name: str | None = None) -> None:
    """Push an explicit list of batch ids, wherever they live.

    The interactive path is per-agent, which cannot express "these eight
    batches across four agents" — and pushing a whole agent would drag in
    batches that were loaded but never dialled. Batch ids are resolved by
    scanning the agents in AGENT_JFC, since a batch carries its own agent_id.
    """
    check_columns(supabase_url, supabase_key)

    wanted = {str(b) for b in batch_ids}
    found: list[dict] = []
    for aid in AGENT_JFC:
        for batch in fetch_all_batches(raya_api_key, aid):
            if str(batch.get("id")) in wanted:
                found.append(batch)

    missing = wanted - {str(b.get("id")) for b in found}
    if missing:
        raise SystemExit(f"batch id(s) not found under any mapped agent: {sorted(missing)}")

    names = agent_names(raya_api_key)
    print(f"Resolved {len(found)} batches:")
    for b in sorted(found, key=lambda x: str(x.get("created_at"))):
        print(f"  {b['id']:<6} {str(b.get('name'))[:36]:<38}"
              f" {names.get(str(b.get('agent_id')), '?')[:30]:<32}"
              f" jfc={jfc_for_agent(b.get('agent_id'))}")

    applications: list[dict] = []
    rows = build_rows_for_batches(raya_api_key, found, applications=applications)
    print(f"\nRows prepared: {len(rows)}   applications: {len(applications)}")
    if not rows:
        return

    # An explicit name beats both the campaign sheet and Raya's auto-generated
    # batch name ("Quiet Glacier 040"), which carries no meaning.
    if campaign_name:
        for row in rows:
            row["campaign_name"] = campaign_name
        print(f"  campaign_name forced to: {campaign_name!r}")

    blank_jfc = sum(1 for r in rows if not r.get("jfc_campaign"))
    named = sum(1 for r in rows if r.get("campaign_name"))
    print(f"  jfc_campaign set   : {len(rows) - blank_jfc}/{len(rows)}")
    print(f"  campaign_name set  : {named}/{len(rows)}")
    print(f"  intent_score set   : {sum(1 for r in rows if r.get('intent_score') is not None)}/{len(rows)}")
    print(f"  transcripts         : {sum(1 for r in rows if r.get('call_transcript'))}/{len(rows)}")
    print(f"  recordings          : {sum(1 for r in rows if r.get('call_recording_url'))}/{len(rows)}")

    if dry_run:
        print("\n--dry-run: nothing written. Sample row:")
        sample = {k: v for k, v in rows[0].items()
                  if k not in ("call_transcript", "call_summary") and v not in (None, "")}
        for k in sorted(sample):
            print(f"    {k:<26} {str(sample[k])[:70]}")
        return

    push_to_supabase(rows, supabase_url, supabase_key)
    push_applications(applications, supabase_url, supabase_key)


def push_applications(rows, url, key):
    """The kkb_applications children. Written AFTER the parent rows, because
    call_id is a foreign key onto kkb_mastersheet."""
    if not rows:
        return
    import requests
    endpoint = f"{url.rstrip('/')}/rest/v1/kkb_applications"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    seen, unique = set(), []
    for r in rows:
        k = (r["call_id"], r["job_id"], r["applied"])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    done = 0
    for start in range(0, len(unique), 500):
        chunk = unique[start:start + 500]
        resp = requests.post(endpoint, headers=headers,
                             params={"on_conflict": "call_id,job_id,applied"},
                             json=chunk, timeout=300)
        if resp.status_code >= 400:
            print(f"  applications FAILED at {start + 1}: "
                  f"{resp.status_code} {resp.text[:200]}")
            return
        done += len(chunk)
    print(f"  applications written: {done}")


def main():
    parser = argparse.ArgumentParser(description="Fetch Raya batch-contact data and push transformed rows into Supabase.")
    parser.add_argument("--agent-id", default=None, help="Raya agent UUID. Omit to pick interactively.")
    parser.add_argument("--batch-id", action="append", default=None,
                        help="Push one specific batch id; repeatable. Resolved across every mapped agent.")
    parser.add_argument("--dry-run", action="store_true", help="Build and summarise rows, write nothing.")
    parser.add_argument("--campaign-name", default=None,
                        help="Force campaign_name on every pushed row, overriding the sheet and the Raya batch name.")
    parser.add_argument("--api-key", default=None, help="Raya API key; defaults to RAYA_API_KEY")
    parser.add_argument("--supabase-url", default=None, help="Supabase project URL; defaults to SUPABASE_URL")
    parser.add_argument("--supabase-key", default=None, help="Supabase service role key; defaults to SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY")
    args = parser.parse_args()

    raya_api_key = env_or_arg("RAYA_API_KEY", args.api_key)
    supabase_url = env_or_arg("SUPABASE_URL", args.supabase_url)
    supabase_key = env_or_arg("SUPABASE_SECRET_KEY", args.supabase_key)
    if not supabase_key:
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if args.batch_id:
        run_batch_ids(raya_api_key, args.batch_id, supabase_url, supabase_key,
                      args.dry_run, args.campaign_name)
    elif args.agent_id:
        run_non_interactive(raya_api_key, args.agent_id, supabase_url, supabase_key, args.dry_run)
    else:
        run_interactive(raya_api_key, supabase_url, supabase_key)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
