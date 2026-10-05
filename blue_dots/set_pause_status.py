"""Sets bluedot_items.pause_status from the Operation Rozgar workbook.

    python set_pause_status.py --dry-run
    python set_pause_status.py

The workbook's two Archive tabs are the pause list; the two Active tabs are
what stays live. Everything is keyed on (instance, item_id) because that is
the primary key - 76 job ids exist under BOTH UP and KA as separate rows, so
matching on item_id alone would pause the wrong region's copy.

Every provider row is set to 'N' first and the archive list to 'Y' after, so
the column is never null and the script is safe to re-run. It writes nothing
else: pause_status is ours, and load_bluedot.py never sends that column, so a
re-sync of the S3 dumps leaves it alone.
"""
import argparse
import collections
import os

import openpyxl
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "operation_rozgar_jobs_final.xlsx")
COLUMN = "pause_status"
# tab -> the instance its rows belong to
ARCHIVE = {"UP-Archive": "UP", "KA-Archive": "KA"}
ACTIVE = {"UP-Active": "UP", "KA-Active": "KA"}
CHUNK = 150          # ids per PATCH; keeps the ?item_id=in.(...) URL sane


def pairs(book, tabs):
    out = []
    for tab, instance in tabs.items():
        sheet = book[tab]
        for row in sheet.iter_rows(min_row=2, max_col=1, values_only=True):
            value = row[0]
            if value and str(value).strip():
                out.append((instance, str(value).strip()))
    return out


def page(url, key, params):
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        resp = requests.get(f"{url}/rest/v1/bluedot_items", headers=headers,
                            timeout=300,
                            params={**params, "offset": offset, "limit": 1000})
        chunk = resp.json()
        if not isinstance(chunk, list) or not chunk:
            break
        rows += chunk
        if len(chunk) < 1000:
            break
        offset += len(chunk)
    return rows


def patch(url, key, instance, ids, value):
    """PATCH one instance's ids in chunks. Returns rows touched."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json",
               "Prefer": "return=minimal,count=exact"}
    done = 0
    for start in range(0, len(ids), CHUNK):
        batch = ids[start:start + CHUNK]
        resp = requests.patch(
            f"{url}/rest/v1/bluedot_items", headers=headers, timeout=300,
            params={"instance": f"eq.{instance}",
                    "item_domain": "eq.provider",
                    "item_id": "in.(" + ",".join(f'"{i}"' for i in batch) + ")"},
            json={COLUMN: value})
        if resp.status_code >= 400:
            raise SystemExit(f"PATCH failed at {start}: "
                             f"{resp.status_code} {resp.text[:300]}")
        done += int(resp.headers.get("content-range", "*/0").split("/")[-1] or 0)
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    book = openpyxl.load_workbook(BOOK, data_only=True)
    archive, active = set(pairs(book, ARCHIVE)), set(pairs(book, ACTIVE))
    print(f"workbook: {len(archive)} archive, {len(active)} active")

    clash = archive & active
    if clash:
        raise SystemExit(f"{len(clash)} rows are in both an Archive and an "
                         f"Active tab; resolve the workbook first: {sorted(clash)[:5]}")

    live = page(url, key, {"select": "instance,item_id,lifecycle_status",
                           "item_domain": "eq.provider", "order": "item_id"})
    known = {(r["instance"], str(r["item_id"])) for r in live}
    print(f"database: {len(known)} provider rows")

    missing = archive - known
    if missing:
        # a pause for a row that does not exist is a silent no-op, so say so
        print(f"  WARNING: {len(missing)} archive rows are not in the database")

    todo = archive & known
    rest = known - todo
    print(f"  -> 'Y' on {len(todo)} rows")
    print(f"  -> 'N' on {len(rest)} rows "
          f"({len(active & known)} active, {len(rest) - len(active & known)} in neither tab)")

    if args.dry_run:
        by = collections.Counter(i for i, _ in todo)
        print(f"\n--dry-run: nothing written. By instance: {dict(by)}")
        return

    # 'N' first, across everything, so no row is left null and a removed id
    # reverts to active rather than keeping a stale 'Y'
    for instance in sorted({i for i, _ in known}):
        ids = [j for i, j in known if i == instance]
        n = patch(url, key, instance, ids, "N")
        print(f"  {instance}: {n} rows set to 'N'")
    for instance in sorted({i for i, _ in todo}):
        ids = [j for i, j in todo if i == instance]
        n = patch(url, key, instance, ids, "Y")
        print(f"  {instance}: {n} rows set to 'Y'")

    head = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}
    for label, flt in (("Y", "eq.Y"), ("N", "eq.N"), ("null", "is.null")):
        resp = requests.get(f"{url}/rest/v1/bluedot_items", headers=head, timeout=120,
                            params={"select": "item_id", "item_domain": "eq.provider",
                                    COLUMN: flt, "limit": 1})
        total = resp.headers.get("content-range", "0-0/0").split("/")[-1]
        print(f"  verify {COLUMN}={label}: {total}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
