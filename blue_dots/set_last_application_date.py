"""Date each provider posting last received an application.

    python set_last_application_date.py --dry-run
    python set_last_application_date.py

Writes bluedot_items.last_application_date. Run sql/alter_last_application_date.sql
once first.

WHY THIS IS NOT IN THE URGENCY SCORE
It looks like a freshness signal and is not one. A recent application does
not mean the posting is still open - providers do not close postings, so a
13-month-old vacancy keeps collecting applications from people we called
last week. Folding it into the age band would rank the deadest postings as
the freshest. It sits beside the score as a fact, not inside it.

WHERE THE DATE COMES FROM
kkb_applications has no date of its own; loaded_at is when our sync ran, not
when anyone applied. The date is the call's: application -> call_id ->
kkb_mastersheet.campaign_date. A posting with no applications stays NULL,
which is most of them.

EVERY ATTEMPT COUNTS, NOT JUST THE SUCCESSFUL ONES
A failed apply is still a seeker who heard this job and chose it; the
failure was our API, not their decision. Counting only successes would date
the posting by when our plumbing last worked. It makes a real difference -
111 postings against 255 - because the failures cluster in May and June,
when most of the apply bugs were live. --successful-only reverses it.
"""
import argparse
import collections
import datetime
import os

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--successful-only", action="store_true",
                    help="count only applications whose apply API succeeded. "
                         "The default counts every attempt, because a failed "
                         "apply is still a seeker who chose this job - the "
                         "failure was ours, not theirs")
    args = ap.parse_args()

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    today = datetime.date.today()

    apps = page(url, key, "kkb_applications", "job_id,applied,call_id", order="id")
    dates = {c["call_id"]: c["campaign_date"]
             for c in page(url, key, "kkb_mastersheet", "call_id,campaign_date",
                           order="call_id") if c.get("campaign_date")}

    # Max per job. Dates are ISO, so a string compare is a date compare.
    last, undated = {}, 0
    for a in apps:
        if args.successful_only and a.get("applied") is not True:
            continue
        when = dates.get(a.get("call_id"))
        if not when:
            undated += 1
            continue
        job = str(a.get("job_id") or "")
        if job:
            last[job] = max(last.get(job, ""), when[:10])

    print(f"{len(apps)} applications "
          f"({'successful only' if args.successful_only else 'all attempts'}) "
          f"-> {len(last)} postings with a date")
    if undated:
        print(f"  {undated} applications skipped: their call has no campaign_date")

    postings = page(url, key, "bluedot_items", "instance,item_id,pause_status",
                    item_domain="eq.provider", order="item_id")
    hits = [(p, last[str(p["item_id"])]) for p in postings
            if str(p["item_id"]) in last]

    unpaused = sum(1 for p, _ in hits if p.get("pause_status") == "N")
    print(f"  matched {len(hits)} of {len(postings)} postings "
          f"({unpaused} of them unpaused)")

    gaps = sorted((today - datetime.date.fromisoformat(d)).days for _, d in hits)
    if gaps:
        print(f"  days since: min {gaps[0]}, median {gaps[len(gaps)//2]}, "
              f"max {gaps[-1]}")
        years = collections.Counter(d[:7] for _, d in hits)
        print("\n  last application by month")
        for month in sorted(years):
            print(f"    {month}  {years[month]:>4}  {'#' * years[month]}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    done = 0
    for posting, when in hits:
        resp = requests.patch(
            f"{url}/rest/v1/bluedot_items", headers=headers, timeout=120,
            params={"instance": f"eq.{posting['instance']}",
                    "item_id": f"eq.{posting['item_id']}"},
            json={"last_application_date": when})
        if resp.status_code >= 400:
            raise SystemExit(f"PATCH failed on {posting['item_id']}: "
                             f"{resp.status_code} {resp.text[:200]}")
        done += 1
    print(f"\n  wrote {done} dates")

    head = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}
    resp = requests.get(f"{url}/rest/v1/bluedot_items", headers=head, timeout=120,
                        params={"select": "item_id", "item_domain": "eq.provider",
                                "last_application_date": "not.is.null", "limit": 1})
    total = resp.headers.get("content-range", "0-0/0").split("/")[-1]
    print(f"  verify: {total} postings now carry a date")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
