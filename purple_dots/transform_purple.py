"""Turns one Raya call into one purple_dots_calls row. No network calls.

Purple Dots is disability support, not jobs, so nothing here is shared with
the Blue Dots transforms - there is no seeker, no employer, no job and no
intent score. What it does share is the discipline: the row records what the
bot actually said, and the output sheet's presentation happens at the sheet
layer, not here.

The bots write placeholder strings for "no value" - "NA" most often - and
those become None rather than the literal text, so a rate computed over this
table is not quietly counting "NA" as an answer.
"""
import ast
import json
from datetime import date, datetime, timedelta
from typing import Any

# every way these bots spell "nothing here"
EMPTY = {"", "na", "n/a", "none", "null", "unknown", "not available",
         "notavailable", "-", "nil", "[]"}

IST = timedelta(hours=5, minutes=30)

# /api/call reports "Failure" for a call that never connected; the batch
# contact calls the same call "Unanswered". One column, one vocabulary.
OUTCOME_AS_STATUS = {"Failure": "Unanswered"}


def clean(value: Any) -> str | None:
    """Text, or None if it is one of the bots' empty placeholders."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in EMPTY else text


def yes_no(value: Any) -> bool | None:
    """Yes/No to a bool. None means the bot never answered, which is NOT the
    same as No - it is the difference between "we asked and they said no" and
    "the call never got that far"."""
    text = clean(value)
    if text is None:
        return None
    lower = text.lower()
    if lower in ("yes", "true", "1"):
        return True
    if lower in ("no", "false", "0"):
        return False
    return None


def whole(value: Any) -> int | None:
    text = clean(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def number(value: Any) -> float | None:
    text = clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def str_list(value: Any) -> list[str] | None:
    """The bots send these as a Python-repr string: "['Hearing Impairment']".

    JSON cannot parse single quotes, so json.loads alone drops the lot. Tried
    literal_eval first and it chokes on the unescaped apostrophes that turn up
    in free-text categories, so this falls back to splitting.
    """
    if value is None:
        return None
    if isinstance(value, list):
        items = [str(x).strip() for x in value if clean(x)]
        return items or None
    text = clean(value)
    if text is None:
        return None
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text.replace("'", '"'))
            if isinstance(parsed, list):
                items = [str(x).strip() for x in parsed if clean(x)]
                return items or None
        except Exception:
            pass
        text = text[1:-1]
    # the sheet writes them pipe-separated; the bot sometimes uses commas
    sep = "|" if "|" in text else ","
    items = [p.strip().strip("'\"") for p in text.split(sep)]
    items = [p for p in items if clean(p)]
    return items or None


def _as_dict(value: Any) -> dict[str, Any]:
    """A dict the bot sent as either real JSON or a Python repr.

    item_state arrives as "{'age': 30, 'address': 'Lucknow', ...}" - single
    quotes, so json.loads alone returns nothing. literal_eval handles the
    repr form; the quote-swap handles genuine JSON that got single-quoted.
    """
    if isinstance(value, dict):
        return value
    text = value.strip() if isinstance(value, str) else ""
    if not text.startswith("{"):
        return {}
    for attempt in (lambda: json.loads(text),
                    lambda: ast.literal_eval(text),
                    lambda: json.loads(text.replace("'", '"'))):
        try:
            got = attempt()
            if isinstance(got, dict):
                return got
        except Exception:
            continue
    return {}


# Identifiers only. The profile tools also carry beneficiary_name, age,
# gender, address, documents_available, disability_type and
# disability_percentage - we deliberately do NOT read them. The platform
# already holds that record; copying it into a second database would put a
# named person's disability status somewhere it does not need to be. The id
# is enough to join back when someone genuinely needs the detail.
PROFILE_FIELDS = ("item_id", "user_id", "acting_as_user_id")


def profile_from_tools(raw_transcript: Any) -> dict[str, Any]:
    """The platform ids for this beneficiary, from the profile tool calls.

    update_profile carries the whole profile, personal details included. We
    take the item_id and user_id and leave the rest where it is.

    A conversation can call update_profile several times as the bot learns
    more, and a later call corrects an earlier one. So the LAST non-empty
    value wins per field - the same rule as merged_output on the Blue Dots
    side, for the same reason: a field the bot revised should not be
    overwritten by the earlier guess, and a field a later call omitted should
    not be wiped.
    """
    found: dict[str, Any] = {}
    if not isinstance(raw_transcript, list):
        return found
    for item in raw_transcript:
        if not isinstance(item, dict):
            continue
        for tc in (item.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if fn.get("name") not in ("update_profile", "connect_provider"):
                continue
            args = _as_dict(fn.get("arguments"))
            merged = {**_as_dict(args.get("item_state")), **args}
            for key in PROFILE_FIELDS:
                value = merged.get(key)
                if value not in (None, "", [], {}) and clean(value) is not None:
                    found[key] = value
    return found


def call_output_of(call: dict[str, Any]) -> dict[str, Any]:
    co = call.get("call_output") or {}
    if isinstance(co, str):
        try:
            co = json.loads(co)
        except Exception:
            co = {}
    return co if isinstance(co, dict) else {}


def _utc(call: dict[str, Any]) -> datetime | None:
    raw = call.get("call_start_time") or call.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def call_date_ist(call: dict[str, Any]) -> date | None:
    utc = _utc(call)
    return (utc + IST).date() if utc else None


def call_datetime_ist(call: dict[str, Any]) -> str | None:
    """Raya timestamps are UTC; the sheet and this column are IST."""
    utc = _utc(call)
    return (utc + IST).isoformat() if utc else None


def is_inbound(call: dict[str, Any]) -> bool:
    """Raya records no direction, so it is inferred: an inbound call has the
    caller's number in caller_no and nothing in to_number."""
    return bool(call.get("caller_no")) and not call.get("to_number")


def transcript_text(raw: Any) -> str | None:
    """The spoken conversation only. Tool calls and their payloads are
    dropped - the tools here are reference lookups (Disabilitytypes,
    DisabilitySchemes, ProvidersList) that carry no answer worth keeping."""
    if not isinstance(raw, list):
        return None
    turns = []
    for item in raw:
        if isinstance(item, dict) and item.get("role") in ("user", "assistant"):
            content = item.get("content")
            if content:
                turns.append(f"{item['role']}: {content}")
    return "\n".join(turns) if turns else None


def tools_used(raw: Any) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    names = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        for tc in (item.get("tool_calls") or []):
            name = ((tc.get("function") or {}).get("name")
                    if isinstance(tc, dict) else None)
            if name and name not in names:
                names.append(name)
    return names or None


def make_connections(call: dict[str, Any], call_id: str) -> list[dict[str, Any]]:
    """One row per provider connection made on this call.

    A call that connects usually connects to TWO providers - 8 of the 9 that
    got that far did - and the call row can only hold a count. The provider
    ids live in the connect_provider tool call and nowhere else, so without
    this the only record of who a beneficiary was actually put in touch with
    is the number 2.

    Same shape as kkb_applications on the Blue Dots side, for the same
    reason: the count belongs on the call, the relationships belong in their
    own table.
    """
    rows, seen = [], set()
    for item in (call.get("call_transcript") or []):
        if not isinstance(item, dict):
            continue
        for tc in (item.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if fn.get("name") != "connect_provider":
                continue
            args = _as_dict(fn.get("arguments"))
            source = _as_dict(args.get("source_item"))
            target = _as_dict(args.get("target_item"))
            consent = _as_dict(args.get("consent"))
            provider = clean(target.get("item_id"))
            if not provider or (call_id, provider) in seen:
                continue
            seen.add((call_id, provider))
            rows.append({
                "call_id": call_id,
                "seeker_item_id": clean(source.get("item_id")),
                "provider_item_id": provider,
                "acting_as_user_id": clean(args.get("acting_as_user_id")),
                # which deployment these ids resolve against, so a bare uuid
                # is still resolvable a year from now
                "provider_instance_url": clean(target.get("item_instance_url")),
                "consent_acknowledged": consent.get("acknowledged") is True,
                "consent_version": whole(consent.get("version")),
            })
    return rows


def make_row(
    call: dict[str, Any],
    campaign_name: str | None = None,
    agent_name: str | None = None,
    batch: dict[str, Any] | None = None,
    contact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One purple_dots_calls row.

    call     a /api/call/{uuid} record
    contact  the batch-contacts entry, when the call came through a batch.
             Purple Dots has none in the Raya account we can see, so this is
             normally None - but that is where section A of the sheet
             (contact_name, the referring support centre) would come from, so
             the parameter exists rather than the shape being assumed away.
    """
    contact = contact or {}
    batch = batch or {}
    out = call_output_of(call)
    args = call.get("agent_args") or {}
    if not isinstance(args, dict):
        args = {}
    raw_transcript = call.get("call_transcript")
    # section E lives here, not in call_output
    prof = profile_from_tools(raw_transcript)

    # The uuid, always. contact_id looked like the better key - it is what
    # the Blue Dots tables use - but a contact with two attempts produces TWO
    # calls sharing one contact_id, and this table is one row per CALL. It is
    # kept alongside as its own column so a batch contact can still be joined.
    cid = contact.get("contact_id")
    call_id = str(call.get("uuid"))

    phone = (contact.get("phone") or args.get("contact_phone")
             or prof.get("mobile_number")
             or call.get("caller_no") or call.get("to_number"))

    return {
        "call_id": call_id,
        "call_uuid": str(call.get("uuid")) if call.get("uuid") else None,
        "contact_id": str(cid) if cid is not None else None,
        "batch_id": str(batch.get("id")) if batch.get("id") is not None else None,
        "campaign_name": clean(campaign_name) or clean(batch.get("name")),
        "agent_id": str(call.get("agent_id")) if call.get("agent_id") else None,
        "agent_name": clean(agent_name),
        "persona": clean(args.get("persona")),
        "channel": "Inbound" if is_inbound(call) else "Outbound",

        # A - PASSTHROUGH. The referring organisation only. contact_name and
        # contact_phone are deliberately absent: see the module docstring.
        "contact_reference_type": clean(args.get("contact_reference_type")),
        "contact_reference": clean(args.get("contact_reference")),

        # B - CALL METADATA
        "call_date_ist": (lambda d: d.isoformat() if d else None)(call_date_ist(call)),
        "call_datetime_ist": call_datetime_ist(call),
        "call_duration_seconds": number(call.get("call_duration")),
        "contact_attempts": len(contact.get("calls") or []) or 1,

        # C - CALL OUTCOME
        # The contact's status wins over the call's outcome. They disagree on
        # exactly the calls nobody picked up: the batch contact says
        # "Unanswered", the per-call endpoint says "Failure" for the same call.
        # The master sheet uses the contact's wording, and a batch-less call
        # has no contact, so OUTCOME_AS_STATUS maps the one onto the other
        # rather than leaving the column speaking two vocabularies.
        "call_status": (clean(contact.get("status"))
                        or OUTCOME_AS_STATUS.get(clean(call.get("outcome")),
                                                 clean(call.get("outcome")))),
        "call_answered": yes_no(out.get("call_answered")),
        "call_engaged": yes_no(out.get("call_engaged")),
        "call_dropped_abruptly": yes_no(out.get("call_dropped_abruptly")),
        "full_journey_completed": yes_no(out.get("full_journey_completed")),
        "abandoned_at_stage": clean(out.get("abandoned_at_stage")),
        "callback_requested": clean(out.get("callback_requested")),

        # D - IDENTITY. Flags, not identities.
        "on_behalf_of": yes_no(out.get("on_behalf_of")),
        "demographic_info_shared": yes_no(out.get("demographic_info_shared")),

        # E - PROFILE. Ids only. The names, ages, addresses and disability
        # percentages that update_profile carries are left on the platform.
        "profile_item_id": clean(prof.get("item_id")),
        "profile_user_id": clean(prof.get("user_id")
                                 or prof.get("acting_as_user_id")),

        # F - NEEDS. The coded category only. disabilities_discussed and
        # needs_challenges_discussed are free text the bot wrote about this
        # person's condition - "locomotor disabled in both legs since
        # childhood" - and are not read.
        "disability_category_mapped": str_list(out.get("disability_category_mapped")),
        "user_unsure_disability": yes_no(out.get("user_unsure_disability")),
        "user_unsure_needs": yes_no(out.get("user_unsure_needs")),

        # G - OPTIONS & ENABLERS
        "solution_option_mapped_categories":
            str_list(out.get("solution_option_mapped_categories")),
        "missing_solution_enabler_mapped_categories":
            str_list(out.get("missing_solution_enabler_mapped_categories")),
        "solution_option_relevance": clean(out.get("solution_option_relevance")),
        "solution_enablers_discussed": yes_no(out.get("solution_enablers_discussed")),
        "user_unsure_solution_options": yes_no(out.get("user_unsure_solution_options")),

        # H - API RESULTS
        "update_profile_api_triggered": yes_no(out.get("update_profile_api_triggered")),
        "update_profile_api_successful": yes_no(out.get("update_profile_api_successful")),
        "matching_providers_found": whole(out.get("matching_providers_found")),
        "connect_provider_api_triggered": yes_no(out.get("connect_provider_api_triggered")),
        "connect_provider_api_successful": yes_no(out.get("connect_provider_api_successful")),
        "providers_connected": whole(out.get("providers_connected")),

        # I - QUALITY. call_value_score and looking_for_details have no source
        # either; same treatment as section E.
        # call_value_score has no source anywhere: not in call_output, not
        # in any tool call. The sheet has it on every row, so it is scored
        # by whoever builds the sheet rather than by the bot.
        "call_value_score": number(out.get("call_value_score")),

        "drop_reason": clean(out.get("drop_reason")),
        # "tool_accces" is Raya's spelling, not a typo here
        "tools_used": str_list(out.get("tool_accces")) or tools_used(raw_transcript),

        "test_flag": None,
    }
