"""Exports a Supabase table to CSV.

    python export_table.py kkb_mastersheet
    python export_table.py kkb_mastersheet --with-transcript
    python export_table.py trrain_mastersheet --out data/trrain.csv

call_transcript and recommendations_input are excluded by default - together
they are over 90% of the bytes and neither is readable in a spreadsheet. Pass
--with-transcript when you actually need them.
"""
import argparse
import csv
import io
import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

# Columns excluded by default because they dominate the file size and are
# rarely what anyone opens a CSV for. recommendations_input alone is 88% of a
# kkb_mastersheet export - the full job list we handed the bot, repeated on
# every contact. jobs_recommended keeps the readable summary of the same thing.
HEAVY = ("call_transcript", "recommendations_input")


def fetch_all(url, key, table, columns="*", page_size=1000):
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        r = requests.get(endpoint, headers=headers,
                         params={"select": columns, "order": "call_id",
                                 "offset": offset, "limit": page_size}, timeout=300)
        if r.status_code >= 400:
            raise SystemExit(f"read failed: {r.status_code} {r.text[:300]}")
        page = r.json()
        if not page:
            break
        rows.extend(page)
        if len(rows) % 10000 < page_size:
            print(f"  {len(rows)} rows...", flush=True)
        if len(page) < page_size:
            break
        offset += len(page)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--out", default=None)
    ap.add_argument("--with-transcript", action="store_true",
                    help="keep call_transcript and recommendations_input (much larger file)")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--supabase-key", default=None)
    args = ap.parse_args()

    url = args.supabase_url or os.getenv("SUPABASE_URL")
    key = (args.supabase_key or os.getenv("SUPABASE_SECRET_KEY")
           or os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

    print(f"Reading {args.table}...")
    rows = fetch_all(url, key, args.table)
    if not rows:
        raise SystemExit("no rows")

    cols = list(rows[0].keys())
    if not args.with_transcript:
        dropped = [c for c in cols if c in HEAVY]
        cols = [c for c in cols if c not in HEAVY]
        if dropped:
            print(f"  excluding {', '.join(dropped)} (use --with-transcript to keep)")

    out = args.out or f"data/{args.table}.csv"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with io.open(out, "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for row in rows:
            # lists and dicts need to survive the round trip as JSON, not repr
            wr.writerow({c: (json.dumps(row[c], ensure_ascii=False)
                             if isinstance(row.get(c), (list, dict)) else row.get(c))
                         for c in cols})
    mb = os.path.getsize(out) / 1_000_000
    print(f"\nwrote {out}")
    print(f"  {len(rows)} rows x {len(cols)} columns, {mb:.1f} MB")


if __name__ == "__main__":
    main()
