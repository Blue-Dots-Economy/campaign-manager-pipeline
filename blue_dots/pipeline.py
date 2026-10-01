"""Shared pipeline logic: agent and batch listing, row building, campaign names.
Fetches agents/batches from Raya, diffs against what's already in Supabase,
and builds rows for whichever batches you choose to push."""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from raya_client import fetch_all_agents, fetch_all_batches, fetch_all_contacts, fetch_call_detail
from supabase_client import fetch_pushed_batch_ids
from transform import first_call, make_application_rows, make_row

# Which Job Facilitation Centre each bot belongs to. Drives the jfc_campaign
# column. Kannada bots serve Hubli-Dharwad (Karnataka); the Hindi bots and the
# college programme serve Ghaziabad (UP). Add new agents here as they appear —
# an unmapped agent leaves jfc_campaign empty rather than guessing.
DKB_AGENT_JFC = {
    "57814ac8-5d79-41f5-bab7-bcfe2d9aac4f": "Ghaziabad",      # DKB Hindi- New
    "d1a1614f-fa7e-41c1-8963-e7f3af213a13": "Hubli-Dharwad",  # DKB Kannada- New
    "fabda71d-af75-4ddd-8cf1-fa35c827f753": "Ghaziabad",      # DKB Hindi Signals
    "847a85e2-c5c8-4727-9918-f1db9efad05d": "Hubli-Dharwad",  # DKB Kannada Signals
    "48bdb18a-1cf3-48bb-bba9-130dac876aac": "Ghaziabad",      # DKB English- New
}

AGENT_JFC = {
    "da612923-1927-45d7-9ad0-b1c7cbb15294": "Ghaziabad",      # KKB Placeholder
    "0689a144-a489-4cb6-9528-422dc542e81a": "Ghaziabad",      # Kaam Ki Baat (General - Hindi)
    "47fdffe6-0cb0-4fcf-8762-135ddadfb194": "Ghaziabad",      # Maya KKB College Hindi
    "87ab9108-5d66-4a13-a20a-575eaa9aae36": "Hubli-Dharwad",  # KKB Placeholder- Kannada
    "1fa978d0-adf5-4310-a227-d0eed93be136": "Hubli-Dharwad",  # KKB Kannada
    # Signals bots, added Sept 2026. Same rule as above: the Hindi bots serve
    # Ghaziabad, the Kannada bot serves Hubli-Dharwad.
    "904f333f-1919-4523-a51d-b22ba382dd22": "Ghaziabad",      # Maya Hindi Signals
    "115b38a5-42ef-4082-be69-84a871bb226a": "Ghaziabad",      # KKB Placeholder- Hindi Signals API
    "33037201-78ce-405d-b509-a3b6934e20f1": "Hubli-Dharwad",  # KKB Placeholder- Kannada Signals API
    # A/B variants of the two above, first batches 16 Sept 2026. These post-date
    # data/agent_ids.txt, which is why a survey driven by this map alone missed
    # them — see list_unmapped_agents().
    "140d13ca-c80f-47c4-9454-5edb3fd38c96": "Ghaziabad",      # KKB Slim- Hindi Signals API (A/B)
    "bd4dfd44-a0c5-4d37-9b49-f8c186c901c1": "Hubli-Dharwad",  # KKB Slim- Kannada Signals API (A/B)
}


# Agents that take inbound calls. Some are inbound-only bots, some are the
# outbound bots above receiving return calls - either way an inbound call has
# no batch, so walking batches never finds it and these ids have to be listed.
# Same rule as everywhere else: Kannada serves Hubli-Dharwad, Hindi Ghaziabad.
INBOUND_AGENT_JFC = {
    "4ac90bf1-a740-4b1c-92b0-45bda099e53f": "Hubli-Dharwad",  # KKB Inbound Placeholder- Kannada
    "b6222233-8a8d-49a6-9950-d07e9d159757": "Ghaziabad",      # KKB Inbound Placeholder
    "df99f501-e636-4f3d-80dc-e06e82240082": "Ghaziabad",      # Inbound Maya KKB College Hindi
    "f38da775-c572-4a50-9340-fe1f42c43901": "Hubli-Dharwad",  # KKB Kannada Inbound Signals
    "1c24feda-a584-4012-a865-fa8f950089df": "Ghaziabad",      # Maya Inbound Signals
    "08001508-0146-467b-a35f-e8754a7aeff5": "Ghaziabad",      # KKB Inbound Hindi 14072026
    "3f521174-574d-43ca-a9be-081849373c18": "Ghaziabad",      # KKB Hindi Inbound Signals
}

# Numbers the team rang the inbound bots from while testing. The calls are
# real in Raya and must not count as seeker traffic, so their rows carry
# test_flag = true.
#
# Kept here rather than flipped once by hand in Supabase: load_inbound.py
# re-upserts these rows on every run and would write the flag straight back
# to null. Last given by the sheet owner on 25 Sept 2026.
TEST_PHONES = {
    "9057206073", "9003061570", "8850601733", "9108790249", "7862879115",
    "9167150842", "9819007259", "8095444625", "6375185476", "8287082929",
}


# Test and demo bots. Their calls are real in Raya and meaningless to us, so
# they are named here rather than filtered by a guess about the name.
EXCLUDED_AGENTS = {
    "f60e0899-aa3a-4be7-9b4f-0296bd28ef48",   # Testing Agent- Blue Dots
    "2f57fa97-7799-4552-8aa0-6ac9b277d81f",   # Purple-dots-with-APIs-V2-Inbound
    "951d13c3-6be0-4b0e-872c-18e74734c81e",   # Dhande Ki Baat (Persona Based)
    "1e2e1670-e60a-4e90-973d-30220db0f08c",   # Kaam Ki Baat (Persona Based)
}

_agent_name_cache: dict[str, str] | None = None


def jfc_for_agent(agent_id: str | None) -> str | None:
    if not agent_id:
        return None
    key = str(agent_id)
    return (AGENT_JFC.get(key) or DKB_AGENT_JFC.get(key)
            or INBOUND_AGENT_JFC.get(key))


def agent_names(api_key: str) -> dict[str, str]:
    """agent_id -> name, read live from Raya and cached for the process.

    Deliberately NOT hardcoded: bots get renamed, and a stale local copy would
    silently write the old name. Only the JFC mapping above is hardcoded,
    because Raya doesn't know which centre a bot serves.
    """
    global _agent_name_cache
    if _agent_name_cache is None:
        _agent_name_cache = {
            str(a["id"]): a.get("name")
            for a in fetch_all_agents(api_key)
            if a.get("id")
        }
    return _agent_name_cache


CAMPAIGN_NAME_MAPPING_PATH = os.path.join(os.path.dirname(__file__), "data", "callids_to_campaignname.xlsx")
_campaign_name_mapping_cache: dict[str, str] | None = None


def campaign_lookup_keys(contact: dict[str, Any]) -> list[str]:
    """Every key a contact's calls could be listed under in the campaign sheet.

    The sheet's "Call ID" column is MIXED-FORMAT: early campaigns (through
    2026-06-07) use Raya's call.uuid, everything after uses Raya's numeric
    call.id. Note contact_id is NOT a valid key — it shares a 7-digit range
    with call.id, so matching on it produces false positives.

    All of a contact's call attempts are included, since the sheet may list
    any one of them.
    """
    keys: list[str] = []
    for call in contact.get("calls") or []:
        if not isinstance(call, dict):
            continue
        if call.get("uuid"):
            keys.append(str(call["uuid"]))
        if call.get("id") is not None:
            keys.append(str(call["id"]))
    return keys


def load_campaign_name_mapping(path: str = CAMPAIGN_NAME_MAPPING_PATH) -> dict[str, str]:
    """call.uuid OR call.id -> real campaign name, from the
    callids_to_campaignname sheet (Raya's own batch name is a meaningless
    auto-generated codename, not the actual campaign name). Both key types
    live in one dict — UUIDs and 7-digit numbers can't collide. Loaded once
    and cached; missing file -> empty dict so callers just fall back to the
    Raya batch name."""
    global _campaign_name_mapping_cache
    if _campaign_name_mapping_cache is not None:
        return _campaign_name_mapping_cache

    mapping: dict[str, str] = {}
    if os.path.exists(path):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue
            call_uuid, campaign_name = row[0], row[1]
            if call_uuid and campaign_name:
                mapping[str(call_uuid).strip()] = str(campaign_name).strip()

    _campaign_name_mapping_cache = mapping
    return mapping


def env_or_arg(name: str, value: str | None) -> str:
    if value:
        return value
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required value: {name}")
    return value


def list_agents(api_key: str) -> list[dict[str, Any]]:
    """All Raya agents, sorted by name."""
    agents = fetch_all_agents(api_key)
    return sorted(agents, key=lambda a: str(a.get("name") or "").lower())


def get_batches_with_status(
    api_key: str,
    agent_id: str,
    supabase_url: str,
    supabase_key: str,
) -> list[dict[str, Any]]:
    """All batches for an agent, each annotated with whether it's already
    been pushed to Supabase (based on kkb_mastersheet.batch_id == batch id)."""
    all_batches = fetch_all_batches(api_key, agent_id)

    pushed_ids = fetch_pushed_batch_ids(supabase_url, supabase_key)

    annotated = []
    for batch in all_batches:
        batch = dict(batch)
        batch["already_pushed"] = str(batch.get("id")) in pushed_ids
        annotated.append(batch)
    return annotated


def build_rows_for_batches(
    api_key: str,
    batches: list[dict[str, Any]],
    max_workers: int = 32,
    progress: Any = None,
    need_transcripts: bool = True,
    applications: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Builds Supabase rows for every contact in the given batches.

    applications: pass a list to also collect kkb_applications child rows -
    one per job each call applied to. Same pattern as dkb_newjobs. Left None
    by callers that only want the mastersheet rows.

    need_transcripts: when True (default), also fetches each contact's full
    per-call transcript (tool_calls + results) so make_row can read the actual
    apply_job outcome instead of guessing. Set False to skip that entirely
    (one extra API call per contact) when the caller only needs fields that
    don't depend on it — e.g. campaign_name backfill.

    Transcripts are fetched with ONE thread pool shared across every batch
    (not one per batch) so the pool stays fully occupied throughout the whole
    run instead of draining and restarting at each batch boundary.

    progress: optional callable(done, total) invoked as transcripts complete.
    """
    campaign_name_mapping = load_campaign_name_mapping()

    batch_contacts: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    call_uuids: set[str] = set()
    for batch in batches:
        if batch.get("id") is None:
            continue
        contacts = fetch_all_contacts(api_key, batch["id"])
        batch_contacts.append((batch, contacts))
        for contact in contacts:
            uuid = first_call(contact).get("uuid")
            if uuid:
                call_uuids.add(uuid)

    details: dict[str, Any] = {}
    if need_transcripts:
        total = len(call_uuids)
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_uuid = {executor.submit(fetch_call_detail, api_key, uuid): uuid for uuid in call_uuids}
            for future in as_completed(future_to_uuid):
                uuid = future_to_uuid[future]
                try:
                    details[uuid] = future.result()
                except Exception:
                    details[uuid] = None
                done += 1
                if progress:
                    progress(done, total)

    rows: list[dict[str, Any]] = []
    for batch, contacts in batch_contacts:
        for contact in contacts:
            uuid = first_call(contact).get("uuid")
            override = None
            for key in campaign_lookup_keys(contact):
                override = campaign_name_mapping.get(key)
                if override:
                    break
            rows.append(make_row(
                batch,
                contact,
                raw_transcript=(details.get(uuid) or {}).get("call_transcript"),
                recording_url=(details.get(uuid) or {}).get("call_recording_url"),
                campaign_name_override=override,
                jfc=jfc_for_agent(batch.get("agent_id")),
                agent_id=batch.get("agent_id"),
                agent_name=agent_names(api_key).get(str(batch.get("agent_id"))),
            ))
            if applications is not None:
                applications.extend(make_application_rows(
                    batch, contact,
                    raw_transcript=(details.get(uuid) or {}).get("call_transcript"),
                ))
    return rows


def build_dkb_rows_for_batches(
    api_key: str,
    batches: list[dict[str, Any]],
    max_workers: int = 32,
    progress: Any = None,
    need_transcripts: bool = True,
) -> list[dict[str, Any]]:
    """Same as build_rows_for_batches, but produces dkb_mastersheet rows.

    DKB has no campaign-name sheet and no intent score, so the only reason to
    fetch per-call details is the transcript and recording URL — neither is on
    the batch contacts endpoint.
    """
    from transform_dkb import make_dkb_row

    batch_contacts: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    call_uuids: set[str] = set()
    for batch in batches:
        if batch.get("id") is None:
            continue
        contacts = fetch_all_contacts(api_key, batch["id"])
        batch_contacts.append((batch, contacts))
        for contact in contacts:
            uuid = first_call(contact).get("uuid")
            if uuid:
                call_uuids.add(uuid)

    details: dict[str, Any] = {}
    if need_transcripts:
        total = len(call_uuids)
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_uuid = {executor.submit(fetch_call_detail, api_key, uuid): uuid for uuid in call_uuids}
            for future in as_completed(future_to_uuid):
                uuid = future_to_uuid[future]
                try:
                    details[uuid] = future.result()
                except Exception:
                    details[uuid] = None
                done += 1
                if progress:
                    progress(done, total)

    rows: list[dict[str, Any]] = []
    for batch, contacts in batch_contacts:
        jfc = jfc_for_agent(batch.get("agent_id"))
        for contact in contacts:
            uuid = first_call(contact).get("uuid")
            detail = details.get(uuid) or {}
            rows.append(make_dkb_row(
                batch,
                contact,
                raw_transcript=detail.get("call_transcript"),
                recording_url=detail.get("call_recording_url"),
                jfc=jfc,
            ))
    return rows
