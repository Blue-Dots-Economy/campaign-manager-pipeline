"""Transformation logic for TRRAIN calls — a raw Raya (batch, contact) pair
into a Supabase `trrain_mastersheet` row. No network calls.

TRRAIN is a LATER STAGE of the same funnel, not a separate programme. The bot
calls seekers who have already applied through us and makes one offer of a free
support service. Every sampled TRRAIN phone (400/400) already exists in
kkb_mastersheet, so these rows describe people we already track.

It gets its own table rather than sharing kkb_mastersheet because the payloads
barely overlap: of 15 call_output fields only 5 exist on the KKB side, there is
no job data at all, and so no intent score can be computed. Folding 1,854 such
rows into kkb_mastersheet would leave ten columns empty and quietly distort
every rate derived from it — answer rate, apply rate, intent distribution.

The link back to the seeker is the phone number, which is how
build_seeker_journey.py already joins.
"""
from typing import Any

from transform import first_call
from transform_dkb import call_date_ist, clean, ist_datetime, yes_no

# TRRAIN writes "NA" for "did not establish this", which is NOT the same as No.
# clean() already maps it to None; these helpers keep the three-way distinction
# where it matters.
TRRAIN_TRISTATE = ("yes", "no", "maybe", "proxy")


def tristate(value: Any) -> str | None:
    """Yes / No / Maybe / Proxy, normalised. NA and blanks become None.

    Kept as text rather than a boolean because "Maybe" is a real answer here -
    279 of 1,082 calls - and collapsing it to either true or false would
    invent a decision the seeker did not make.
    """
    text = clean(value)
    if text is None:
        return None
    lower = text.lower()
    return text.capitalize() if lower in TRRAIN_TRISTATE else text


def question_list(value: Any) -> list[str] | None:
    """questions_asked is a list of what the seeker asked; [] means none."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in ("na", "[]", "none", "null"):
            return None
        return [text]
    if isinstance(value, list):
        items = [str(x).strip() for x in value if str(x).strip()]
        return items or None
    return None


def all_call_outputs(contact: dict[str, Any]) -> list[dict[str, Any]]:
    """Every attempt's call_output, newest first (Raya returns calls newest-first)."""
    import json as _json
    outs = []
    for call in (contact.get("calls") or []):
        if not isinstance(call, dict):
            continue
        co = call.get("call_output") or {}
        if isinstance(co, str):
            try:
                co = _json.loads(co)
            except Exception:
                co = {}
        if isinstance(co, dict) and co:
            outs.append(co)
    return outs


def latest_answer(outs: list[dict[str, Any]], key: str) -> Any:
    """The most recent attempt that actually recorded this field.

    Retries are common here, and a later no-answer attempt overwrites an
    earlier real answer if you only read calls[0]. Scanning newest-first for
    the first non-empty value keeps what the seeker actually said.
    """
    for co in outs:
        value = clean(co.get(key))
        if value is not None:
            return co.get(key)
    return None


def any_yes(outs: list[dict[str, Any]], key: str) -> bool | None:
    """True if ANY attempt recorded yes. Used for do_not_call, where losing an
    opt-out because a later retry went unanswered would be a real harm."""
    seen = False
    for co in outs:
        v = yes_no(co.get(key))
        if v is True:
            return True
        if v is False:
            seen = True
    return False if seen else None


def make_trrain_row(
    batch: dict[str, Any],
    contact: dict[str, Any],
    raw_transcript: Any = None,
    recording_url: str | None = None,
    jfc: str | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """One row per contact, keyed on Raya's contact_id — the same grain and the
    same key convention as dkb_mastersheet, so the two can be joined."""
    call = first_call(contact)            # NOTE: calls[0] is the LATEST attempt
    outs = all_call_outputs(contact)
    call_output = outs[0] if outs else {}

    phone = contact.get("phone") or contact.get("caller_no") or ""

    return {
        "call_id": str(contact.get("contact_id")) if contact.get("contact_id") is not None else None,
        "batch_id": str(batch.get("id")) if batch.get("id") is not None else None,
        "campaign_name": clean(batch.get("name")),
        # .isoformat(), not the date object - requests cannot serialise a date
        "campaign_date": (lambda d: d.isoformat() if d else None)(call_date_ist(call)),
        "call_datetime_ist": ist_datetime(call),
        "agent_id": str(agent_id) if agent_id else None,
        "agent_name": agent_name,
        "jfc_campaign": jfc,
        "channel": "Outbound",

        # who we reached
        "phone": str(phone) if phone else None,
        "contact_name": clean(contact.get("name")),
        "seeker_name": clean(latest_answer(outs, "seeker_name")),
        "right_person": tristate(latest_answer(outs, "right_person")),
        "call_answered": yes_no(call_output.get("call_answered")),
        "audio_check_confirmed": tristate(latest_answer(outs, "audio_check_confirmed")),

        # the offer itself - the whole point of the call
        "trrain_pitched": yes_no(latest_answer(outs, "trrain_pitched")),
        "trrain_interest": tristate(latest_answer(outs, "trrain_interest")),
        "partner_named": yes_no(latest_answer(outs, "partner_named")),
        "offer_repeated": yes_no(latest_answer(outs, "offer_repeated")),
        "remembered_application": tristate(latest_answer(outs, "remembered_application")),
        "questions_asked": question_list(latest_answer(outs, "questions_asked")),

        # compliance signals
        "do_not_call": any_yes(outs, "do_not_call"),
        "promised_outcome": yes_no(latest_answer(outs, "promised_outcome")),
        # a real timestamp, not a flag - better than inferring from drop_reason
        "callback_requested": clean(latest_answer(outs, "callback_requested")),

        # call mechanics
        "call_outcome": clean(call.get("outcome")),
        "call_duration_seconds": call.get("call_duration"),
        "contact_attempts": len(contact.get("calls") or []),
        "drop_reason": clean(call_output.get("drop_reason")),
        "call_summary": clean(call_output.get("final_summary")),
        "call_transcript": raw_transcript,
        "call_recording_url": recording_url,
    }
