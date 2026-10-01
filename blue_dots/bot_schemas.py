"""Per-bot input schemas: what agent_args each Raya agent expects.

Every bot family wants a different payload, and they disagree about how to
say "nothing here" - DKB writes the literal string "Not Available", the
seeker bots leave a field as "". The bots read those strings, so getting it
wrong is not a validation error, it is a bot being told something false.

This module is the one place that knows the difference. Anything building a
batch should go through build_args() rather than assembling a dict by hand.

SCOPE: six bots, by request - the two KKB signals APIs, Maya Hindi Signals,
TRRAIN Hindi, and the two DKB signals bots. Asking for any other agent raises
rather than guessing. The other eleven were removed deliberately; their
shapes are still in docs/bot_input_formats.txt if they are needed again.

Every schema here was read off the most recent live batch for that agent, not
from documentation - see docs/bot_input_formats.txt for the same information
in prose, and validate_against_raya() below to re-check it against Raya.
"""
from typing import Any

# how a family spells "no value". The seeker bots and DKB genuinely differ.
BLANK = ""
NA = "Not Available"


class Field:
    """One agent_args key.

    json_string matters: `recommendations` is a JSON array SERIALISED INTO A
    STRING inside agent_args, not a nested array. Passing a real list may not
    be read by the bot.
    """

    def __init__(self, name, required=True, empty=BLANK, json_string=False,
                 note=""):
        self.name = name
        self.required = required
        self.empty = empty
        self.json_string = json_string
        self.note = note

    def __repr__(self):
        return f"Field({self.name!r})"


# ---------------------------------------------------------------- families

RECOMMENDATIONS = Field(
    "recommendations", json_string=True,
    note="JSON string: [{job_id, role, company, qualification}, ...]. "
         "Check every job_id still resolves - 219 applies failed as "
         "'Job not found' because it did not.")
CONTACT_MEMORY = Field(
    "contact_memory", empty=NA,
    note="What we already know about this contact. 'Not Available' when new.")

SEEKER = [CONTACT_MEMORY, RECOMMENDATIONS]

SEEKER_SIGNALS = SEEKER + [
    Field("location", required=False, note="Free text address."),
]

SEEKER_COLLEGE = SEEKER + [
    Field("college_name", note="RAW name as the college is known - 'VMLG "
                               "College'. The pipeline normalises it on the "
                               "way back in; do not pre-normalise."),
    Field("location", required=False),
]

TRRAIN = [
    CONTACT_MEMORY,
    Field("college_name"),
    Field("applied_job_company", note="The job they ALREADY applied to."),
    Field("applied_job_role"),
]

PROVIDER = [
    CONTACT_MEMORY,
    Field("company_name", empty=NA),
    Field("city", empty=NA),
    Field("location", empty=NA, note="Full postal address, not a city."),
    Field("job_id", empty=NA),
    Field("job_role", empty=NA),
    Field("num_vacancies", empty=NA),
    Field("qualification", empty=NA),
    Field("salary", empty=NA),
    Field("work_experience", empty=NA),
    Field("work_experience_years", empty=NA),
    Field("phoneNumber", required=False, empty=BLANK,
          note="camelCase, unlike every other key, and empty in practice - "
               "the number dialled is the contact's phone, not this."),
]

# ---------------------------------------------------------------- the agents
#
# family, plus the batch settings each bot is actually run with. Concurrency
# differs by an order of magnitude between bots and that is deliberate, so a
# generator should copy the bot's own figure rather than pick one.

SCHEMAS: dict[str, dict[str, Any]] = {
    # --- seekers, signals ---
    "115b38a5-42ef-4082-be69-84a871bb226a": {
        "name": "KKB Placeholder- Hindi Signals API", "fields": SEEKER_SIGNALS,
        "concurrency": 25, "max_retries": 3, "retry_after_hrs": 1},
    "33037201-78ce-405d-b509-a3b6934e20f1": {
        "name": "KKB Placeholder- Kannada Signals API",
        "fields": SEEKER_SIGNALS + [Field("college_name", required=False)],
        "concurrency": 50, "max_retries": 2, "retry_after_hrs": 1},

    # --- HE college ---
    "904f333f-1919-4523-a51d-b22ba382dd22": {
        "name": "Maya Hindi Signals", "fields": SEEKER_COLLEGE,
        "concurrency": 20, "max_retries": 3, "retry_after_hrs": 1},

    # --- TRRAIN, the service offer ---
    "cf39a59a-3b24-4842-ba03-4248ec245aa1": {
        "name": "TRRAIN Hindi", "fields": TRRAIN,
        "concurrency": 10, "max_retries": 3, "retry_after_hrs": 1},

    # --- providers ---
    # Neither has ever been given a batch, so these fields are the DKB family
    # shape rather than something observed on the wire. describe() says so.
    "fabda71d-af75-4ddd-8cf1-fa35c827f753": {
        "name": "DKB Hindi Signals", "fields": PROVIDER, "unproven": True,
        "concurrency": 15, "max_retries": 2, "retry_after_hrs": 1},
    "847a85e2-c5c8-4727-9918-f1db9efad05d": {
        "name": "DKB Kannada Signals", "fields": PROVIDER, "unproven": True,
        "concurrency": 15, "max_retries": 2, "retry_after_hrs": 1},
}


class SchemaError(ValueError):
    pass


def schema_for(agent_id: str) -> dict[str, Any]:
    if agent_id not in SCHEMAS:
        raise SchemaError(
            f"no schema for agent {agent_id}. This module covers six bots by "
            f"request: {', '.join(v['name'] for v in SCHEMAS.values())}. "
            f"To add another, read its most recent live batch first - do not "
            f"guess the fields.")
    return SCHEMAS[agent_id]


def build_args(agent_id: str, **values: Any) -> dict[str, Any]:
    """The agent_args for one contact, with this bot's own empty convention.

    Unknown keys are refused rather than passed through: a typo that Raya
    silently accepts is a bot running with a field it never reads.
    """
    import json

    schema = schema_for(agent_id)
    fields = {f.name: f for f in schema["fields"]}
    unknown = sorted(set(values) - set(fields))
    if unknown:
        raise SchemaError(
            f"{schema['name']} has no field(s) {', '.join(unknown)}. "
            f"It accepts: {', '.join(sorted(fields))}")

    args: dict[str, Any] = {}
    missing = []
    for name, f in fields.items():
        value = values.get(name)
        if value in (None, ""):
            if f.required and name not in values:
                missing.append(name)
            args[name] = f.empty
            continue
        if f.json_string and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        args[name] = value
    if missing:
        raise SchemaError(
            f"{schema['name']} requires {', '.join(missing)}. Pass the value, "
            f"or pass it explicitly as None to accept this bot's empty "
            f"convention ({fields[missing[0]].empty!r}).")
    return args


def batch_settings(agent_id: str, name: str) -> dict[str, Any]:
    """The batch envelope, using the concurrency this bot is actually run at."""
    s = schema_for(agent_id)
    return {"agent_id": agent_id, "name": name,
            "concurrency": s["concurrency"],
            "max_retries": s["max_retries"],
            "retry_after_hrs": s["retry_after_hrs"]}


def describe(agent_id: str) -> str:
    s = schema_for(agent_id)
    lines = [f"{s['name']}  ({agent_id})",
             f"  concurrency {s['concurrency']}, retries {s['max_retries']}, "
             f"retry after {s['retry_after_hrs']}h"]
    if s.get("unproven"):
        lines.append("  NEVER RUN - schema copied from the family, not observed")
    for f in s["fields"]:
        flag = "" if f.required else "  (optional)"
        lines.append(f"    {f.name:<24}empty={f.empty!r}{flag}")
        if f.note:
            lines.append(f"      {f.note}")
    return "\n".join(lines)


def validate_against_raya(api_key: str, agent_id: str) -> list[str]:
    """Compare this schema to the agent's most recent live batch.

    The schemas were read off live batches once. Bots get edited, so this is
    how you find out the schema has drifted - rather than by a campaign going
    out with a field the bot no longer reads.
    """
    from raya_client import fetch_all_batches, fetch_contacts

    schema = schema_for(agent_id)
    declared = {f.name for f in schema["fields"]}
    batches = fetch_all_batches(api_key, agent_id)
    if not batches:
        return [f"{schema['name']}: no batches to compare against"]
    newest = sorted(batches, key=lambda b: str(b.get("created_at")),
                    reverse=True)[0]
    contacts, _ = fetch_contacts(api_key, newest["id"], limit=20)
    seen: set[str] = set()
    for c in contacts:
        args = c.get("agent_args")
        if isinstance(args, dict):
            seen |= set(args)
    if not seen:
        return [f"{schema['name']}: batch {newest['id']} has no agent_args"]
    # A bot whose newest batch is months old tells us nothing about drift -
    # the schema was read off a NEWER batch on another agent of the same
    # family. Date the finding so a stale batch is not read as a mismatch,
    # which is exactly what the first version of this did.
    when = str(newest.get("created_at"))[:10]
    where = f"batch {newest['id']} ({when})"
    problems = []
    for extra in sorted(seen - declared):
        problems.append(f"{schema['name']}: Raya sends {extra!r} in {where}, "
                        f"schema does not - REAL DRIFT, add the field")
    for missing in sorted(declared - seen):
        problems.append(f"{schema['name']}: schema has {missing!r}, {where} "
                        f"does not - probably just an old batch, check the date")
    return problems


if __name__ == "__main__":
    import os
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    if "--check" in sys.argv:
        key = os.getenv("RAYA_API_KEY")
        drift, stale, never = [], [], []
        for aid in SCHEMAS:
            for p in validate_against_raya(key, aid):
                (drift if "REAL DRIFT" in p
                 else never if "no batches" in p else stale).append(p)
        for title, items in (("NEEDS ACTION - Raya sends a field the schema lacks", drift),
                             ("probably stale - schema field absent from an old batch", stale),
                             ("never run - nothing to compare against", never)):
            print()
            print(f"{title}: {len(items)}")
            for p in items:
                print(f"  {p}")
        print()
        print(f"{len(SCHEMAS)} schemas checked, {len(drift)} need action.")
    else:
        for aid in SCHEMAS:
            print(describe(aid), end="\n\n")
