"""Builds the output-sheet tabs from Supabase, and optionally writes them.

    python push_sheets.py --dry-run          # write CSVs, touch no sheet
    python push_sheets.py --sheet-preview    # write to "<tab> (pipeline)" copies
    python push_sheets.py --sheet-replace    # overwrite the live tabs

Currently covers the two High Intend tabs of "Application - Operation Rozgar":

    UP - High Intend   <- jfc_campaign = Ghaziabad
    KA - High intend   <- jfc_campaign = Hubli-Dharwad

Columns match the live tabs exactly:

    Batch, campaign_type, call_id, call_duration_seconds, call_datetime_ist,
    phone, candidate_, candidate details, intent_score

Decisions baked in, per the sheet owner:
  * campaign_type  = our real campaign_name (kkb_up_sept21), not the old
    KKB_Hindi_DayN numbering - campaign_day is 1 on 71,443 of 71,546 rows so it
    cannot reproduce that scheme.
  * candidate details omits Age. bluedot_seeker_profiles returns age as "2***"
    and gender as "D***": the S3 dumps are non-PII by design, so age is masked
    at source and cannot be recovered from the database.
  * Batch is a run counter starting at 50, +1 per push, held in
    data/sheet_batch.json.
  * High Intend = intent_score > 4 AND no application. An apply that FAILED
    still counts as an application and belongs on the applications tab, so both
    outcomes are excluded here - the two tabs partition rather than overlap.
  * `job family` is dropped: it is a taxonomy that exists in no table.

The application tabs (UP / KA) are not built yet. They cover every apply
attempt, successful or failed, with `application in DB` reading "Yes" or
"No (api_failed)".
"""
import argparse
import collections
import csv
import io
import json
import os
import re

import requests
from dotenv import load_dotenv

from sheet_format import format_tab

load_dotenv()

SPREADSHEET_ID = "1JDsC-hWLzVSswMXV4nTHZyy1IiJNBuT2ks-EHxnLDuU"   # Application - Operation Rozgar
SERVICE_ACCOUNT = os.path.join("secrets", "google-service-account.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

COUNTER = os.path.join("data", "sheet_batch.json")
FIRST_BATCH = 50
OUT_DIR = "data"

HEADER = ["Batch", "campaign_type", "call_id", "call_duration_seconds",
          "call_datetime_ist", "phone", "candidate_", "candidate details",
          "intent_score"]

TABS = {
    "UP - High Intend": "Ghaziabad",
    "KA - High intend": "Hubli-Dharwad",
}
APP_TABS = {
    "UP": "Ghaziabad",
    "KA": "Hubli-Dharwad",
}
# Their header, minus `job family` (a taxonomy held nowhere). Four seeker
# columns stay blank: seeker location / institution name / seeker qualification
# / seeker specialization lived only in the seeker_csv_dumps PII export, which
# no longer exists. `Remarks` is hand-written and is carried over, never blanked.
APP_HEADER = ["Batch", "application in DB", "job_id", "job role", "job family",
              "date of application", "seeker name", "seeker contact",
              "seeker location", "institution name", "seeker qualification",
              "seeker specialization", "company name", "provider phone",
              "provider location", "number of openings", "salary",
              "provider qualification required", "experience required", "Remarks"]
CALL_COLUMNS = ("call_id,campaign_name,call_duration_seconds,campaign_date,phone,"
                "channel,test_flag,"
                "phone_number,seeker_name,intent_score,jfc_campaign,"
                "apply_api_success,applied_to_job,applications_count")


def page(url, key, table, select, extra=None, page_size=1000, order="call_id"):
    """PostgREST caps a page at 1000 whatever you ask for, and unordered pages
    are not stable - both learned the hard way."""
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset = [], 0
    while True:
        params = {"select": select, "order": order, "offset": offset, "limit": page_size}
        if extra:
            params.update(extra)
        r = requests.get(endpoint, headers=headers, params=params, timeout=300)
        if r.status_code >= 400:
            raise SystemExit(f"{table}: {r.status_code} {r.text[:300]}")
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < page_size:
            break
        offset += len(chunk)
    return rows


# The bot writes the literal string "NA" when it has no value - 109 company
# names and 402 qualifications across the two tabs. Blanked here, at the sheet
# layer, so kkb_mastersheet keeps faithfully whatever Raya actually said.
NA_STRINGS = {"na", "n/a", "none", "null", "nan", "not available", "-", "--", "nil"}


def blank_na(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in NA_STRINGS else text


def norm_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else None


def next_batch(advance=False):
    """The batch number this run would stamp on new rows.

    Read-only by default. Advancing it is bump_batch's job, called only once a
    run has actually appended something - advancing here instead burned 23
    numbers on runs that re-formatted the sheet and appended nothing, leaving
    gaps in a column whose only purpose is to say which push a row arrived in.
    """
    n = FIRST_BATCH
    if os.path.exists(COUNTER):
        try:
            n = int(json.load(io.open(COUNTER, encoding="utf-8"))["next"])
        except Exception:
            n = FIRST_BATCH
    if advance:
        bump_batch(n)
    return n


def bump_batch(n):
    os.makedirs(os.path.dirname(COUNTER) or ".", exist_ok=True)
    json.dump({"next": n + 1}, io.open(COUNTER, "w", encoding="utf-8"))


def applied_at_all(call):
    """True if an apply was attempted, whether it succeeded or errored.

    apply_api_success is the reliable signal: True = the apply_job tool call
    worked, False = it was attempted and failed, None = never attempted.
    applied_to_job and applications_count are checked too because Raya
    sometimes reports an application the transcript does not evidence.
    """
    if call.get("apply_api_success") is not None:
        return True
    if call.get("applied_to_job") is True:
        return True
    try:
        return int(call.get("applications_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def candidate_details(profile):
    """"Role: Fitter | Status: Inactive | Source: KA Prod" - Age deliberately
    absent; it is masked in the non-PII dump."""
    if not profile:
        return ""
    bits = []
    if profile.get("role"):
        bits.append(f"Role: {profile['role']}")
    if profile.get("status"):
        bits.append(f"Status: {'Active' if profile['status'] == 'live' else 'Inactive'}")
    if profile.get("instance"):
        bits.append(f"Source: {profile['instance']} Prod - Seeker Profile")
    return " | ".join(bits)


def build_profiles(url, key):
    """phone -> {role, status, instance}, via seeker_id.

    The Blue Dot dumps carry no phone numbers, so the bridge is
    aggregated_seeker_journey: phone -> seeker_id, then bluedot_items.created_by
    -> the seeker's own profile item.
    """
    journey = page(url, key, "aggregated_seeker_journey", "seeker_id,phone,instance",
                   extra={"in_bluedot": "is.true", "phone": "not.is.null"},
                   order="seeker_id")
    by_seeker = {}
    for row in journey:
        p = norm_phone(row.get("phone"))
        if p:
            by_seeker.setdefault(row["seeker_id"], p)

    profiles = page(url, key, "bluedot_seeker_profiles",
                    "created_by,role_wanted_raw,lifecycle_status,instance",
                    order="item_id")
    out = {}
    for prof in profiles:
        phone = by_seeker.get(prof.get("created_by"))
        if not phone or phone in out:
            continue
        role = (prof.get("role_wanted_raw") or "").strip()
        if role.lower() in ("nan", "na", "none", ""):
            role = ""
        out[phone] = {"role": role, "status": prof.get("lifecycle_status"),
                      "instance": prof.get("instance")}
    return out


def job_catalogue(url, key):
    """job_id -> everything the sheet needs about the posting.

    item_state holds the posting itself; it has no location field, so provider
    location and phone come from dkb_mastersheet, which called those employers.
    """
    cat = {}
    for it in page(url, key, "bluedot_items", "item_id,item_state,created_by,instance",
                   extra={"item_domain": "eq.provider"}, order="item_id"):
        st = it.get("item_state") or {}
        if not isinstance(st, dict):
            st = {}
        lo, hi = st.get("salaryMin"), st.get("salaryMax")
        salary = ""
        if lo or hi:
            salary = f"{lo or ''}-{hi or ''}".strip("-")
        qual = (st.get("minQualificationVocational") or st.get("minQualificationCollege")
                or st.get("minQualificationSchool") or st.get("minEducationalInstitute"))
        cat[str(it["item_id"])] = {
            "role": st.get("role"),
            "company": st.get("jobProviderName"),
            "openings": st.get("positions"),
            "salary": salary,
            "qualification": qual,
            "experience": st.get("workExperienceYears") or st.get("candidateExperienceType"),
            "provider_phone": None,
            "provider_location": None,
        }
    for row in page(url, key, "dkb_mastersheet",
                    "job_id,contact_phone,location_input,company_name", order="call_id"):
        jid = str(row.get("job_id") or "")
        if not jid:
            continue
        entry = cat.setdefault(jid, {"role": None, "company": None, "openings": None,
                                     "salary": "", "qualification": None,
                                     "experience": None, "provider_phone": None,
                                     "provider_location": None})
        entry["provider_phone"] = entry["provider_phone"] or row.get("contact_phone")
        entry["provider_location"] = entry["provider_location"] or row.get("location_input")
        entry["company"] = entry["company"] or row.get("company_name")
    return cat


def seeker_index(url, key):
    """Blue Dot seeker user id -> {phone, name, called}."""
    idx = {}
    for row in page(url, key, "aggregated_seeker_journey",
                    "seeker_id,phone,seeker_name,ever_called,instance",
                    extra={"in_bluedot": "is.true"}, order="seeker_id"):
        idx[row["seeker_id"]] = {
            "phone": norm_phone(row.get("phone")),
            "name": row.get("seeker_name"),
            "called": bool(row.get("ever_called")),
        }
    return idx


def existing_remarks(book, tab):
    """Remarks are typed by people. Keyed on job_id + seeker contact so a
    rewrite never wipes them."""
    keep = {}
    try:
        values = book.worksheet(tab).get_all_values()
    except Exception:
        return keep
    if not values:
        return keep
    head = [h.strip() for h in values[0]]
    try:
        i_job = head.index("job_id")
        i_phone = head.index("seeker contact")
        i_rem = head.index("Remarks")
    except ValueError:
        return keep
    for row in values[1:]:
        if len(row) <= max(i_job, i_phone, i_rem):
            continue
        note = (row[i_rem] or "").strip()
        if note:
            keep[(row[i_job].strip(), norm_phone(row[i_phone]))] = note
    return keep


def call_days(calls):
    """(phone, YYYY-MM-DD) for every call we made.

    An application "made during a voice call" is one the seeker submitted on a
    day we called them. Same-day is the tightest test available: kkb_mastersheet
    stores campaign_date but no call time, so the application timestamp cannot
    be matched to the call window itself.
    """
    days = set()
    for c in calls:
        phone = norm_phone(c.get("phone") or c.get("phone_number"))
        day = str(c.get("campaign_date") or "")[:10]
        if phone and day:
            days.add((phone, day))
    return days


def attempts_as_jobs(call):
    """apply_attempts -> the same shape as jobs_applied / jobs_failed_to_apply.

    Used only where the bot never listed the job in call_output: 81 successful
    applies and 249 failed ones. The tool call carries the job_id, so these are
    real applications the sheet would otherwise miss entirely - but it carries
    no company or salary, so those cells stay empty.
    """
    ok, bad = [], []
    for a in (call.get("apply_attempts") or []):
        if not isinstance(a, dict) or not a.get("job_id"):
            continue
        job = {"job_id": a["job_id"], "role": None, "company_name": None,
               "salary_offered": None, "company_location": None,
               "qualification_required": None}
        if a.get("ok"):
            ok.append(job)
        else:
            job["failure_reason"] = a.get("error") or "api_failed"
            bad.append(job)
    return ok, bad


def build_app_rows(apps, cat, remarks, batch, jfc):
    """One sheet row per application, read from kkb_applications_full.

    The flattening used to happen here, re-deriving everything from the JSON
    columns on every run. It now lives in transform.make_application_rows and
    is written at push time, so this is just presentation: pick the columns,
    blank the "NA" placeholders, keep the hand-typed Remarks.
    """
    rows = []
    for a in sorted((x for x in apps if x.get("jfc_campaign") == jfc),
                    key=lambda x: str(x.get("campaign_date") or ""), reverse=True):
        jid = str(a.get("job_id") or "")
        phone = norm_phone(a.get("phone"))
        extra = cat.get(jid) or {}
        status = "Yes" if a.get("applied") else f"No ({a.get('failure_reason') or 'api_failed'})"

        def pick(*vals):
            for v in vals:
                cleaned = blank_na(v)
                if cleaned:
                    return cleaned
            return ""

        rows.append([
            batch, status, jid, blank_na(a.get("job_role")), "",
            a.get("campaign_date"), a.get("seeker_name"), phone,
            "", "", "", "",
            pick(a.get("company_name"), extra.get("company")),
            pick(a.get("provider_phone"), extra.get("provider_phone")),
            pick(a.get("job_location"), extra.get("provider_location")),
            pick(a.get("vacancies"), extra.get("openings")),
            pick(a.get("salary"), extra.get("salary")),
            pick(a.get("qualification_required"), extra.get("qualification")),
            blank_na(extra.get("experience")),
            remarks.get((jid, phone), ""),
        ])
    return [["" if v is None else v for v in r] for r in rows]


def open_sheet():
    import gspread
    from google.oauth2.service_account import Credentials
    if not os.path.exists(SERVICE_ACCOUNT):
        raise SystemExit(f"{SERVICE_ACCOUNT} not found - see the setup notes.")
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT, scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)


def merge_tab(book, title, header, rows):
    """Append only what is genuinely new; never touch an existing row.

    The application tabs hold rows added by hand - older applications Raya
    never reported - plus typed Remarks. Replacing the grid would destroy both,
    so this keeps every existing row exactly as it is and appends the rest,
    matched on job_id + seeker contact.
    """
    ws = book.worksheet(title)
    existing = ws.get_all_values()
    if not existing:
        ws.update([header] + rows, "A1", value_input_option="USER_ENTERED")
        return title, len(rows), 0

    head = [h.strip() for h in existing[0]]
    try:
        i_job, i_phone = head.index("job_id"), head.index("seeker contact")
    except ValueError:
        raise SystemExit(f"{title!r}: no job_id / seeker contact column to merge on; "
                         f"header is {head[:8]}")
    have = set()
    for row in existing[1:]:
        if len(row) > max(i_job, i_phone):
            have.add((row[i_job].strip(), norm_phone(row[i_phone])))

    j_job, j_phone = header.index("job_id"), header.index("seeker contact")
    fresh = [r for r in rows
             if (str(r[j_job]).strip(), norm_phone(r[j_phone])) not in have]

    # Appending alone is not enough: a row already present keeps whatever it
    # was first written with, so a later improvement (company names recovered
    # from recommendations_input, say) never reaches the sheet. Refresh the
    # derived job columns in place, and never touch Remarks or any column the
    # pipeline does not own.
    OWNED = ("job role", "company name", "provider phone", "provider location",
             "number of openings", "salary", "provider qualification required",
             "experience required")
    by_key = {(str(r[j_job]).strip(), norm_phone(r[j_phone])): r for r in rows}
    updates, changed = [], 0
    for n, row in enumerate(existing[1:], start=2):
        if len(row) <= max(i_job, i_phone):
            continue
        src = by_key.get((row[i_job].strip(), norm_phone(row[i_phone])))
        if not src:
            continue
        newrow = list(row) + [""] * (len(head) - len(row))
        touched = False
        for col in OWNED:
            if col not in head or col not in header:
                continue
            k, v = head.index(col), str(src[header.index(col)])
            if v and newrow[k] != v:
                newrow[k] = v
                touched = True
        if touched:
            changed += 1
            updates.append({"range": f"A{n}", "values": [newrow]})
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")

    if not fresh:
        return title, 0, len(existing) - 1, changed

    # the live sheet may have a different column order; map by name and leave
    # any column we do not produce (Remarks, job family) untouched
    idx = {name: k for k, name in enumerate(header)}
    shaped = [[("" if idx.get(col) is None else r[idx[col]]) for col in head]
              for r in fresh]
    # NOT append_rows: it lets the Sheets API guess where the "table" ends, and
    # on a tab whose first column is blank for the top 100 rows it guessed B537
    # instead of A544 - overwriting 7 live rows and shifting every appended row
    # one column right. Anchor the write explicitly to the row after the last.
    anchor = len(existing) + 1
    if anchor + len(shaped) - 1 > ws.row_count:
        ws.add_rows(anchor + len(shaped) - 1 - ws.row_count)
    ws.update(shaped, f"A{anchor}", value_input_option="USER_ENTERED")
    return title, len(fresh), len(existing) - 1, changed


def write_tab(book, title, header, rows, replace):
    """Replace a tab's contents wholesale.

    Preview mode writes to "<tab> (pipeline)" so the live tab is untouched and
    you can diff the two side by side. Only --sheet-replace touches the real
    one, and even then it rewrites the whole grid rather than appending, so a
    re-run is idempotent instead of doubling the rows.
    """
    name = title if replace else f"{title} (pipeline)"
    try:
        ws = book.worksheet(name)
        ws.clear()
    except Exception:
        ws = book.add_worksheet(title=name, rows=max(len(rows) + 10, 100),
                                cols=len(header))
    # one update call, not one per row - the API quota is per-request
    ws.update([header] + rows, "A1", value_input_option="USER_ENTERED")
    return name, len(rows)


# Every tab of the Rozgar sheet that the pipeline maintains. "initial
# applications " is deliberately absent: its first header cell is a bare UUID,
# so it is a raw paste rather than a maintained tab, and giving it a header bar
# would dress up something nobody is keeping current.
ROZGAR_TABS = ("UP", "UP - Inbound", "UP - High Intend",
               "KA", "KA - Inbound", "KA - High intend")


def format_rozgar(book=None):
    """Apply the shared colour scheme and column widths to every Rozgar tab."""
    book = book or open_sheet()
    meta = book.fetch_sheet_metadata({"includeGridData": False})
    live = {s["properties"]["title"] for s in meta["sheets"]}
    for tab in ROZGAR_TABS:
        if tab not in live:
            print(f"  {tab:<20} not on this sheet, skipped")
            continue
        print(f"  {tab:<20} formatted, {format_tab(book, meta, tab)} columns")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=4.0,
                    help="High Intend means intent_score STRICTLY GREATER than this")
    ap.add_argument("--batch", type=int, default=None, help="override the run counter")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the CSVs but do not advance the batch counter")
    ap.add_argument("--sheet-preview", action="store_true",
                    help='write to "<tab> (pipeline)" copies, leaving the live tabs alone')
    ap.add_argument("--sheet-replace", action="store_true",
                    help="OVERWRITE the live tabs")
    ap.add_argument("--apps", action="store_true",
                    help="also build the UP / KA application tabs")
    ap.add_argument("--all-applications", action="store_true",
                    help="include applications from seekers we never called "
                         "(default: only seekers the campaign reached)")
    ap.add_argument("--format-only", action="store_true",
                    help="apply the colour scheme and column widths, "
                         "touch no values, and exit")
    args = ap.parse_args()

    if args.format_only:
        print("Formatting the Operation Rozgar tabs...")
        format_rozgar()
        return

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    batch = args.batch if args.batch is not None else next_batch(advance=not args.dry_run)
    print(f"Batch = {batch}   High Intend = intent_score > {args.threshold:g} "
          f"AND no application")

    print("Reading kkb_mastersheet (high intent, outbound only)...")
    # Outbound only, and no test calls. These tabs have always meant
    # "seekers we dialled"; inbound callers arrived in the table later and
    # live on their own tabs (push_inbound_sheets.py), so letting them in
    # here would change what the tab means with nothing on it saying so.
    raw = page(url, key, "kkb_mastersheet", CALL_COLUMNS,
               extra={"intent_score": f"gt.{args.threshold}",
                      "channel": "eq.Outbound",
                      "test_flag": "is.null"})
    # "High Intend" means high intent AND NO application - an apply that FAILED
    # still counts as an application and belongs on the applications tab, so
    # both outcomes are excluded here. The two tabs then partition cleanly
    # instead of double-counting the same seeker.
    calls = [c for c in raw if not applied_at_all(c)]
    print(f"  {len(raw)} above the threshold, {len(raw) - len(calls)} excluded "
          f"for having an application -> {len(calls)} high-intent")

    print("Building the phone -> profile map...")
    profiles = build_profiles(url, key)
    print(f"  {len(profiles)} phones with a Blue Dot profile")

    os.makedirs(OUT_DIR, exist_ok=True)
    built = {}
    for tab, jfc in TABS.items():
        rows = [c for c in calls if c.get("jfc_campaign") == jfc]
        slug = tab.lower().replace(" - ", "_").replace(" ", "_")
        path = os.path.join(OUT_DIR, f"{slug}.csv")
        matched = 0
        out_rows = []
        with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(HEADER)
            for c in sorted(rows, key=lambda x: str(x.get("campaign_date") or ""), reverse=True):
                phone = norm_phone(c.get("phone") or c.get("phone_number"))
                prof = profiles.get(phone)
                if prof:
                    matched += 1
                record = [
                    batch,
                    c.get("campaign_name"),
                    c.get("call_id"),
                    c.get("call_duration_seconds"),
                    # DATE ONLY. kkb_mastersheet has no call_datetime_ist
                    # column - dkb_mastersheet and trrain_mastersheet both do,
                    # but KKB never stored the call time, so the time-of-day in
                    # the live tab cannot be reproduced without adding that
                    # column and backfilling it from Raya.
                    c.get("campaign_date"),
                    phone or c.get("phone"),
                    c.get("seeker_name"),
                    candidate_details(prof),
                    c.get("intent_score"),
                ]
                # the sheet wants "" not None, or gspread writes the literal
                wr.writerow(record)
                out_rows.append(["" if v is None else v for v in record])
        built[tab] = out_rows
        print(f"  {tab:<18} {len(rows):>5} rows -> {path}"
              f"   ({matched} with profile detail, {len(rows) - matched} without)")

    spread = collections.Counter(c.get("campaign_name") for c in calls)
    print("\n  top campaigns in this cut:")
    for name, cnt in spread.most_common(6):
        print(f"    {cnt:>5}  {name}")

    if args.apps:
        print("\nBuilding the application tabs from kkb_applications...")
        apps = page(url, key, "kkb_applications_full",
                    "call_id,job_id,applied,failure_reason,synthetic_job_id,"
                    "job_role,company_name,provider_phone,job_location,vacancies,"
                    "salary,qualification_required,campaign_name,campaign_date,"
                    "jfc_campaign,seeker_name,phone", order="id")
        print(f"  {len(apps)} applications")
        cat = job_catalogue(url, key)
        print(f"  {len(cat)} job postings for anything still missing")

        book = open_sheet() if (args.sheet_preview or args.sheet_replace) else None
        for tab, jfc in APP_TABS.items():
            remarks = existing_remarks(book, tab) if book else {}
            rows = build_app_rows(apps, cat, remarks, batch, jfc)
            path = os.path.join(OUT_DIR, f"applications_{tab.lower()}.csv")
            with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
                wr = csv.writer(fh)
                wr.writerow(APP_HEADER)
                wr.writerows(rows)
            ok = sum(1 for r in rows if r[1] == "Yes")
            print(f"  {tab:<4} {len(rows):>5} rows  ({ok} succeeded, {len(rows) - ok} failed)"
                  f"  {len(remarks)} remarks preserved -> {path}")
            built[tab] = rows

    if args.sheet_preview or args.sheet_replace:
        mode = "LIVE TABS" if args.sheet_replace else "preview copies"
        print(f"\nWriting to Google Sheets ({mode})...")
        book = open_sheet()
        print(f"  opened {book.title!r}")
        for tab, rows in built.items():
            head = APP_HEADER if tab in APP_TABS else HEADER
            # application tabs MERGE (they hold hand-added rows and Remarks);
            # the High Intend tabs are fully derived, so they are replaced.
            if tab in APP_TABS and args.sheet_replace:
                name, added, kept, changed = merge_tab(book, tab, head, rows)
                print(f"  {name:<34} {kept} kept + {added} appended = {kept + added}"
                      f"   ({changed} existing rows enriched)")
            else:
                name, n = write_tab(book, tab, head, rows, args.sheet_replace)
                print(f"  {name:<34} {n} rows written")
    else:
        print("\nNo sheet written. Pass --sheet-preview or --sheet-replace.")

    if args.dry_run:
        print("--dry-run: batch counter NOT advanced.")
    else:
        print(f"Batch counter advanced; next push will be {batch + 1}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
