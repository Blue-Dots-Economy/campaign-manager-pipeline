"""Transformation logic for DKB (employer-facing) calls — turns a raw Raya
(batch, contact) pair into a Supabase `dkb_mastersheet` row. No network calls.

DKB differs from KKB in two important ways:

1. The authoritative job data lives in `contact.agent_args` (what we told the
   bot about the job), NOT in `call_output`. The `*_input` columns come from
   there. The `*_value` fields in call_output are what the employer actually
   confirmed on the call, and land in the `updated_*` columns.

2. call_output uses placeholder strings for "no value", and the two DKB bots
   disagree on which: the Hindi bot writes "unknown", the Kannada bot writes
   "NA", and agent_args uses "Not Available". All of them mean empty.
"""
import json
from datetime import date, datetime, timedelta
from typing import Any

from transform import first_call, parse_iso_date, safe_int

# every way the DKB bots spell "nothing here"
DKB_EMPTY = {"", "na", "n/a", "none", "null", "unknown", "not available", "notavailable", "-"}


def clean(value: Any) -> str | None:
    """Text value, or None if it's one of the bots' empty placeholders."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in DKB_EMPTY else text


def yes_no(value: Any) -> bool | None:
    text = clean(value)
    if text is None:
        return None
    lower = text.lower()
    if lower in ("yes", "true", "1"):
        return True
    if lower in ("no", "false", "0"):
        return False
    return None


def whole_number(value: Any) -> int | None:
    """Integer, or None if the text isn't a plain number. The updated_* count
    columns are bigint, so "2 vacancies" has to become None rather than fail
    the whole insert."""
    text = clean(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def phase_number(value: Any) -> int | None:
    """call_output says "Phase 1"; the column stores 1."""
    text = clean(value)
    if text is None:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def ist_datetime(call: dict[str, Any]) -> str | None:
    """Raya timestamps are UTC; the sheet and this column are IST (UTC+5:30)."""
    raw = call.get("call_start_time") or call.get("created_at")
    if not raw:
        return None
    try:
        utc = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None
    return (utc + timedelta(hours=5, minutes=30)).isoformat()


def call_date_ist(call: dict[str, Any]) -> date | None:
    raw = call.get("call_start_time") or call.get("created_at")
    if not raw:
        return None
    try:
        utc = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None
    return (utc + timedelta(hours=5, minutes=30)).date()


def dkb_transcript(raw_transcript: Any) -> str | None:
    """Formats a transcript the way the existing dkb_mastersheet rows store it:
    a JSON string of {speaker, content}, with Raya's role names translated to
    the DKB vocabulary (user -> Employer, assistant -> Bot). Tool calls and
    their payloads are dropped."""
    if not isinstance(raw_transcript, list):
        return None
    speakers = {"user": "Employer", "assistant": "Bot"}
    turns = []
    for item in raw_transcript:
        if not isinstance(item, dict):
            continue
        speaker = speakers.get(item.get("role"))
        content = item.get("content")
        if speaker and content:
            turns.append({"speaker": speaker, "content": str(content)})
    return json.dumps(turns, ensure_ascii=False) if turns else None


# (input field in agent_args, confirmed field in call_output, column to write)
UPDATE_FIELDS = (
    ("job_role",      "job_role_value",      "updated_job_role"),
    ("num_vacancies", "num_vacancies_value", "updated_vacancies"),
    ("salary",        "salary_value",        "updated_salary"),
    ("location",      "location_value",      "updated_location"),
    ("qualification", "qualification_value", "updated_qualification"),
)


# --- intent score -------------------------------------------------------
# Scores how strong an engagement / hiring signal the provider gave, 0-10.
# Fractional scores (0.5 steps) are deliberate and preserved — the column is
# numeric, verified to round-trip 8.5.

def compute_dkb_intent_score(
    call_outcome: str | None,
    job_status: str | None,
    new_job_mentioned: bool | None,
    new_job_posted: bool | None,
    duration: float | None,
    fields_updated: int | None,
) -> tuple[float, str]:
    """Returns (score 0-10, reasoning). Exclusions score a hard 0; everything
    else is additive and capped at 10."""
    outcome = (call_outcome or "").strip()
    status = (job_status or "").strip()
    seconds = int(duration or 0)

    # ---- exclusions -> hard 0 ----
    if outcome == "No Answer" or status in ("Not Called", "Pending"):
        return 0.0, "No Answer / Not Called (+0)"
    if status == "Closed" and not new_job_mentioned:
        return 0.0, "Job confirmed closed, no new opening (+0)"

    # ---- additive signals ----
    score = 0.0
    parts: list[str] = []

    if seconds >= 180:
        score += 3
        parts.append(f"duration {seconds}s (+3)")
    elif seconds >= 90:
        score += 2
        parts.append(f"duration {seconds}s (+2)")
    elif seconds >= 30:
        score += 1
        parts.append(f"duration {seconds}s (+1)")
    else:
        parts.append(f"duration {seconds}s (+0)")

    if status == "Active":
        score += 2
        parts.append("job confirmed Active (+2)")

    if new_job_mentioned:
        score += 2
        parts.append("new job mentioned (+2)")
        if new_job_posted:
            score += 1
            parts.append("new job posted (+1)")

    if outcome == "Callback Requested":
        score += 0.5
        parts.append("callback requested (+0.5)")

    if (fields_updated or 0) > 0:
        score += 0.5
        parts.append("fields updated (+0.5)")

    return min(score, 10.0), " | ".join(parts)


def dkb_score_bucket(score: float | None) -> str:
    if score is None:
        return "n/a"
    if score == 0:
        return "0 (excluded)"
    if score <= 2:
        return "0.1-2 (low)"
    if score <= 5:
        return "2.1-5 (some)"
    if score <= 8:
        return "5.1-8 (good)"
    return "8.1-10 (high)"


def all_call_outputs(contact: dict[str, Any]) -> list[dict[str, Any]]:
    """Every attempt's call_output, newest first."""
    outs = []
    for call in contact.get("calls") or []:
        if not isinstance(call, dict):
            continue
        co = call.get("call_output") or {}
        if isinstance(co, list):
            co = co[0] if co else {}
        if isinstance(co, dict) and co:
            outs.append(co)
    return outs


# values that mean "this attempt recorded nothing here"
_DKB_BLANK = (None, "", "NA", "na", "unknown", "Unknown", "[]", "null", "None", "Not Available")


def merged_output(contact: dict[str, Any]) -> dict[str, Any]:
    """The latest attempt, backfilled from earlier ones field by field.

    Same problem from_any_call solves for campaign fields, applied to the rest
    of the payload: when a final retry rings out, calls[0] is blank and
    whatever the employer actually said on an earlier attempt is discarded.
    The newest real value always wins; only empty fields fall through.
    """
    outs = all_call_outputs(contact)
    if len(outs) <= 1:
        return outs[0] if outs else {}
    merged = dict(outs[0])
    for older in outs[1:]:
        for key, value in older.items():
            if merged.get(key) in _DKB_BLANK and value not in _DKB_BLANK:
                merged[key] = value
    return merged


def from_any_call(contact: dict[str, Any], key: str) -> str | None:
    """First non-empty value of `key` across all attempts.

    Used for fields that describe the campaign rather than the call —
    campaign_type, city_campaign, language. Reading only the latest attempt
    loses them whenever the final retry rang out, which was leaving
    campaign_name null on 85% of rows.
    """
    for co in all_call_outputs(contact):
        value = clean(co.get(key))
        if value is not None:
            return value
    return None


def make_dkb_newjob_row(contact: dict[str, Any]) -> dict[str, Any] | None:
    """The dkb_newjobs child row for a contact that surfaced a new job, or
    None. One row per call, keyed on the same call_id as the parent."""
    call_id = contact.get("contact_id")
    if call_id is None:
        return None
    for co in all_call_outputs(contact):
        if not yes_no(co.get("new_job_mentioned")):
            continue
        row = {
            "call_id": str(call_id),
            "new_job_role": clean(co.get("new_job_role")),
            "new_vacancies": clean(co.get("new_job_vacancies")),
            "new_salary": clean(co.get("new_job_salary")),
            "new_location": clean(co.get("new_job_location")),
            "new_qualification": clean(co.get("new_job_qualification")),
        }
        if any(v for k, v in row.items() if k != "call_id"):
            return row
    return None


def make_dkb_row(
    batch: dict[str, Any],
    contact: dict[str, Any],
    raw_transcript: Any = None,
    recording_url: str | None = None,
    jfc: str | None = None,
) -> dict[str, Any]:
    call = first_call(contact)
    # the latest attempt, with blanks filled in from earlier attempts
    call_output = merged_output(contact)
    args = contact.get("agent_args") or {}
    if not isinstance(args, dict):
        args = {}

    cdate = call_date_ist(call)
    batch_start = parse_iso_date(batch.get("created_at"))
    # campaign_day is NOT NULL in dkb_mastersheet, and a contact that was never
    # dialled has no call date to measure from — treat it as day 1 of the batch.
    campaign_day = (cdate - batch_start).days + 1 if (cdate and batch_start) else 1

    # how many job fields the employer actually changed
    updated: dict[str, Any] = {}
    changed = 0
    for arg_key, out_key, column in UPDATE_FIELDS:
        confirmed = clean(call_output.get(out_key))
        # vacancies is a numeric column; the other confirmed values are text
        updated[column] = whole_number(confirmed) if column == "updated_vacancies" else confirmed
        original = clean(args.get(arg_key))
        if confirmed is not None and confirmed != original:
            changed += 1

    new_job_posted = yes_no(call_output.get("new_job_posted"))
    new_job_mentioned = yes_no(call_output.get("new_job_mentioned"))
    job_status = clean(call_output.get("job_status"))
    outcome = clean(call_output.get("call_outcome")) or clean(call.get("outcome")) or clean(contact.get("status"))
    # call_duration_seconds is a bigint column — a float like 19.0 is rejected
    duration = safe_int(call.get("call_duration"))
    intent_score, intent_reasoning = compute_dkb_intent_score(
        outcome, job_status, new_job_mentioned, new_job_posted, duration, changed,
    )
    phone = contact.get("phone") or clean(call_output.get("contact_phone"))

    row = {
        # identity
        "call_id": str(contact.get("contact_id")) if contact.get("contact_id") is not None else None,
        "contact_phone": str(phone) if phone else None,
        "job_id": clean(args.get("job_id")) or clean(call_output.get("job_id")),
        "company_name": clean(args.get("company_name")) or clean(call_output.get("company_name")),
        "source_id": None,          # reserved, same as kkb_mastersheet
        "test_flag": None,

        # campaign
        "campaign_name": from_any_call(contact, "campaign_type"),
        "campaign_date": (cdate or batch_start).isoformat() if (cdate or batch_start) else None,
        "campaign_day": campaign_day,
        "jfc_campaign": jfc,

        # what we told the bot about the job. The *_input values are the
        # platform's existing record; agent_args is authoritative, with
        # call_output's own *_input as a fallback.
        "num_vacancies_input": clean(args.get("num_vacancies")) or clean(call_output.get("num_vacancies_input")),
        "contact_name": clean(call_output.get("contact_name")),
        "bot_language": clean(call_output.get("language")),
        "contact_attempts": whole_number(call_output.get("contact_attempts")) or safe_int(contact.get("attempts")),
        "job_role_input": clean(args.get("job_role")),
        "salary_input": clean(args.get("salary")),
        "location_input": clean(args.get("location")),
        "qualification_input": clean(args.get("qualification")),
        "city_input": clean(args.get("city")),

        # the call itself
        "call_datetime_ist": ist_datetime(call),
        "call_duration_seconds": duration,
        "call_duration": None,      # unused column
        "call_outcome": outcome,
        "call_status": clean(call_output.get("call_status")),
        "call_recording_url": recording_url,
        "call_summary": clean(call_output.get("final_summary")),
        "call_transcript": dkb_transcript(raw_transcript),
        "drop_reason": clean(call_output.get("drop_reason")),
        "phases_reached": phase_number(call_output.get("phases_reached")),

        # what the employer confirmed
        "update_job_status": job_status,
        "fields_updated": changed,

        # new jobs surfaced on the call
        "new_job_mentioned": new_job_mentioned,
        "new_job_posted": new_job_posted,
        "total_jobs_posted": 1 if new_job_posted else 0,

        # nothing in Raya's payload supplies these
        "other_needs": None,
        "other_needs_mentioned": None,
        "update_api_success": None,
        "update_completeness": None,
        "update_correctness": None,

        "intent_score": intent_score,
        "intent_score_reasoning": intent_reasoning,
    }
    row.update(updated)
    return row


def dkb_inbound_as_contact(call: dict[str, Any]) -> dict[str, Any]:
    """Reshape a /api/call/{uuid} record into the contact shape make_dkb_row
    wants, so an inbound employer call goes through the same logic as an
    outbound one instead of a parallel copy that would drift.

    call_id becomes the call's uuid: an inbound call has no contact record, so
    there is no contact_id to key on.
    """
    return {
        "contact_id": call.get("uuid"),
        "phone": call.get("caller_no"),
        "caller_no": call.get("caller_no"),
        "name": "",
        "agent_args": call.get("agent_args") or {},
        "status": call.get("outcome"),
        # one attempt by definition: nobody retries a call that came to them
        "calls": [call],
    }


def make_dkb_inbound_row(
    call: dict[str, Any],
    campaign_name: str,
    jfc: str | None = None,
) -> dict[str, Any]:
    """A dkb_mastersheet row for one inbound employer call."""
    row = make_dkb_row(
        {},                                   # no batch
        dkb_inbound_as_contact(call),
        raw_transcript=call.get("call_transcript"),
        recording_url=call.get("call_recording_url"),
        jfc=jfc,
    )
    row["channel"] = "Inbound"
    # campaign_day is NOT NULL and counts days from the batch start. There is
    # no batch, so make_dkb_row already falls back to 1; naming it here so the
    # 1 is read as "not applicable" rather than "first day of a campaign".
    row["campaign_day"] = 1
    row["campaign_name"] = campaign_name
    return row
