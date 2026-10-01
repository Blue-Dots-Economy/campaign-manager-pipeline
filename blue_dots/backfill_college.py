"""Backfills kkb_mastersheet.college_name from Raya's agent_args.

Two HE campaigns cover more than one college under a single campaign_name
("HE_LR and VMLG_Retry", "HE_7sept_vlmg&lr_unanswered" - 2,188 calls), so the
campaign name alone cannot split them. agent_args.college_name is per contact
and is what we told the bot, so it is the reliable source.

Cheap: agent_args is on the batch-contacts endpoint, so no transcript fetch.

    python backfill_college.py --dry-run
    python backfill_college.py
"""
import argparse
import collections
import os

import requests
from dotenv import load_dotenv

from pipeline import AGENT_JFC
from raya_client import fetch_all_batches, fetch_all_contacts
from transform import normalise_college

load_dotenv()

TABLE = "kkb_mastersheet"

# Four HE campaigns have college_name null on EVERY call - 1,211 of them -
# because Raya was never sent agent_args.college_name for those batches, so
# the walk above has nothing to copy. Every other HE campaign is 100% filled.
#
# Because the HE sheet selects on college_name, those 1,211 calls are
# invisible to the whole HE pipeline: 49 real applications never reached the
# tab, and the per-college rates are computed on too small a base.
#
# The campaign names state the college outright, so this maps them by hand.
# Listed one by one rather than pattern-matched on purpose: an inferred
# college written into a column the sheets trust should be a decision someone
# made per campaign, not a regex that quietly catches the next new name. The
# inbound campaigns also have a null college_name and are deliberately absent.
CAMPAIGN_COLLEGE = {
    "16sept_HE_multanimalmodi_retry": "Multanimal Modi College, Modinagar",
    "HigherEducation_MMH_BA_Day1": "MMH College, Ghaziabad",
    "HigherEducation_MMH_Day3": "MMH College, Ghaziabad",
    "HigherEducation_MMH_BCom_Day1": "MMH College, Ghaziabad",
}


def page(url, key, select, extra=None):
    endpoint = f"{url.rstrip('/')}/rest/v1/{TABLE}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        params = {"select": select, "order": "call_id", "offset": offset,
                  "limit": 1000}
        if extra:
            params.update(extra)
        r = requests.get(endpoint, headers=headers, params=params, timeout=300)
        if r.status_code >= 400:
            raise SystemExit(f"{TABLE}: {r.status_code} {r.text[:300]}")
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        offset += len(chunk)
    return rows


def from_campaign(url, key, dry_run):
    """Fill college_name from the campaign name, for the four that need it."""
    rows = page(url, key, "call_id,campaign_name",
                extra={"college_name": "is.null",
                       "campaign_name": f"in.({','.join(CAMPAIGN_COLLEGE)})"})
    by_college = collections.defaultdict(list)
    for r in rows:
        by_college[CAMPAIGN_COLLEGE[r["campaign_name"]]].append(str(r["call_id"]))
    print(f"  {len(rows)} calls with no college_name in those campaigns")
    for college, ids in sorted(by_college.items(), key=lambda kv: -len(kv[1])):
        print(f"    {len(ids):>6}  {college}")
    if dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    endpoint = f"{url.rstrip('/')}/rest/v1/{TABLE}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    done = 0
    for college, ids in by_college.items():
        for start in range(0, len(ids), 200):
            chunk = ids[start:start + 200]
            r = requests.patch(endpoint, headers=headers,
                               params={"call_id": f"in.({','.join(chunk)})"},
                               json={"college_name": college}, timeout=300)
            if r.status_code >= 400:
                raise SystemExit(f"  FAILED {college}: {r.status_code} {r.text[:200]}")
            done += len(chunk)
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-campaign", action="store_true",
                    help="skip Raya; fill college_name from the campaign name "
                         "for the four HE campaigns that never carried it")
    args = ap.parse_args()

    api_key = os.getenv("RAYA_API_KEY")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if args.from_campaign:
        print("Filling college_name from the campaign name...")
        n = from_campaign(url, key, args.dry_run)
        if not args.dry_run:
            print(f"\nset college_name on {n} rows")
        return

    print("Walking Raya for agent_args.college_name...")
    found = {}
    for aid in AGENT_JFC:
        for b in fetch_all_batches(api_key, aid):
            try:
                contacts = fetch_all_contacts(api_key, b["id"])
            except Exception as exc:
                print(f"  batch {b.get('id')}: {exc}")
                continue
            for c in contacts:
                cid = c.get("contact_id")
                a = c.get("agent_args")
                if cid is None or not isinstance(a, dict):
                    continue
                college = normalise_college(a.get("college_name"))
                if college:
                    found[str(cid)] = college
    print(f"  {len(found)} contacts carry a college")

    spread = collections.Counter(found.values())
    print("\n  colleges seen:")
    for name, n in spread.most_common(15):
        print(f"    {n:>6}  {name}")

    if args.dry_run:
        print(f"\n--dry-run: would set college_name on up to {len(found)} rows.")
        return

    # group by college so each PATCH carries one value for many call_ids
    by_college = collections.defaultdict(list)
    for cid, college in found.items():
        by_college[college].append(cid)

    endpoint = f"{url.rstrip('/')}/rest/v1/{TABLE}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    done = 0
    for college, ids in sorted(by_college.items(), key=lambda kv: -len(kv[1])):
        for start in range(0, len(ids), 200):
            chunk = ids[start:start + 200]
            r = requests.patch(endpoint, headers=headers,
                               params={"call_id": f"in.({','.join(chunk)})"},
                               json={"college_name": college}, timeout=300)
            if r.status_code >= 400:
                print(f"  FAILED {college}: {r.status_code} {r.text[:200]}")
                break
            done += len(chunk)
        print(f"  {len(ids):>6}  {college}")
    print(f"\nset college_name on {done} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
