"""Transformation logic — turns a raw Raya (batch, contact) pair into a
Supabase `kkb_mastersheet` row. No network calls here."""
import json
import re
from datetime import date, datetime
from typing import Any


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text).date()
    except Exception:
        return None


def call_date_of(call: dict[str, Any]) -> date | None:
    return parse_iso_date(call.get("call_start_time")) or parse_iso_date(call.get("created_at"))


def campaign_day_number(batch: dict[str, Any], call_date: date | None) -> int | None:
    """Day number of the campaign this call happened on, counting the
    batch's creation date as Day 1."""
    batch_start = parse_iso_date(batch.get("created_at"))
    if not batch_start or not call_date:
        return None
    return (call_date - batch_start).days + 1


def parse_json_field(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list, bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return text
    return value


def first_call(contact: dict[str, Any]) -> dict[str, Any]:
    calls = contact.get("calls") or []
    if not isinstance(calls, list):
        return {}
    for item in calls:
        if isinstance(item, dict):
            return item
    return {}


def safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def safe_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y"}:
            return True
        if v in {"false", "0", "no", "n"}:
            return False
    return bool(value)


EMPTY_STRING_SENTINELS = {"", "[]", "null", "na", "n/a", "none"}


def all_call_outputs(contact: dict[str, Any]) -> list[dict[str, Any]]:
    """Every attempt's call_output, newest first (Raya returns calls newest-first)."""
    outs: list[dict[str, Any]] = []
    for call in contact.get("calls") or []:
        if not isinstance(call, dict):
            continue
        co = call.get("call_output") or {}
        if isinstance(co, str):
            try:
                co = json.loads(co)
            except Exception:
                co = {}
        if isinstance(co, list):
            co = co[0] if co else {}
        if isinstance(co, dict) and co:
            outs.append(co)
    return outs


# values that mean "this attempt recorded nothing here"
_BLANK = (None, "", "NA", "na", "N/A", "[]", "null", "None")


def merged_output(contact: dict[str, Any]) -> dict[str, Any]:
    """The latest attempt, backfilled from earlier ones field by field.

    Reading only calls[0] loses what a seeker said when a LATER retry went
    unanswered: she answers and engages on attempt 1, attempts 2 and 3 ring
    out, and calls[0] is blank. Measured at ~0.5% of dialled contacts - small,
    but those are specifically people who engaged, so they are exactly the
    ones worth keeping.

    Newest value wins wherever the newest attempt actually has one; only
    genuinely empty fields fall through to an earlier attempt.
    """
    outs = all_call_outputs(contact)
    if len(outs) <= 1:
        return outs[0] if outs else {}
    merged = dict(outs[0])
    for older in outs[1:]:
        for key, value in older.items():
            if merged.get(key) in _BLANK and value not in _BLANK:
                merged[key] = value
    return merged


def job_list(value: Any) -> list[dict[str, Any]] | None:
    """Raya sends these job arrays as a list, a bare dict, a JSON string, or a
    placeholder like "NA". Normalise all of it to a list, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip().lower() in EMPTY_STRING_SENTINELS:
            return None
        try:
            value = json.loads(value)
        except Exception:
            return None
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else None


def jobs_failed_to_apply_nonempty(fails: Any) -> bool:
    """True only when jobs_failed_to_apply actually names failed jobs — not
    for empty/placeholder values like "NA", "null", "[]", or None."""
    if isinstance(fails, str):
        text = fails.strip()
        if text.lower() in EMPTY_STRING_SENTINELS:
            return False
        try:
            parsed = json.loads(text)
        except Exception:
            return True  # non-empty, non-placeholder string — treat as a signal
        return bool(parsed)
    return bool(fails)


def compute_intent_score(contact: dict[str, Any], call: dict[str, Any], call_output: dict[str, Any]) -> tuple[int, str]:
    """Derives an intent score (0-10) + reasoning from fields Raya actually
    returns (used for agents like KKB whose call_output has no native
    intent_score). Mirrors the old system's rubric:
      0  - not dialled yet, or call never connected
      9  - application completed
      7  - consented to apply but the apply attempt failed
      else - additive, duration + engagement weighted
    """
    # 1. Pending / not dialled
    if contact.get("status") == "Pending":
        return 0, "Not dialled — batch incomplete"

    # 2. Applied successfully
    if call_output.get("applied_to_job") == "Yes":
        return 9, "Application completed successfully"

    # 3. Consented to apply but apply failed
    if jobs_failed_to_apply_nonempty(call_output.get("jobs_failed_to_apply")):
        return 7, "Consented to apply, apply_job failed"

    # 4. Call didn't connect
    duration = safe_int(call.get("call_duration")) or 0
    if duration == 0:
        return 0, "Call did not connect"

    # 5. Everything else — additive duration-weighted rubric
    score = 0
    reasons = []

    if duration >= 10:
        score += 1
        reasons.append(">10s")
    if duration >= 30:
        score += 1
        reasons.append(">30s")
    if duration >= 60:
        score += 1
        reasons.append(">60s")
    if duration >= 120:
        score += 1
        reasons.append(">2min")

    if call_output.get("jobs_shown") == "Yes":
        score += 2
        reasons.append("jobs shown")
    if call_output.get("call_engaged") == "Yes":
        score += 1
        reasons.append("engaged")

    return min(10, score), "; ".join(reasons) if reasons else "Minimal engagement"


# A tool-result message following an apply_job tool call carries text like
# "[Error: apply_job request failed (HTTP 404).]" plus a "__RAYA_TOOL_DEBUG__"
# block with status_code=... when the apply-to-job API call itself failed.
APPLY_FAILURE_MARKERS = (
    "__raya_tool_debug__",
    "kind=http_error",
    "request failed",
    "[error:",
)


def _is_apply_job_tool(name: Any) -> bool:
    if not isinstance(name, str):
        return False
    lower = name.lower()
    return "apply" in lower and "job" in lower


def extract_apply_attempts(raw_transcript: Any) -> list[dict[str, Any]] | None:
    """Every apply_job tool call with its job_id and what actually came back.

    This is the evidence classify_apply_job_tool_calls() reads and then throws
    away. It is the ONLY place a failed apply's job_id exists when the bot
    omits the job from call_output.jobs_failed_to_apply - 81 successful applies
    had no job_id for exactly that reason. It also carries the real upstream
    error (PROFILE_NOT_LIVE, ACTION_LIMIT_REACHED), which the spoken transcript
    never mentions.

    Returns one entry per apply_job invocation, or None if there were none.
    """
    if not isinstance(raw_transcript, list):
        return None

    attempts: list[dict[str, Any]] = []
    for i, item in enumerate(raw_transcript):
        if not isinstance(item, dict):
            continue
        for tool_call in item.get("tool_calls") or []:
            fn = tool_call.get("function") or {}
            if not _is_apply_job_tool(fn.get("name")):
                continue
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if not isinstance(args, dict):
                args = {}

            result, ok = None, None
            for later in raw_transcript[i + 1:]:
                if not isinstance(later, dict) or later.get("role") != "tool":
                    continue
                result = str(later.get("content") or "")
                ok = not any(m in result.lower() for m in APPLY_FAILURE_MARKERS)
                break

            attempts.append({
                "job_id": args.get("job_id"),
                "profile_id": args.get("profile_id"),
                "ok": ok,
                # the machine-readable cause, e.g. PROFILE_NOT_LIVE - dug out
                # of the __RAYA_TOOL_DEBUG__ block rather than the prose
                "error": _apply_error(result) if ok is False else None,
                "result": (result or "")[:500],
            })
    return attempts or None


def _apply_error(result: str | None) -> str | None:
    """The upstream error code out of the tool result, if there is one."""
    if not result:
        return None
    m = re.search(r'"error"\s*:\s*"([^"]+)"', result)
    if m:
        return m.group(1)
    m = re.search(r"status_code=(\d+)", result)
    return f"HTTP {m.group(1)}" if m else None


def classify_apply_job_tool_calls(raw_transcript: Any) -> bool | None:
    """Reads the full per-call transcript (from /api/call/{uuid}, which
    includes tool_calls + their tool-role results) for apply_job invocations
    and classifies each by its actual result payload — not by guessing from
    the bot's spoken language. Returns True (at least one apply succeeded and
    none failed), False (at least one apply attempt failed), or None (no
    apply_job attempt found in this transcript)."""
    if not isinstance(raw_transcript, list):
        return None

    saw_attempt = False
    saw_failure = False
    for i, item in enumerate(raw_transcript):
        if not isinstance(item, dict):
            continue
        for tool_call in item.get("tool_calls") or []:
            fn = tool_call.get("function") or {}
            if not _is_apply_job_tool(fn.get("name")):
                continue
            saw_attempt = True
            for later in raw_transcript[i + 1:]:
                if not isinstance(later, dict) or later.get("role") != "tool":
                    continue
                content = str(later.get("content") or "").lower()
                if any(marker in content for marker in APPLY_FAILURE_MARKERS):
                    saw_failure = True
                break

    if not saw_attempt:
        return None
    return False if saw_failure else True


def extract_transcript_text(raw_transcript: Any) -> str | None:
    """Human-readable conversation text (user/assistant turns only — tool
    calls and their raw payloads are excluded) for the call_transcript column."""
    if not isinstance(raw_transcript, list):
        return None
    texts = []
    for item in raw_transcript:
        if isinstance(item, dict) and item.get("role") in ("user", "assistant"):
            content = item.get("content")
            if content:
                texts.append(str(content))
    return "\n".join(texts) if texts else None


def compute_apply_api_success(call_output: dict[str, Any], raw_transcript: Any) -> bool | None:
    """Whether an apply-to-job API call succeeded. Prefers reading the actual
    apply_job tool-call result in the transcript (ground truth); falls back to
    the structured call_output fields when no transcript/apply attempt is found."""
    from_transcript = classify_apply_job_tool_calls(raw_transcript)
    if from_transcript is not None:
        return from_transcript

    if call_output.get("applied_to_job") == "Yes":
        return True
    if jobs_failed_to_apply_nonempty(call_output.get("jobs_failed_to_apply")):
        return False
    return None


# Values the bot writes when it has nothing. Kept out of the application rows
# so "NA" never reaches a sheet or a report as if it were a company name.
NA_STRINGS = {"na", "n/a", "none", "null", "nan", "not available", "-", "--", "nil"}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in NA_STRINGS else (text or None)


def _first(*values: Any) -> str | None:
    for v in values:
        cleaned = _clean(v)
        if cleaned:
            return cleaned
    return None


def make_application_rows(
    batch: dict[str, Any],
    contact: dict[str, Any],
    raw_transcript: Any = None,
) -> list[dict[str, Any]]:
    """One row per job this call applied to - succeeded or failed.

    A different grain from make_row: one call can apply to several jobs, so
    this returns a list. Same pattern as transform_dkb.make_dkb_newjob_row,
    which produces the dkb_newjobs children.

    Three sources, in order of trust:
      1. recommendations_input - the catalogue the bot was HANDED for this
         call. Best job detail, and the only thing that resolves the synthetic
         "<provider phone>_<role>" ids that come from job feed entries with no
         Blue Dot uuid. Resolves 1,711 of 1,718 attempted ids.
      2. jobs_applied / jobs_failed_to_apply - the bot's own summary.
      3. apply_attempts from the transcript - job_id only, but it is the ONLY
         record for 81 successful and 249 failed applies the bot never listed.
    """
    call_id = contact.get("contact_id")
    if call_id is None:
        return []

    call_output = merged_output(contact)
    call = first_call(contact)
    offered = {}
    reco = parse_json_field(contact.get("agent_args", {}).get("recommendations")
                            if isinstance(contact.get("agent_args"), dict) else None)
    for job in (reco or []):
        if isinstance(job, dict) and job.get("job_id"):
            offered[str(job["job_id"])] = job

    applied = job_list(call_output.get("jobs_applied")) or []
    failed = job_list(call_output.get("jobs_failed_to_apply")) or []
    attempts = extract_apply_attempts(raw_transcript) or []

    if not applied and not failed:
        # nothing summarised - fall back to the tool calls themselves
        for a in attempts:
            if not isinstance(a, dict) or not a.get("job_id"):
                continue
            job = {"job_id": a["job_id"]}
            if a.get("ok"):
                applied.append(job)
            else:
                failed.append(dict(job, failure_reason=a.get("error") or "api_failed"))

    # error per job, from the tool result - more precise than the bot's prose
    errors = {str(a.get("job_id")): a.get("error")
              for a in attempts if isinstance(a, dict) and a.get("job_id")}

    rows: list[dict[str, Any]] = []
    for jobs, ok in ((applied, True), (failed, False)):
        for job in jobs:
            if not isinstance(job, dict):
                continue
            jid = str(job.get("job_id") or "").strip()
            if not jid:
                continue
            shown = offered.get(jid, {})
            # a synthetic id is "<provider phone>_<role>"; that phone is the
            # provider's and is otherwise absent from these rows
            head = jid.split("_", 1)[0] if "_" in jid else ""
            rows.append({
                "call_id": str(call_id),
                "job_id": jid,
                "applied": ok,
                "failure_reason": None if ok else _first(
                    errors.get(jid), job.get("failure_reason"), "api_failed"),
                "synthetic_job_id": not _looks_like_uuid(jid),
                # campaign_name / campaign_date / jfc_campaign / seeker_name /
                # phone are NOT stored: they live on kkb_mastersheet under the
                # same call_id and are joined back by kkb_applications_full.
                "job_role": _first(shown.get("role"), job.get("role")),
                "company_name": _first(shown.get("company"), job.get("company_name")),
                "provider_phone": head if head.isdigit() else None,
                "job_location": _first(shown.get("location"), job.get("company_location")),
                "vacancies": _first(shown.get("vacancy")),
                "salary": _first(shown.get("salary"), job.get("salary_offered")),
                "qualification_required": _first(shown.get("qualification"),
                                                 job.get("qualification_required")),
            })
    return rows


def _looks_like_uuid(value: str) -> bool:
    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                         r"[0-9a-f]{4}-[0-9a-f]{12}$", value, re.I))


# Raya carries seven spellings of five colleges - "MMH College" and "MMH
# College, Ghaziabad" are one place, and "LR College" and "Lajpat Rai College
# Sahibabad" are another. Normalised to one full name each so a summary groups
# correctly; an unrecognised value is passed through untouched rather than
# dropped, so a new college shows up instead of silently vanishing.
COLLEGE_NAMES = {
    "mmh college": "MMH College, Ghaziabad",
    "mmh college, ghaziabad": "MMH College, Ghaziabad",
    "lr college": "Lajpat Rai College, Sahibabad",
    "lajpat rai college sahibabad": "Lajpat Rai College, Sahibabad",
    "vmlg college": "VMLG College, Ghaziabad",
    "multanimal modi college modinagar": "Multanimal Modi College, Modinagar",
    # the college is in Modinagar, not Ghaziabad. Both wrong spellings appear
    # on the HE output sheet - 55 rows between them - and split what is one
    # college into three in every per-college count.
    "multanimal modi college, modinagar": "Multanimal Modi College, Modinagar",
    "multanimal modi college": "Multanimal Modi College, Modinagar",
    "multanimal modi college, ghaziabad": "Multanimal Modi College, Modinagar",
    "multanimal modi college ghaziabad": "Multanimal Modi College, Modinagar",
    "manyawar kanshiram government college": "Manyawar Kanshiram Government College",
}


def normalise_college(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    return COLLEGE_NAMES.get(text.strip().lower(), text.strip())


def inbound_as_contact(call: dict[str, Any]) -> dict[str, Any]:
    """Reshape a /api/call/{uuid} record into the contact shape make_row wants.

    An inbound call has no contact record and no batch — the caller simply
    rang in — but the call_output it produces has exactly the same fields as
    an outbound one. Adapting the shape means inbound goes through the same
    intent scoring, apply-attempt extraction and job parsing as everything
    else, rather than through a parallel copy that would drift.

    call_id becomes the call's uuid, because there is no contact_id to use.
    That is a visible difference in the table - outbound call_ids are short
    numeric strings - and it is the honest one: these rows are keyed on the
    only identifier Raya gives them.
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


def make_inbound_row(
    call: dict[str, Any],
    campaign_name: str,
    jfc: str | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """A kkb_mastersheet row for one inbound call."""
    row = make_row(
        {},                                   # no batch
        inbound_as_contact(call),
        raw_transcript=call.get("call_transcript"),
        campaign_name_override=campaign_name,
        jfc=jfc,
        agent_id=agent_id,
        agent_name=agent_name,
        recording_url=call.get("call_recording_url"),
    )
    # make_row hard-codes Outbound, which is right for everything reached
    # through a batch and wrong for exactly these rows
    row["channel"] = "Inbound"
    row["batch_id"] = None
    row["campaign_day"] = None
    return row


def make_row(
    batch: dict[str, Any],
    contact: dict[str, Any],
    raw_transcript: Any = None,
    campaign_name_override: str | None = None,
    jfc: str | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    recording_url: str | None = None,
) -> dict[str, Any]:
    """raw_transcript: the full call_transcript list from Raya's per-call
    detail endpoint (/api/call/{uuid}) — includes tool_calls/tool results, not
    just spoken text. Optional; pass it when the caller has already fetched it
    (see pipeline.build_rows_for_batches) so apply_api_success can be read from
    the actual apply_job tool result rather than guessed.

    campaign_name_override: the real campaign name from the callids_to_campaignname
    sheet (see pipeline.load_campaign_name_mapping), looked up by this call's
    uuid — Raya's own batch name is a meaningless auto-generated codename
    ("Steady Cinder 182-UP"), not the actual campaign name. Falls back to the
    Raya batch name when there's no match in the sheet.

    jfc: which Job Facilitation Centre this bot serves ("Ghaziabad" or
    "Hubli-Dharwad"), from pipeline.AGENT_JFC. Goes in jfc_campaign. Left
    empty for an unmapped agent rather than guessed.
    """
    batch_name = batch.get("name") or ""
    campaign_name = campaign_name_override or batch_name
    batch_id = batch.get("id")
    call = first_call(contact)
    call_date = call_date_of(call)
    # the latest attempt, with blanks filled in from earlier attempts
    call_output = merged_output(contact)
    agent_args = contact.get("agent_args") or {}

    recommendations_raw = agent_args.get("recommendations")
    recommendations_input = parse_json_field(recommendations_raw)

    jobs_recommended = job_list(call_output.get("jobs_recommended"))
    jobs_applied = job_list(call_output.get("jobs_applied"))
    # Carries a per-job failure_reason (e.g. ACTION_LIMIT_REACHED), which is the
    # only place the CAUSE of a failed apply is recorded. apply_api_success says
    # an apply failed; this says why, and for which job.
    jobs_failed_to_apply = job_list(call_output.get("jobs_failed_to_apply"))

    phone = contact.get("phone") or contact.get("caller_no") or ""
    name = contact.get("name") or ""
    transcript_text = extract_transcript_text(raw_transcript)
    intent_score, intent_score_reasoning = compute_intent_score(contact, call, call_output)
    apply_api_success = compute_apply_api_success(call_output, raw_transcript)
    updated = {
        "campaign_name": campaign_name,
        "jfc_campaign": jfc,
        "agent_id": str(agent_id) if agent_id else None,
        "agent_name": agent_name,
        # Every contact reached through a batch was dialled out to. Inbound
        # calls don't come through batches at all (they have no contact
        # record), so anything built here is Outbound by definition.
        "channel": "Outbound",
        # Which college this seeker belongs to, from agent_args - what we told
        # the bot, not what it heard. Two HE campaigns cover LR and VMLG under
        # one campaign_name, so this is the only way to split them.
        "college_name": normalise_college(agent_args.get("college_name")),
        "bot_language": None,
        "test_flag": None,
        "phone_number": str(phone) if phone is not None else None,
        # Raya's batch id. source_id is deliberately NOT written here — it is
        # reserved for another purpose.
        "batch_id": str(batch_id) if batch_id is not None else None,
        "seeker_name": name,
        "recommendations_input": recommendations_input,
        "call_duration_seconds": safe_float(call.get("call_duration")),
        # only present on /api/call/{uuid}, never on the batch contacts list
        "call_recording_url": recording_url or call.get("call_recording_url") or None,
        "call_transcript": transcript_text,
        "call_summary": call_output.get("final_summary") or call_output.get("call_summary") or None,
        "call_outcome": contact.get("status") or call_output.get("outcome") or None,
        "drop_reasom": call_output.get("drop_reason") or None,
        "call_answered": safe_bool(call_output.get("call_answered")),
        "call_engaged": safe_bool(call_output.get("call_engaged")),
        "create_profile_api_success": None,
        "jobs_shown": bool(jobs_recommended) if jobs_recommended is not None else None,
        "jobs_mismatch": None,
        "applied_to_job": safe_bool(call_output.get("applied_to_job")),
        "apply_api_success": apply_api_success,
        "needs_mentioned": None,
        "needs_details": None,
        "applications_count": safe_int(call_output.get("applications_count")),
        "intent_score": float(intent_score),
        "intent_score_reasoning": intent_score_reasoning,
        "jobs_recommended": jobs_recommended,
        "jobs_applied": jobs_applied,
        "jobs_failed_to_apply": jobs_failed_to_apply,
        # the tool-call evidence itself, so a failed apply's job_id and its
        # real cause survive past push time
        "apply_attempts": extract_apply_attempts(raw_transcript),
        "profile_completeness": safe_float(call_output.get("profile_completeness")),
        "profile_correctness": safe_float(call_output.get("profile_correctness")),
        "new_seeker_id": None,
        "phone": str(phone) if phone is not None else None,
        "name": name,
        "gender": None,
        "role": None,
        "workExperience": None,
        "workExperienceYears": None,
        "highestQualification": None,
        "natureOfJobsInterestedIn": None,
        "campaign_day": campaign_day_number(batch, call_date),
        "call_id": str(contact.get("contact_id")) if contact.get("contact_id") is not None else None,
        "campaign_date": call_date.isoformat() if call_date else None,
    }

    if call_output.get("drop_reason"):
        updated["drop_reasom"] = call_output.get("drop_reason")

    # parse memory if present for nicer fields
    memory = agent_args.get("contact_memory")
    if isinstance(memory, str):
        lower_memory = memory.lower()
        if "gender" in lower_memory:
            updated["gender"] = memory.split("Gender")[-1].split(":")[-1].strip() if ":" in memory else None

    if isinstance(recommendations_raw, str):
        cleaned = recommendations_raw.strip()
        if cleaned.startswith("["):
            try:
                updated["recommendations_input"] = json.loads(cleaned)
            except Exception:
                updated["recommendations_input"] = recommendations_raw

    return updated
