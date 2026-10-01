"""Fills the "HE Campaign - Applications" sheet from Supabase.

A different spreadsheet from Operation Rozgar, with its own tabs:

    Applications                 one row per application, HE campaigns only
    Interested but didn't apply  intent > 4 and no application
    Summary                      hand-written by the team; left alone
    Pipeline Summary             rebuilt from scratch every run
    TRRAIN Bot                   hand-managed; left alone

Every tab MERGES: existing rows keep their position and their hand-typed
columns, the derived columns are refreshed, and genuinely new rows are
appended. Nothing is ever cleared.

TRRAIN columns are filled from trrain_mastersheet, matched on phone. They
answer three questions in order, each one only meaningful if the last was yes:

    TRRAIN Bot ->             did the TRRAIN bot call this person at all?
    Answered -> Yes/ No       did they pick up?
    Result -> Yes, Maybe, No  if they picked up, what did they say to the
                              counselling offer?

So "Yes / No / (blank)" is a real and expected pattern - called, didn't pick
up, so there is no answer to record. "Yes / (blank) / ..." is not, and was a
bug: see trrain_by_phone.

"Yes / Yes / (blank)" is also expected, on 187 rows: they picked up but the
bot never got as far as the offer - trrain_pitched is False on 358 of the
360 such calls, median 26s against 59s when an answer did come back. Some
were wrong numbers, some could not hear, most just ended. Asked whether to
label these, the sheet owner said leave them blank, so we do.

Columns never written, because people fill them in themselves:
    Call Outcome, Feedback (by TRRAIN)->, Actual Outcome, interest_details,
    stream, year_of_graduation, email

Call Outcome and Feedback (by TRRAIN)-> could be derived - call_outcome
covers all 1,155 TRRAIN phones and drop_reason 78 rows - but both are the
TRRAIN team's own columns, so we leave them to write.

    python push_he_sheets.py --dry-run
    python push_he_sheets.py
"""
import argparse
import collections
import csv
import io
import os

from dotenv import load_dotenv

from push_sheets import (SERVICE_ACCOUNT, SCOPES, blank_na, bump_batch,
                         job_catalogue, next_batch, norm_phone, page)
from sheet_format import (BLOCKS, COLUMN_BLOCK, COLUMN_WIDTH, HIGHLIGHTS,
                          column_style, find_col, format_tab, rgb, same_value)
from transform import COLLEGE_NAMES

load_dotenv()

SPREADSHEET_ID = "1kL_E66d7bL3Dnwd7PbEcxQ-vpUEEzjjZrqihKbhMPlw"
OUT_DIR = "data"

# tab titles carry trailing spaces in the live sheet - matched exactly
TAB_APPS = "Applications "
TAB_INTERESTED = "Interested but didn't apply "
TAB_SUMMARY = "Summary"

# HE calls are identified by college_name, not by campaign name: two campaigns
# ("HE_LR and VMLG_Retry", "HE_7sept_vlmg&lr_unanswered") cover two colleges
# under one name, so only agent_args.college_name separates them.
HE_COLUMNS = ("call_id,campaign_name,campaign_date,phone,phone_number,seeker_name,"
              "intent_score,college_name,apply_api_success,applied_to_job,"
              "applications_count,jfc_campaign")

# what merge() owns on each tab. The three TRRAIN columns are deliberately
# absent: sync_trrain writes those, keyed on phone rather than on job_id or
# seeker_number, so it reaches rows merge() never sees.
APPS_OWNED = ("application in DB", "job_id", "job role", "date of application",
              "seeker name", "seeker contact", "institution name", "company name",
              "provider phone", "provider location", "number of openings", "salary",
              "provider qualification required", "call_id")
INTERESTED_OWNED = ("call_id", "campaign_date", "seeker_number", "name",
                    "college_name")


def summary_block(book):
    """Print the Summary tab's numbers, counted off the live tabs.

    The Summary tab itself is NOT written. It is the team's own workspace -
    column A is a repeated college label, columns B/D/E are working lists of
    phone numbers, and the counts sit in a small hand-typed block beside them
    with TRRAIN breakdowns written in prose. There is no shape here a script
    can fill without destroying the rest, so we print the numbers instead and
    they paste them in.

    Counted by unique seeker, which is what their block counts - the
    Applications tab has more rows than seekers because one person can apply
    to several jobs.
    """
    tabs = ((TAB_APPS, "institution name", "seeker contact"),
            (TAB_INTERESTED, "college_name", "seeker_number"))
    seekers, rows_per = [], []
    for tab, ccol, pcol in tabs:
        values = book.worksheet(tab).get_all_values()
        head = [h.strip() for h in values[0]]
        ci, pi = head.index(ccol), head.index(pcol)
        by, count = collections.defaultdict(set), collections.Counter()
        for row in values[1:]:
            college = (row[ci].strip() if len(row) > ci else "") or "(blank)"
            phone = norm_phone(row[pi] if len(row) > pi else "")
            if phone:
                by[college].add(phone)
            count[college] += 1
        seekers.append(by)
        rows_per.append(count)

    applied, high = seekers
    print()
    print("  Summary (unique seekers; paste into the Summary tab):")
    print(f"    {'college':<40}{'Applied':>9}{'High Intent':>13}{'Total':>8}")
    for college in sorted(set(applied) | set(high)):
        a, h = len(applied.get(college, ())), len(high.get(college, ()))
        print(f"    {college[:38]:<40}{a:>9}{h:>13}{a + h:>8}")
    ta = len(set().union(*applied.values())) if applied else 0
    th = len(set().union(*high.values())) if high else 0
    print(f"    {'TOTAL':<40}{ta:>9}{th:>13}{ta + th:>8}")
    print(f"    ({sum(rows_per[0].values())} application rows, "
          f"{sum(rows_per[1].values())} interested rows)")


TAB_PIPELINE_SUMMARY = "Pipeline Summary"
SUMMARY_HEADER = ["College", "Seekers called", "Applied", "Applications",
                  "High intent, didn't apply", "Called by TRRAIN",
                  "TRRAIN answered", "Said Yes", "Said Maybe", "Said No",
                  "Answered, no result"]


def write_summary(book, calls):
    """Rebuild the "Pipeline Summary" tab from scratch on every run.

    Deliberately NOT the existing Summary tab, which stays exactly as it is:
    that one holds working lists of phone numbers and prose notes, and there is
    no shape in it a script can fill without destroying the rest. This tab is
    wholly derived, so it is rewritten rather than merged and nothing on it is
    ever typed by hand.

    Everything is counted by unique seeker except Applications, which counts
    rows, because one person can apply to several jobs - that gap is the point
    of showing both. "Seekers called" comes from kkb_mastersheet rather than
    from the tabs, so it covers everyone the HE bot dialled, including the ones
    who neither applied nor scored high enough to appear anywhere else.
    """
    called = collections.defaultdict(set)
    for c in calls:
        phone = norm_phone(c.get("phone") or c.get("phone_number"))
        if c.get("college_name") and phone:
            called[c["college_name"].strip()].add(phone)

    def fresh():
        return {k: set() for k in ("applied", "high", "tr_called", "tr_answered",
                                   "Yes", "Maybe", "No", "none")} | {"apps": 0}

    stats = collections.defaultdict(fresh)
    for tab, ccol, pcol, is_app in (
            (TAB_APPS, "institution name", "seeker contact", True),
            (TAB_INTERESTED, "college_name", "seeker_number", False)):
        values = book.worksheet(tab).get_all_values()
        head = [h.strip() for h in values[0]]
        ci, pi = head.index(ccol), head.index(pcol)
        bi, ai = find_col(head, "TRRAIN Bot ->"), find_col(head, "Answered -> Yes/ No")
        ri = find_col(head, "Result -> Yes, Maybe, No")
        for row in values[1:]:
            phone = norm_phone(row[pi] if len(row) > pi else "")
            if not phone:
                continue
            college = (row[ci].strip() if len(row) > ci else "") or "(no college)"

            def cell(i):
                return (row[i].strip() if i is not None and len(row) > i else "")

            for key in (college, "TOTAL"):
                s = stats[key]
                if is_app:
                    s["apps"] += 1
                    s["applied"].add(phone)
                else:
                    s["high"].add(phone)
                if cell(bi) == "Yes":
                    s["tr_called"].add(phone)
                if cell(ai) == "Yes":
                    s["tr_answered"].add(phone)
                    said = cell(ri)
                    s[said if said in ("Yes", "Maybe", "No") else "none"].add(phone)

    def line(name, s, seekers):
        return [name, seekers, len(s["applied"]), s["apps"], len(s["high"]),
                len(s["tr_called"]), len(s["tr_answered"]),
                len(s["Yes"]), len(s["Maybe"]), len(s["No"]), len(s["none"])]

    out = [line(c, stats[c], len(called.get(c, ())))
           for c in sorted(stats) if c != "TOTAL"]
    if "TOTAL" in stats:
        every = set().union(*called.values()) if called else set()
        out.append(line("TOTAL", stats["TOTAL"], len(every)))

    note = ("Rebuilt by push_he_sheets.py - do not type here, edits are "
            "overwritten. Unique seekers, except Applications which counts rows. "
            "Yes/Maybe/No are answers to the TRRAIN counselling offer; "
            '"Answered, no result" means they picked up but the bot never got '
            "as far as the offer.")
    body = [SUMMARY_HEADER] + out + [[""] * len(SUMMARY_HEADER), [note]]
    body = [r + [""] * (len(SUMMARY_HEADER) - len(r)) for r in body]

    try:
        ws = book.worksheet(TAB_PIPELINE_SUMMARY)
        ws.clear()
    except Exception:
        ws = book.add_worksheet(TAB_PIPELINE_SUMMARY,
                                rows=len(body) + 10, cols=len(SUMMARY_HEADER))
    ws.update(body, "A1", value_input_option="USER_ENTERED")
    # the tab is rebuilt every run, so the formatting is reapplied with it
    last = chr(64 + len(SUMMARY_HEADER))
    ws.freeze(rows=1)
    ws.format(f"A1:{last}1", {"textFormat": {"bold": True}})
    ws.format(f"A{len(out) + 1}:{last}{len(out) + 1}",
              {"textFormat": {"bold": True}})
    return len(out)


def open_he():
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT, scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)


def trrain_by_phone(url, key):
    """phone -> what the TRRAIN follow-up found, folded across every attempt.

    575 of the 1,155 phones were dialled more than once. Keeping the first row
    per phone threw away 127 answers and 143 results, because a later retry
    that nobody picked up overwrote what an earlier attempt had captured. So
    each field takes the best attempt: answered beats unanswered, a recorded
    value beats a blank.

    call_answered comes back as a real bool, never None. The rows where the DB
    holds NULL are ones where the bot produced no call_output at all - no
    pitch, no interest, no drop_reason, no summary, and a median duration of 9
    seconds with 329 at exactly zero. Nobody picked up. On the sheet that is a
    "No", not an empty cell: the column answers "was this call answered?", and
    we did call.
    """
    attempts = collections.defaultdict(list)
    for r in page(url, key, "trrain_mastersheet",
                  "phone,call_answered,trrain_interest",
                  order="call_id"):
        p = norm_phone(r.get("phone"))
        if p:
            attempts[p].append(r)

    def pick(rows, key):
        for r in rows:
            v = r.get(key)
            if v not in (None, ""):
                return v
        return None

    out = {}
    for phone, rows in attempts.items():
        # an attempt that connected is the authoritative one for every field
        rows = sorted(rows, key=lambda r: r.get("call_answered") is not True)
        out[phone] = {
            "call_answered": any(r.get("call_answered") is True for r in rows),
            "trrain_interest": pick(rows, "trrain_interest"),
        }
    return out


# what the bots write instead of leaving a field empty; same meaning as blank
PLACEHOLDERS = {"na", "n/a", "none", "null", "nil", "-", "--"}


def yes_no(value):
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return ""


def trrain_cells(t):
    """(TRRAIN Bot ->, Answered -> Yes/ No, Result -> Yes, Maybe, No).

    All three blank if the bot never called this phone. Result is gated on
    Answered - an interest value against a call nobody picked up is nonsense.
    """
    if not t:
        return "", "", ""
    return ("Yes", yes_no(t["call_answered"]),
            blank_na(t.get("trrain_interest")) if t["call_answered"] else "")


# the three TRRAIN columns, written by sync_trrain on every matched row
TRRAIN_MAP = (("TRRAIN Bot ->", lambda t: trrain_cells(t)[0]),
              ("Answered -> Yes/ No", lambda t: trrain_cells(t)[1]),
              ("Result -> Yes, Maybe, No", lambda t: trrain_cells(t)[2]))


def normalise_colleges(ws, col):
    """Collapse college spellings on the tab to the canonical five.

    Rows the team adds by hand carry their own spellings - "Multanimal Modi
    College", "Multanimal Modi College, Ghaziabad" - for a college that is in
    Modinagar. 55 rows between them, and they split one college into three in
    every per-college count on the Summary tab. Only recognised variants are
    touched; an unfamiliar name is left exactly as typed.
    """
    values = ws.get_all_values()
    if not values:
        return 0
    head = [h.strip() for h in values[0]]
    if col not in head:
        return 0
    i = head.index(col)
    updates = []
    for n, row in enumerate(values[1:], start=2):
        was = row[i].strip() if len(row) > i else ""
        now = COLLEGE_NAMES.get(was.lower())
        if now and now != was:
            newrow = list(row) + [""] * (len(head) - len(row))
            newrow[i] = now
            updates.append({"range": f"A{n}", "values": [newrow]})
    for start in range(0, len(updates), 200):
        ws.batch_update(updates[start:start + 200], value_input_option="USER_ENTERED")
    return len(updates)


def sync_trrain(ws, phone_col, trrain):
    """Own the three TRRAIN columns outright, on every phone-matched row.

    These are not judgement calls that a person makes - they are facts from our
    own trrain_mastersheet - so the table is authoritative and we overwrite.
    Doing anything less left the tab self-contradicting: 13 rows read
    "Answered: No" beside a real Result, because someone typed the No before a
    later retry connected, and a fill-blanks-only pass could not correct it.

    This runs over every row, not just the ones the pipeline produces, because
    merge() keys on job_id / seeker_number while TRRAIN keys on phone - 216
    rows on these tabs are the team's own and still deserve their TRRAIN data.

    The other three columns in that block - Call Outcome, Feedback (by TRRAIN)
    and Actual Outcome - are the team's to write and are never touched.
    """
    values = ws.get_all_values()
    if not values:
        return 0, 0
    head = [h.strip() for h in values[0]]
    if phone_col not in head:
        return 0, 0
    pi = head.index(phone_col)
    cols = [(i, fn) for i, fn in
            ((find_col(head, c), fn) for c, fn in TRRAIN_MAP) if i is not None]
    ri = find_col(head, "Result -> Yes, Maybe, No")

    updates, rows, cells = [], 0, 0
    for n, row in enumerate(values[1:], start=2):
        t = trrain.get(norm_phone(row[pi] if len(row) > pi else ""))
        if not t:
            continue
        newrow = list(row) + [""] * (len(head) - len(row))
        touched = False
        for i, fn in cols:
            v = fn(t)
            # an empty value never blanks a cell, except in the two cases below
            if v and not same_value(newrow[i], v):
                newrow[i], touched, cells = v, True, cells + 1
        if ri is not None and not trrain_cells(t)[2]:
            existing = newrow[ri].strip().lower()
            # "NA" is the bot's own word for "nothing recorded" - which is what
            # an empty cell already says. Our loader turns it into NULL and we
            # write a blank; the team's earlier export passed the raw string
            # through. One state, two spellings, so collapse it to one.
            # A real Yes/No/Maybe against a call nobody answered is the other
            # case: a leftover from before a retry connected.
            if existing in PLACEHOLDERS or existing in ("yes", "no", "maybe"):
                newrow[ri], touched, cells = "", True, cells + 1
        if touched:
            rows += 1
            updates.append({"range": f"A{n}", "values": [newrow]})
    for start in range(0, len(updates), 200):
        ws.batch_update(updates[start:start + 200], value_input_option="USER_ENTERED")
    return rows, cells


def merge(ws, header, rows, key_cols, owned):
    """Refresh owned columns on matching rows, append the rest, clear nothing."""
    existing = ws.get_all_values()
    head = [h.strip() for h in existing[0]] if existing else []
    if not head:
        ws.update([header] + rows, "A1", value_input_option="USER_ENTERED")
        return len(rows), 0, 0

    def key_of(row, columns):
        parts = []
        for col in key_cols:
            if col not in columns:
                return None
            i = columns.index(col)
            v = row[i] if len(row) > i else ""
            parts.append(norm_phone(v) if "contact" in col or "number" in col
                         else str(v).strip())
        # every part must be present. With any(), a row whose job_id is
        # blank keys on the phone alone, and two unrelated applications by
        # one seeker collapse into a single row - that is rows 182/183 on
        # the live tab, two different jobs at two different companies.
        return tuple(parts) if all(parts) else None

    by_key = {}
    for r in rows:
        k = key_of(r, header)
        if k:
            by_key.setdefault(k, r)

    updates, changed, seen = [], 0, set()
    for n, row in enumerate(existing[1:], start=2):
        k = key_of(row, head)
        if not k or k not in by_key:
            continue
        seen.add(k)
        src = by_key[k]
        newrow = list(row) + [""] * (len(head) - len(row))
        touched = False
        for col in owned:
            if col not in head or col not in header:
                continue
            i, v = head.index(col), str(src[header.index(col)])
            if v and not same_value(newrow[i], v):
                newrow[i] = v
                touched = True
        if touched:
            changed += 1
            updates.append({"range": f"A{n}", "values": [newrow]})
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")

    fresh = [r for k, r in by_key.items() if k not in seen]
    if fresh:
        shaped = [[(r[header.index(c)] if c in header else "") for c in head]
                  for r in fresh]
        # NOT append_rows: it lets the Sheets API guess where the "table" ends, and
        # on a tab whose first column is blank for the top 100 rows it guessed B537
        # instead of A544 - overwriting 7 live rows and shifting every appended row
        # one column right. Anchor the write explicitly to the row after the last.
        anchor = len(existing) + 1
        if anchor + len(shaped) - 1 > ws.row_count:
            ws.add_rows(anchor + len(shaped) - 1 - ws.row_count)
        ws.update(shaped, f"A{anchor}", value_input_option="USER_ENTERED")
    return len(fresh), changed, len(existing) - 1


def main():
    ap = argparse.ArgumentParser()
    # STRICTLY greater than 4, matching the Rozgar High Intend tabs. Their
    # live tab holds 99 rows scoring exactly 4; those are theirs to keep,
    # and merge() never deletes, so they stay put - we just do not add more.
    ap.add_argument("--threshold", type=float, default=4.0,
                    help="High Intend means intent_score STRICTLY GREATER than this")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    batch = next_batch()          # bumped at the end, only if rows landed

    print("Reading HE calls (identified by college_name)...")
    calls = page(url, key, "kkb_mastersheet", HE_COLUMNS,
                 extra={"college_name": "not.is.null"})
    print(f"  {len(calls)} calls across "
          f"{len({c['college_name'] for c in calls})} colleges")

    by_call = {str(c["call_id"]): c for c in calls}
    apps = [a for a in page(url, key, "kkb_applications_full",
                            "call_id,job_id,applied,failure_reason,job_role,"
                            "company_name,provider_phone,job_location,vacancies,"
                            "salary,qualification_required,campaign_date,"
                            "seeker_name,phone", order="id")
            if str(a["call_id"]) in by_call]
    print(f"  {len(apps)} applications from those calls")

    trrain = trrain_by_phone(url, key)
    cat = job_catalogue(url, key)
    print(f"  {len(trrain)} TRRAIN phones, {len(cat)} job postings")

    # ---- Applications ----
    apps_header = ["Batch", "application in DB", "job_id", "job role",
                   "date of application", "seeker name", "seeker contact",
                   "seeker location", "institution name", "seeker qualification",
                   "seeker specialization", "company name", "provider phone",
                   "provider location", "number of openings", "salary",
                   "provider qualification required", "experience required",
                   "call_id", "TRRAIN Bot ->", "Answered -> Yes/ No",
                   "Result -> Yes, Maybe, No", "Feedback (by TRRAIN)->",
                   "Call Outcome", "Actual Outcome"]
    app_rows = []
    for a in apps:
        call = by_call[str(a["call_id"])]
        phone = norm_phone(a.get("phone") or call.get("phone"))
        t = trrain.get(phone, {})
        extra = cat.get(str(a.get("job_id") or "")) or {}
        status = "Yes" if a.get("applied") else f"No ({a.get('failure_reason') or 'api_failed'})"
        app_rows.append([
            batch, status, a.get("job_id"), blank_na(a.get("job_role")),
            a.get("campaign_date"), a.get("seeker_name"), phone,
            "", blank_na(call.get("college_name")), "", "",
            blank_na(a.get("company_name")) or blank_na(extra.get("company")),
            blank_na(a.get("provider_phone")), blank_na(a.get("job_location")),
            blank_na(a.get("vacancies")), blank_na(a.get("salary")),
            blank_na(a.get("qualification_required")), blank_na(extra.get("experience")),
            a.get("call_id"), *trrain_cells(t), "", "", "",
        ])

    # ---- Interested but didn't apply ----
    applied_calls = {str(a["call_id"]) for a in apps}
    int_header = ["call_id", "campaign_date", "seeker_number", "name", "college_name",
                  "stream", "year_of_graduation", "email", "interest_details",
                  "TRRAIN Bot ->", "Answered -> Yes/ No", "Result -> Yes, Maybe, No",
                  "Feedback (by TRRAIN)->", "Call Outcome", "Actual Outcome"]
    int_rows = []
    for c in calls:
        try:
            score = float(c.get("intent_score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score <= args.threshold or str(c["call_id"]) in applied_calls:
            continue
        if c.get("apply_api_success") is not None or c.get("applied_to_job") is True:
            continue
        phone = norm_phone(c.get("phone") or c.get("phone_number"))
        t = trrain.get(phone, {})
        int_rows.append([
            c.get("call_id"), c.get("campaign_date"), phone, c.get("seeker_name"),
            blank_na(c.get("college_name")), "", "", "", "",
            *trrain_cells(t), "", "", "",
        ])

    # ---- Summary ----
    summary = collections.defaultdict(lambda: {"applied": 0, "high_intent": 0})
    for a in apps:
        if a.get("applied"):
            summary[by_call[str(a["call_id"])].get("college_name")]["applied"] += 1
    for r in int_rows:
        summary[r[4] or "(unknown)"]["high_intent"] += 1

    os.makedirs(OUT_DIR, exist_ok=True)
    for name, header, rows in (("he_applications", apps_header, app_rows),
                               ("he_interested", int_header, int_rows)):
        with io.open(os.path.join(OUT_DIR, f"{name}.csv"), "w",
                     encoding="utf-8-sig", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(header)
            wr.writerows(rows)

    print(f"\n  Applications                : {len(app_rows)} rows")
    print(f"  Interested but didn't apply : {len(int_rows)} rows")
    print("\n  by college:")
    print(f"    {'college':<40}{'applied':>9}{'high intent':>13}")
    for college, s in sorted(summary.items(), key=lambda kv: -kv[1]["high_intent"]):
        print(f"    {str(college)[:38]:<40}{s['applied']:>9}{s['high_intent']:>13}")

    if args.dry_run:
        print("\n--dry-run: CSVs written, sheet untouched.")
        return

    book = open_he()
    print(f"\nWriting to {book.title!r}...")
    appended = 0
    for tab, header, rows, keys, owned, college_col in (
        (TAB_APPS, apps_header, app_rows, ("job_id", "seeker contact"),
         APPS_OWNED, "institution name"),
        (TAB_INTERESTED, int_header, int_rows, ("seeker_number",),
         INTERESTED_OWNED, "college_name"),
    ):
        ws = book.worksheet(tab)
        added, changed, kept = merge(ws, header, rows, keys, owned)
        print(f"  {tab.strip():<30} {kept} kept, {changed} refreshed, {added} appended")
        appended += added
        r, c = sync_trrain(ws, keys[-1], trrain)
        print(f"  {'':<30} TRRAIN sync: {c} cells on {r} rows")
        n = normalise_colleges(ws, college_col)
        print(f"  {'':<30} college names normalised: {n} rows")
    if appended:
        bump_batch(batch)

    n = write_summary(book, calls)
    print(f"  {TAB_PIPELINE_SUMMARY:<30} rebuilt, {n} rows")

    # one metadata read, reused for every tab - it carries the sheet ids,
    # grid sizes and the conditional rules that need clearing first
    meta = book.fetch_sheet_metadata({"includeGridData": False})
    for tab, freeze in ((TAB_APPS, 0), (TAB_INTERESTED, 0),
                        (TAB_PIPELINE_SUMMARY, 0)):
        print(f"  {tab.strip():<30} formatted, "
              f"{format_tab(book, meta, tab, freeze)} columns")
    summary_block(book)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
