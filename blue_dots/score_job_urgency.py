"""Job urgency score for unpaused provider postings.

    python score_job_urgency.py --dry-run
    python score_job_urgency.py

Scored ONLY where pause_status = 'N'. Paused postings keep NULL, not 0 - a
pause is a decision, the same way call_confidence.py separates "do not call"
from "call last".

The score answers: how badly does this posting need attention? Old with no
applications scores high; already filling scores low.

WHY ONLY THE UNPAUSED SET
Run across all 3,722 postings this score is useless: 3,398 are over 180 days
old and 3,605 have zero applications, so 89% land on exactly 6 and the
ranking collapses. On the 115 unpaused rows the inputs actually vary and the
score spreads across its whole range. The pause filter is not a nicety, it is
what makes the number mean anything.
"""
import argparse
import collections
import datetime
import os

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Age bands. The catalogue is heavily skewed old - median 240 days - so these
# only separate anything within the unpaused set.
AGE_BANDS = ((180, 3, "age>180"), (90, 2, "age 90-180"), (30, 1, "age 30-90"))


def days_since(value, today):
    try:
        return (today - datetime.date.fromisoformat(str(value)[:10])).days
    except (TypeError, ValueError):
        return None


def positions_of(item_state):
    """Vacancies on the posting. Defaults to 1, which is also the median."""
    if not isinstance(item_state, dict):
        return 1
    try:
        return max(int(item_state.get("positions") or 1), 1)
    except (TypeError, ValueError):
        return 1


def urgency(posting, applications, today):
    """Returns (score, reason). Age plus how far from filled it is.

    The three application rules are a TIER, not three additions: zero
    applications already satisfies "fewer than positions", so adding both
    would hand every empty posting a redundant +1. That changes no ordering -
    a constant offset on 97% of rows - but it makes the number mean what the
    rule says.
    """
    score, parts = 0, []

    age = days_since(posting.get("created_at"), today)
    if age is not None:
        for threshold, points, label in AGE_BANDS:
            if age > threshold:
                score += points
                parts.append(f"{label} ({points:+d})")
                break

    count = applications.get(str(posting["item_id"]), 0)
    seats = positions_of(posting.get("item_state"))
    if count == 0:
        score += 2
        parts.append("no applications (+2)")
    elif count < seats:
        score += 1
        parts.append(f"{count} of {seats} filled (+1)")
    else:
        score -= 2
        parts.append(f"{count} applications for {seats} (-2)")

    return score, " | ".join(parts) if parts else "no signal"


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
    ap.add_argument("--count-failed", action="store_true",
                    help="count failed apply attempts as applications received; "
                         "by default only successful applications count")
    args = ap.parse_args()

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    today = datetime.date.today()

    postings = page(url, key, "bluedot_items",
                    "instance,item_id,created_at,item_state,lifecycle_status",
                    item_domain="eq.provider", pause_status="eq.N", order="item_id")
    apps = page(url, key, "kkb_applications", "job_id,applied", order="id")
    applications = collections.Counter(
        str(a["job_id"]) for a in apps
        if args.count_failed or a.get("applied") is True)

    print(f"{len(postings)} unpaused postings, {sum(applications.values())} "
          f"applications counted "
          f"({'all attempts' if args.count_failed else 'successful only'})")
    drafts = sum(1 for p in postings if p.get("lifecycle_status") != "live")
    if drafts:
        print(f"  note: {drafts} of these are not 'live' (never published). "
              f"They are scored because pause_status = 'N'.")

    scored = [(p, *urgency(p, applications, today)) for p in postings]
    spread = collections.Counter(s for _, s, _ in scored)
    print("\n  score spread")
    for value in sorted(spread, reverse=True):
        bar = "#" * spread[value]
        print(f"    {value:>3}  {spread[value]:>4}  {bar}")

    top = sorted(scored, key=lambda x: -x[1])[:5]
    print("\n  most urgent")
    for p, s, why in top:
        state = p.get("item_state") or {}
        role = str(state.get("role") or "")[:28]
        print(f"    {s:>2}  {p['instance']}  {role:<30}{why}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    done = 0
    for posting, score, why in scored:
        resp = requests.patch(
            f"{url}/rest/v1/bluedot_items", headers=headers, timeout=120,
            params={"instance": f"eq.{posting['instance']}",
                    "item_id": f"eq.{posting['item_id']}"},
            json={"job_urgency_score": score, "job_urgency_reason": why})
        if resp.status_code >= 400:
            raise SystemExit(f"PATCH failed on {posting['item_id']}: "
                             f"{resp.status_code} {resp.text[:200]}")
        done += 1
    print(f"\n  wrote {done} scores")

    head = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}
    for label, flt in (("scored", "not.is.null"), ("unscored", "is.null")):
        resp = requests.get(f"{url}/rest/v1/bluedot_items", headers=head, timeout=120,
                            params={"select": "item_id", "item_domain": "eq.provider",
                                    "job_urgency_score": flt, "limit": 1})
        print(f"  verify {label}: "
              f"{resp.headers.get('content-range', '0-0/0').split('/')[-1]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
