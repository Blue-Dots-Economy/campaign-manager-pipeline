"""Fills the inbound tabs of "Application - Operation Rozgar".

    UP - Inbound    Ghaziabad
    KA - Inbound    Hubli-Dharwad

Inbound calls are seekers who rang us, rather than seekers we dialled. They
sat outside the pipeline entirely until load_inbound.py put them in
kkb_mastersheet with channel = 'Inbound', and they stay on their own tabs
rather than being folded into UP and KA: mixing them in would change what
those tabs have always meant without anything on them saying so.

Each tab carries two kinds of row:

    an application      the caller applied to a job, so the job columns are filled
    a high-intent call  intent_score > 4 and no application - job columns blank,
                        and the row is marked with a star in the "High intent"
                        column so it reads at a glance

A row can be both: an application from a caller who also scored above 4 gets
the star too.

Test calls are excluded. pipeline.TEST_PHONES lists the numbers the team rang
in from, and their rows carry test_flag in Supabase.

Columns never written, because people type them:
    Remarks, Seeker Match Observations, job family

    python push_inbound_sheets.py --dry-run
    python push_inbound_sheets.py
"""
import argparse
import collections
import csv
import io
import os

from dotenv import load_dotenv

from push_sheets import (SPREADSHEET_ID, SERVICE_ACCOUNT, SCOPES, blank_na,
                         bump_batch, job_catalogue, next_batch, norm_phone,
                         page)
from sheet_format import find_col, format_tab, same_value

load_dotenv()

OUT_DIR = "data"
STAR = "⭐"

TABS = {
    "UP - Inbound": "Ghaziabad",
    "KA - Inbound": "Hubli-Dharwad",
}

# The live UP - Inbound header, kept exactly, with three columns appended.
# call_id and intent_score were not there before; without them a high-intent
# row has no key and no way to show why it qualified.
HEADER = ["Batch", "application in DB", "job_id", "job role", "job family",
          "date of application", "seeker name", "seeker gender", "seeker age",
          "seeker dob", "seeker contact", "seeker location", "institution name",
          "seeker qualification", "seeker specialization", "company name",
          "provider phone", "provider location", "number of openings", "salary",
          "provider qualification required", "experience required", "Remarks",
          "Seeker Match Observations", "call_id", "intent_score", "High intent"]

# What the pipeline owns. Remarks, Seeker Match Observations and job family
# are absent on purpose: people write those.
#
# Batch is absent too, for a different reason. It records which push a row
# ARRIVED in, so rewriting it is wrong by definition - and because the
# counter moves whenever any tab appends, leaving it here rewrote every
# row on every run and reported the whole tab as refreshed.
OWNED = ("application in DB", "job_id", "job role",
         "date of application", "seeker name", "seeker contact",
         "company name", "provider phone", "provider location",
         "number of openings", "salary", "provider qualification required",
         "experience required", "call_id", "intent_score", "High intent")

CALL_COLUMNS = ("call_id,campaign_name,campaign_date,phone,phone_number,"
                "seeker_name,intent_score,jfc_campaign,channel,test_flag,"
                "apply_api_success,applied_to_job,applications_count")


def open_rozgar():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT, scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)


def applied_at_all(call):
    if call.get("apply_api_success") is not None or call.get("applied_to_job") is True:
        return True
    try:
        return int(call.get("applications_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def row_key(row, columns):
    """Two kinds of row, so two kinds of key.

    An application is identified by its job and the seeker, which is what the
    53 rows already on UP - Inbound carry - they predate the call_id column,
    so keying everything on call_id would fail to match them and append 53
    duplicates. A high-intent row has no job, so it keys on the call instead.
    """
    def get(name):
        # str(): a row read back from the sheet holds only strings, but a row
        # we just built holds whatever Supabase returned, None included
        if name not in columns:
            return ""
        i = columns.index(name)
        return str(row[i] if len(row) > i and row[i] is not None else "").strip()

    job, phone = get("job_id"), norm_phone(get("seeker contact"))
    if job and job.upper() != "NA" and phone:
        return ("app", job, phone)
    call = get("call_id")
    return ("call", call) if call else None


def ensure_columns(ws, header):
    """Add any of our columns the tab does not have yet, on the right.

    UP - Inbound predates call_id, intent_score and High intent. Without this
    the appended rows get shaped to the old 24-column header, which silently
    drops the star and leaves every high-intent row keyless - 51 of them
    vanished on the first run before this existed.
    """
    existing = ws.get_all_values()
    head = [h.strip() for h in existing[0]] if existing else []
    missing = [c for c in header if c not in head]
    if not head or not missing:
        return head
    if len(head) + len(missing) > ws.col_count:
        ws.add_cols(len(head) + len(missing) - ws.col_count)
    start = len(head) + 1
    letter = (chr(64 + start) if start <= 26
              else chr(64 + (start - 1) // 26) + chr(65 + (start - 1) % 26))
    ws.update([missing], f"{letter}1", value_input_option="USER_ENTERED")
    print(f"  {ws.title:<16} added columns: {', '.join(missing)}")
    return head + missing


def merge(ws, rows, header):
    """Refresh what we own, append what is new, clear nothing."""
    ensure_columns(ws, header)
    existing = ws.get_all_values()
    head = [h.strip() for h in existing[0]] if existing else []
    if not head:
        ws.update([header] + rows, "A1", value_input_option="USER_ENTERED")
        return len(rows), 0, 0

    by_key = {}
    for r in rows:
        k = row_key(r, header)
        if k:
            by_key.setdefault(k, r)

    updates, changed, seen = [], 0, set()
    for n, row in enumerate(existing[1:], start=2):
        k = row_key(row, head)
        if not k or k not in by_key:
            continue
        seen.add(k)
        src, newrow, touched = by_key[k], list(row) + [""] * (len(head) - len(row)), False
        for col in OWNED:
            if col not in head or col not in header:
                continue
            i, v = head.index(col), str(src[header.index(col)])
            if v and not same_value(newrow[i], v):
                newrow[i], touched = v, True
        if touched:
            changed += 1
            updates.append({"range": f"A{n}", "values": [newrow]})
    for start in range(0, len(updates), 200):
        ws.batch_update(updates[start:start + 200], value_input_option="USER_ENTERED")

    fresh = [r for k, r in by_key.items() if k not in seen]
    if fresh:
        shaped = [[(r[header.index(c)] if c in header else "") for c in head]
                  for r in fresh]
        anchor = len(existing) + 1
        if anchor + len(shaped) - 1 > ws.row_count:
            ws.add_rows(anchor + len(shaped) - 1 - ws.row_count)
        ws.update(shaped, f"A{anchor}", value_input_option="USER_ENTERED")
    return len(fresh), changed, len(existing) - 1


def build(calls, apps, cat, batch, threshold):
    """Rows per region: every inbound application, then every high-intent
    inbound caller who did not apply."""
    out = collections.defaultdict(list)
    by_call = {str(c["call_id"]): c for c in calls}

    def score(call):
        try:
            return float(call.get("intent_score") or 0)
        except (TypeError, ValueError):
            return 0.0

    for a in apps:
        call = by_call.get(str(a["call_id"]))
        if not call:
            continue
        extra = cat.get(str(a.get("job_id") or "")) or {}
        status = "Yes" if a.get("applied") else f"No ({a.get('failure_reason') or 'api_failed'})"
        out[call["jfc_campaign"]].append([
            batch, status, a.get("job_id"), blank_na(a.get("job_role")), "",
            a.get("campaign_date"), a.get("seeker_name") or call.get("seeker_name"),
            "", "", "", norm_phone(a.get("phone") or call.get("phone")), "", "", "", "",
            blank_na(a.get("company_name")) or blank_na(extra.get("company")),
            blank_na(a.get("provider_phone")), blank_na(a.get("job_location")),
            blank_na(a.get("vacancies")), blank_na(a.get("salary")),
            blank_na(a.get("qualification_required")), blank_na(extra.get("experience")),
            "", "", a.get("call_id"), score(call),
            STAR if score(call) > threshold else "",
        ])

    applied_calls = {str(a["call_id"]) for a in apps}
    for call in calls:
        if str(call["call_id"]) in applied_calls or applied_at_all(call):
            continue
        if score(call) <= threshold:
            continue
        out[call["jfc_campaign"]].append([
            batch, "", "", "", "", call.get("campaign_date"),
            call.get("seeker_name"), "", "", "",
            norm_phone(call.get("phone") or call.get("phone_number")),
            "", "", "", "", "", "", "", "", "", "", "", "", "",
            call.get("call_id"), score(call), STAR,
        ])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=4.0,
                    help="high intent means intent_score STRICTLY GREATER than this")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    batch = next_batch()          # bumped at the end, only if rows landed

    print("Reading inbound calls...")
    calls = [c for c in page(url, key, "kkb_mastersheet", CALL_COLUMNS,
                             extra={"channel": "eq.Inbound"})
             if not c.get("test_flag") and c.get("jfc_campaign") in TABS.values()]
    print(f"  {len(calls)} inbound calls, excluding test numbers")

    by_call = {str(c["call_id"]) for c in calls}
    apps = [a for a in page(url, key, "kkb_applications_full",
                            "call_id,job_id,applied,failure_reason,job_role,"
                            "company_name,provider_phone,job_location,vacancies,"
                            "salary,qualification_required,campaign_date,"
                            "seeker_name,phone", order="id")
            if str(a["call_id"]) in by_call]
    cat = job_catalogue(url, key)
    print(f"  {len(apps)} applications from them, {len(cat)} job postings")

    rows = build(calls, apps, cat, batch, args.threshold)
    print(f"\n  Batch = {batch}   high intent = intent_score > {args.threshold:g}")
    for tab, region in TABS.items():
        got = rows.get(region, [])
        stars = sum(1 for r in got if r[HEADER.index("High intent")])
        apps_n = sum(1 for r in got if r[HEADER.index("job_id")])
        print(f"    {tab:<16} {len(got):>5} rows   {apps_n} applications, "
              f"{len(got) - apps_n} high-intent calls, {stars} starred")

    os.makedirs(OUT_DIR, exist_ok=True)
    for tab, region in TABS.items():
        path = os.path.join(OUT_DIR, f"{tab.lower().replace(' - ', '_')}.csv")
        with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(HEADER)
            w.writerows(rows.get(region, []))

    if args.dry_run:
        print("\n--dry-run: CSVs written, sheet untouched.")
        return

    book = open_rozgar()
    print(f"\nWriting to {book.title!r}...")
    titles = {w.title for w in book.worksheets()}
    appended = 0
    for tab, region in TABS.items():
        if tab not in titles:
            book.add_worksheet(tab, rows=len(rows.get(region, [])) + 50,
                               cols=len(HEADER))
            book.worksheet(tab).update([HEADER], "A1",
                                       value_input_option="USER_ENTERED")
            print(f"  {tab:<16} created")
        added, changed, kept = merge(book.worksheet(tab), rows.get(region, []), HEADER)
        print(f"  {tab:<16} {kept} kept, {changed} refreshed, {added} appended")
        appended += added

    if appended:
        bump_batch(batch)
    meta = book.fetch_sheet_metadata({"includeGridData": False})
    for tab in TABS:
        print(f"  {tab:<16} formatted, {format_tab(book, meta, tab)} columns")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
