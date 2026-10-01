"""Shared look and feel for the output sheets.

Both sheets - Operation Rozgar and HE Campaign - are wide operational tables
that people read across, so they get the same colour scheme, the same column
widths and the same status highlighting rather than each drifting into its
own. The palette is defined once here; a tab picks it up by column name.

Nothing in this module reads or writes a cell value. format_tab only touches
formatting, and the two helpers are pure.
"""
import gspread  # noqa: F401  - imported by callers, kept for a clear dependency


# ---------------------------------------------------------------- formatting
#
# These tabs are 25 columns wide and read as an undifferentiated wall without
# help. One hue per group of columns - saturated in the header, very pale in
# the body - makes the shape legible at a glance: who the seeker is, what the
# job is, who the provider is, what TRRAIN found. White means "you type here".
BLOCKS = {
    "meta":     ("#546E7A", "#F4F6F7"),
    "job":      ("#B26A00", "#FFF8E7"),
    "seeker":   ("#2F5EA8", "#EAF1FC"),
    "provider": ("#4C7A2C", "#F2F8EC"),
    "trrain":   ("#6A3E8E", "#F6EEFA"),
    "hand":     ("#37474F", "#FFFFFF"),
}
COLUMN_BLOCK = {
    "Batch": "meta", "call_id": "meta", "campaign_date": "meta",
    "application in DB": "job", "job_id": "job", "job role": "job",
    "date of application": "job",
    "seeker name": "seeker", "seeker contact": "seeker",
    "seeker location": "seeker", "institution name": "seeker",
    "seeker qualification": "seeker", "seeker specialization": "seeker",
    "seeker_number": "seeker", "name": "seeker", "college_name": "seeker",
    "stream": "seeker", "year_of_graduation": "seeker", "email": "seeker",
    "company name": "provider", "provider phone": "provider",
    "provider location": "provider", "number of openings": "provider",
    "salary": "provider", "provider qualification required": "provider",
    "experience required": "provider",
    "TRRAIN Bot ->": "trrain", "Answered -> Yes/ No": "trrain",
    "Result -> Yes, Maybe, No": "trrain",
    # the columns the team fills in are left white on purpose
    "Feedback (by TRRAIN)->": "hand", "Call Outcome": "hand",
    "Actual Outcome": "hand", "interest_details": "hand", "Remarks": "hand",
    "Seeker Match Observations": "hand", "job family": "hand",
    # the Rozgar tabs
    "campaign_type": "meta", "call_datetime_ist": "meta",
    "call_duration_seconds": "meta", "intent_score": "meta",
    "High intent": "meta", "candidate_": "seeker",
    "candidate details": "seeker", "seeker gender": "seeker",
    "seeker age": "seeker", "seeker dob": "seeker",
}
COLUMN_WIDTH = {
    "High intent": 85, "intent_score": 95, "campaign_type": 150,
    "call_datetime_ist": 150, "call_duration_seconds": 130,
    "candidate_": 150, "candidate details": 260, "seeker gender": 95,
    "seeker age": 80, "seeker dob": 110, "job family": 150,
    "Remarks": 240, "Seeker Match Observations": 260,
    "Batch": 55, "application in DB": 200, "job_id": 250, "job role": 170,
    "date of application": 115, "seeker name": 150, "seeker contact": 115,
    "seeker location": 120, "institution name": 210, "seeker qualification": 140,
    "seeker specialization": 150, "company name": 210, "provider phone": 130,
    "provider location": 160, "number of openings": 95, "salary": 95,
    "provider qualification required": 160, "experience required": 130,
    "call_id": 90, "TRRAIN Bot ->": 95, "Answered -> Yes/ No": 105,
    "Result -> Yes, Maybe, No": 135, "Feedback (by TRRAIN)->": 210,
    "Call Outcome": 120, "Actual Outcome": 160, "campaign_date": 115,
    "seeker_number": 115, "name": 150, "college_name": 210, "stream": 90,
    "year_of_graduation": 115, "email": 190, "interest_details": 230,
}
GREEN, AMBER, RED, GREY = "#188038", "#B06000", "#C5221F", "#80868B"
GREEN_BG, AMBER_BG, RED_BG = "#E6F4EA", "#FEF7E0", "#FCE8E6"

# column -> [(match type, text, text colour, background)]
HIGHLIGHTS = {
    "High intent": [("TEXT_CONTAINS", "⭐", "#B06000", AMBER_BG)],
    "application in DB": [("TEXT_STARTS_WITH", "Yes", GREEN, GREEN_BG),
                          ("TEXT_STARTS_WITH", "No", RED, RED_BG)],
    "Answered -> Yes/ No": [("TEXT_EQ", "Yes", GREEN, GREEN_BG),
                            ("TEXT_EQ", "No", GREY, None)],
    "Result -> Yes, Maybe, No": [("TEXT_EQ", "Yes", GREEN, GREEN_BG),
                                 ("TEXT_EQ", "Maybe", AMBER, AMBER_BG),
                                 ("TEXT_EQ", "No", RED, RED_BG)],
}


def column_style(name):
    """Block and pixel width for a header, tolerant of renames after the "->".

    The team edits these titles in place - "Result -> Yes, Maybe, No" became
    "Result -> Yes, Maybe, No, early hung up" - and an exact lookup would drop
    that column into the default grey instead of the TRRAIN purple.
    """
    if name in COLUMN_BLOCK:
        return COLUMN_BLOCK[name], COLUMN_WIDTH.get(name, 130)
    stem = name.split("->")[0].strip().lower()
    for known in COLUMN_BLOCK:
        if stem and known.split("->")[0].strip().lower() == stem:
            return COLUMN_BLOCK[known], COLUMN_WIDTH.get(known, 130)
    return "meta", 130


def rgb(value):
    v = value.lstrip("#")
    return {"red": int(v[0:2], 16) / 255, "green": int(v[2:4], 16) / 255,
            "blue": int(v[4:6], 16) / 255}


def format_tab(book, meta, title, freeze_cols=0):
    """Colour, size and annotate one tab. Touches formatting only, never a value.

    Reapplied on every run, because a rewritten row carries no formatting of
    its own and would otherwise show up as an unstyled stripe.
    """
    sheet = next((s for s in meta["sheets"]
                  if s["properties"]["title"] == title), None)
    if sheet is None:
        return 0
    sid = sheet["properties"]["sheetId"]
    grid = sheet["properties"]["gridProperties"]
    rows, cols = grid["rowCount"], grid["columnCount"]
    head = [h.strip() for h in book.worksheet(title).row_values(1)]
    if not head:
        return 0

    def span(i, n=1):
        return {"sheetId": sid, "startRowIndex": 0, "endRowIndex": rows,
                "startColumnIndex": i, "endColumnIndex": i + n}

    req = [
        # drop the rules from the previous run before laying them down again
        *({"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}}
          for _ in sheet.get("conditionalFormats", [])),
        {"updateSheetProperties": {
            "properties": {"sheetId": sid, "gridProperties": {
                "frozenRowCount": 1, "frozenColumnCount": freeze_cols}},
            "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}},
        # a taller header so wrapped two-word titles stay readable
        {"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "ROWS",
                      "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 52}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "ROWS",
                      "startIndex": 1, "endIndex": rows},
            "properties": {"pixelSize": 24}, "fields": "pixelSize"}},
    ]

    for i, name in enumerate(head):
        block, width = column_style(name)
        dark, pale = BLOCKS[block]
        req.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": width},
            "fields": "pixelSize"}})
        req.append({"repeatCell": {
            "range": {**span(i), "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": rgb(dark),
                "textFormat": {"bold": True, "fontSize": 10,
                               "foregroundColor": rgb("#FFFFFF")},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP"}},
            "fields": ("userEnteredFormat(backgroundColor,textFormat,"
                       "horizontalAlignment,verticalAlignment,wrapStrategy)")}})
        req.append({"repeatCell": {
            "range": {**span(i), "startRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": rgb(pale),
                "textFormat": {"fontSize": 10},
                "verticalAlignment": "MIDDLE",
                # CLIP, not WRAP: a single long remark would otherwise triple
                # the height of the row it sits in
                "wrapStrategy": "CLIP"}},
            "fields": ("userEnteredFormat(backgroundColor,textFormat,"
                       "verticalAlignment,wrapStrategy)")}})

    for name, rules in HIGHLIGHTS.items():
        i = find_col(head, name)
        if i is None:
            continue
        for kind, text, fg, bg in rules:
            fmt = {"textFormat": {"foregroundColor": rgb(fg), "bold": True}}
            if bg:
                fmt["backgroundColor"] = rgb(bg)
            req.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [{**span(i), "startRowIndex": 1}],
                "booleanRule": {
                    "condition": {"type": kind,
                                  "values": [{"userEnteredValue": text}]},
                    "format": fmt}}}})

    req.append({"setBasicFilter": {"filter": {"range": {
        "sheetId": sid, "startRowIndex": 0, "endRowIndex": rows,
        "startColumnIndex": 0, "endColumnIndex": min(len(head), cols)}}}})
    book.batch_update({"requests": req})
    return len(head)



def find_col(head, name):
    """Index of a header, matched on the part before "->".

    The team renames these in place - "Result -> Yes, Maybe, No" became
    "Result -> Yes, Maybe, No, early hung up" - and an exact-match lookup
    skips the column silently when they do, which is worse than failing.
    """
    if name in head:
        return head.index(name)
    # the stems must be EQUAL, not merely a prefix: "Answered, no result" on
    # the summary tab starts with "answered" and was picking up the Yes/No
    # rules meant for "Answered -> Yes/ No".
    stem = name.split("->")[0].strip().lower()
    for i, h in enumerate(head):
        if stem and h.split("->")[0].strip().lower() == stem:
            return i
    return None



def same_value(a, b):
    """Is what the sheet shows already what we would write?

    USER_ENTERED reformats as it writes: we send 30000 and read back "30,000".
    A plain string compare then sees a difference on every single run and
    rewrites the same cell forever, so numbers are compared as numbers.
    """
    a, b = str(a).strip(), str(b).strip()
    if a == b:
        return True
    try:
        return float(a.replace(",", "")) == float(b.replace(",", ""))
    except ValueError:
        return False


