"""Generates the column catalogue, and the SQL to put it in the database.

Written as a generator rather than a hand-maintained document, because the
answer to "how is this column filled" IS the code - anything that restates it
can disagree with it, and this repo has already had a README naming deleted
files. Run it whenever; it reads the live schema and the live source.

Two outputs:

    docs/columns.md          the catalogue, with the pipeline structure
    docs/column_comments.sql  comment on column ... - run it in Supabase

    python describe_columns.py            # both files
    python describe_columns.py --stdout   # print the catalogue instead
"""
import argparse
import ast
import io
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

# table -> the module and function that builds a row for it
BUILDERS = {
    "kkb_mastersheet": ("transform.py", "make_row"),
    "dkb_mastersheet": ("transform_dkb.py", "make_dkb_row"),
    "trrain_mastersheet": ("transform_trrain.py", "make_trrain_row"),
    "kkb_applications": ("transform.py", "make_application_rows"),
}
# heavy text columns: counted with a HEAD request rather than pulled
HEAVY = {"call_transcript", "recommendations_input", "apply_attempts",
         "intent_score_reasoning", "call_summary", "call_recording_url"}

# where a value comes from, in trust order. The order matters and is the
# single most useful thing in the catalogue: when the bot's own summary
# disagrees with the transcript, the transcript is right.
SOURCES = [
    ("Raya · call record", ("call.get", "contact.get", "batch.get",
                            "call_date", "first_call"),
     "Recorded by the dialler. Fact."),
    ("Raya · agent_args", ("agent_args", "args.get"),
     "What WE sent the bot when the batch was made. Echoed back unchanged."),
    ("Raya · transcript", ("raw_transcript", "extract_apply", "transcript",
                           "tool_call"),
     "Dug out of the conversation and its tool calls. A tool call either "
     "happened or it did not, so this is the strongest evidence we have."),
    ("Raya · call_output", ("call_output", "merged_output", "out.get",
                            "latest_answer", "any_yes"),
     "An AI's summary written after the call. A claim, not a measurement, "
     "and only present when somebody answered."),
    ("computed locally", ("compute_", "normalise", "campaign_day", "job_list",
                          "safe_", "yes_no", "tristate", "clean("),
     "Derived by our code. Ours to change."),
]


def classify(expr: str, filled: int = 0) -> tuple[str, str]:
    """`expr == "None"` means the ROW BUILDER does not set it - not that the
    column is empty. Three of them are filled by backfill scripts, and the
    first version of this reported bot_language (83% full) as "never set".
    The fill rate is what separates the two."""
    if expr.strip() == "None":
        if filled:
            return ("filled by a backfill",
                    "The row builder leaves this null; a backfill script "
                    "fills it afterwards. It goes stale if the backfill is "
                    "not re-run after new rows land.")
        return "never filled", ("Declared and never populated. A query on it "
                                "returns nothing, which reads as 'this never "
                                "happens' rather than 'we never asked'.")
    for name, needles, why in SOURCES:
        if any(n in expr for n in needles):
            return name, why
    return "computed locally", SOURCES[-1][2]


def compact(col: str, expr: str) -> str:
    """The expression with the repetition taken out, for the document only.

    Most rows are the column's own name fetched through one coercion -
    `yes_no(out.get('call_answered'))` for `call_answered`. Printing 199 of
    those buries the dozen expressions that actually say something, so a
    pass-through collapses to the coercion and `·` marks the fetch. A
    RENAME is never collapsed: `clean(args.get('city'))` filling `city_input`
    is exactly the kind of thing somebody reads this to find out.
    The database comments keep the full expression.
    """
    e = expr.strip()
    if e == col:
        return "·"
    keys = re.findall(r"'([^']*)'", e)
    # exactly ONE fetch: `clean(x.get('company_name')) or clean(y.get(...))`
    # must stay spelled out, because the fallback to a second SOURCE is the
    # whole point of that row.
    if len(keys) == 1 and keys[0] == col and e.count(".get(") == 1:
        return re.sub(r"\b[\w.]+\.get\('[^']*'\)", "·", e)
    return e[:88]


def expressions(module: str, func: str) -> dict[str, str]:
    """Each column paired with the expression that fills it, from the AST.

    Takes the BIGGEST dict literal in the function - the row - rather than the
    last one walked, which is a different dict and produced an empty result
    the first time this was written.
    """
    try:
        tree = ast.parse(io.open(module, encoding="utf-8").read())
    except Exception:
        return {}
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == func), None)
    if fn is None:
        return {}
    dicts = [n for n in ast.walk(fn) if isinstance(n, ast.Dict)]
    if not dicts:
        return {}
    best = max(dicts, key=lambda d: len(d.keys))
    out = {}
    for k, v in zip(best.keys, best.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            out.setdefault(k.value, ast.unparse(v))
    # a row assembled in pieces (transform_dkb builds `updated` separately)
    for d in dicts:
        if d is best:
            continue
        for k, v in zip(d.keys, d.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.setdefault(k.value, ast.unparse(v))
    return out


def live(url, key, table):
    """Column list from PostgREST, and how many rows actually carry a value."""
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    spec = requests.get(f"{url}/rest/v1/", headers=h, timeout=120).json()
    props = spec.get("definitions", {}).get(table, {}).get("properties", {})
    cols = list(props)
    light = [c for c in cols if c not in HEAVY]

    rows, off = [], 0
    while light:
        r = requests.get(f"{url}/rest/v1/{table}", headers=h, timeout=300,
                         params={"select": ",".join(light), "order": cols[0],
                                 "offset": off, "limit": 1000})
        chunk = r.json() if r.status_code < 400 else []
        if not isinstance(chunk, list) or not chunk:
            break
        rows += chunk
        if len(chunk) < 1000:
            break
        off += len(chunk)
    total = len(rows)
    fill = {c: sum(1 for r in rows if r.get(c) not in (None, "", [], {}))
            for c in light}
    for c in (set(cols) & HEAVY):
        r = requests.get(f"{url}/rest/v1/{table}",
                         headers={**h, "Prefer": "count=exact"},
                         params={"select": cols[0], c: "not.is.null", "limit": 1},
                         timeout=300)
        fill[c] = int(r.headers.get("content-range", "0-0/0").split("/")[-1])
    return cols, fill, total, props


STRUCTURE = """## How the pipeline is put together

    Raya API                  the voice-bot platform
       |
       |  raya_client.py      reads only. Batches, contacts, per-call detail
       v
    pipeline.py               which agent serves which JFC, real campaign names
       |
       |  transform*.py       pure functions. One (batch, contact) -> one row.
       |                      No network, no writes, so they can be reasoned
       v                      about and tested in isolation
    supabase_client.py        the only thing that writes to the database
       |
       v
    Supabase                  kkb / dkb / trrain mastersheets, applications
       |
       |  build_*_journey.py  rolls calls up to one row per person
       |  call_confidence.py  scores who is worth calling next
       v
    aggregated_*_journey
       |
       |  push_*_sheets.py    merge into the Google Sheets. Refresh what we
       v                      own, append what is new, clear nothing
    Google Sheets

**Entry points** are run directly: `main.py` (push a batch), `load_inbound.py`
(inbound calls, which belong to no batch), `load_bluedot.py` (the S3 dumps),
the `push_*` sheet writers, the `backfill_*` scripts, `score_confidence.py`.

**Libraries** are only imported: `raya_client`, `pipeline`, `transform*`,
`supabase_client`, `sheet_format`, `call_confidence`, `bot_schemas`.

Every entry point takes `--dry-run`, which writes a CSV and touches nothing
remote. That is the intended way to inspect a change.

When `applied_to_job` (a summary field) disagrees with `apply_api_success`
(read from the tool result), the tool result is right.

### One thing to know about `call_output`

15 of the 22 agents declare **no output fields at all**. The fields that come
back are invented by Raya's summariser rather than specified by us - which is
why `drop_reason` has 322 distinct values including four spellings of "hung
up", and why it is blank on most rows. Declaring `output_fields` on an agent
fixes it: the DKB bots declare 38 and their data is markedly cleaner.

## The journey, step by step

What happens between somebody's phone ringing and a cell changing in a Google
Sheet. Nine stages; the interesting ones are 4, 6 and 9.

### 0. Before the pipeline: the campaign is launched

Not in this repo. Somebody builds a batch in Raya - an agent, a contact list,
and the per-contact `agent_args` that bot expects. `bot_schemas.py` records
what each of the six live bots needs, and `--check` compares it against what
Raya actually declares. Raya names batches itself, and the names are
auto-generated nonsense ("Quiet Glacier 040"), which is why stage 5 exists.

### 1. Discovery: which batches are there

`GET /api/agent`, paged 100 at a time. `AGENT_JFC` maps each agent id to the
job-family cluster it serves - the Hindi bots to Ghaziabad, the Kannada ones
to Hubli-Dharwad - and `EXCLUDED_AGENTS` drops four test bots. Then
`GET /api/batch?agent_id=&sort=desc`, also paged. `get_batches_with_status()`
cross-checks the result against the `batch_id` values already in
`kkb_mastersheet`, so the interactive picker only offers batches nobody has
pushed yet.

### 2. Preflight: fail in two seconds, not twenty minutes

`check_columns()` builds a throwaway row from an empty batch and contact,
reads one live row, and diffs the key sets. PostgREST rejects an **entire**
write if it carries one unknown column, so a field added to `transform.py`
without its `alter table` would otherwise kill the push at the first chunk -
after every transcript had already been fetched. Instead it exits immediately
with the exact SQL to run.

### 3. Fetch the contacts

`GET /api/batch/{id}/contacts`, paged 100. One contact is one person in the
batch, and carries `calls[]` - one entry per dialling attempt - each with its
own `call_output`, plus the `agent_args` echoed back unchanged. This endpoint
returns only eight fields per call: no transcript, no recording URL.

### 4. Fetch the per-call detail, concurrently

Every contact's first call uuid is collected into one set across **all**
batches, then `GET /api/call/{uuid}` runs on a single 32-thread pool shared
by the whole run rather than one pool per batch - otherwise the pool drains
and restarts at every batch boundary. Retries back off on 429 (honouring
Raya's own `retry_after`) and on 502/503/504/520/522/524, because Raya sits
behind Cloudflare and a twenty-minute walk through tens of thousands of
contacts will meet at least one. This stage is where `call_transcript` - with
its `tool_calls` and their tool-role results - and `call_recording_url` come
from. Everything in the `transcript` src group depends on it.

### 5. Resolve the campaign name

Three sources, first win: `--campaign-name` on the command line, then
`data/callids_to_campaignname.xlsx` keyed by call uuid, then Raya's own batch
name as a last resort. Names follow
`<programme>_<jfc>_<segment>_<month><day>`, lowercase, month before day:
`kkb_up_sept21`, `trrain_gzb_aug18`.

### 6. Transform: one (batch, contact) becomes one row

Pure functions - no network, no writes - so they can be reasoned about and
tested on their own. The steps that are not obvious:

- `merged_output()` takes the newest attempt and backfills it field by field
  from the older ones. Reading `calls[0]` alone loses what somebody said on
  attempt 1 when attempts 2 and 3 rang out. That is about 0.5% of dialled
  contacts, but they are specifically the people who engaged.
- `extract_apply_attempts()` walks the transcript for `apply_job` tool calls
  and pairs each with the tool-role message that follows it, recording the
  `job_id`, the `profile_id`, whether it worked, and the machine-readable cause
  when it did not - `PROFILE_NOT_LIVE`, `ACTION_LIMIT_REACHED`, `HTTP 404`. This
  is the only place a failed apply's job_id survives when the bot leaves it out
  of `jobs_failed_to_apply`, and the spoken transcript never mentions the error
  at all.
- `compute_apply_api_success()` is the trust order written down as code: it
  takes the transcript's verdict, and falls back to `call_output` only when no
  apply attempt was found in the transcript at all.
- `compute_intent_score()` returns 0-10 with a reason. It short-circuits
  first - not dialled is 0, a completed application is 9, consented-but-the-
  apply-failed is 7, zero duration is 0 - and otherwise adds up duration bands
  (over 10s, 30s, 60s and 2min, one point each), jobs shown (two points) and
  engagement (one).
- `normalise_college()` collapses spelling variants, and `_clean()` turns
  "NA", "null", "-" and friends into real nulls so a placeholder never reaches
  a sheet looking like a company name.
- `make_application_rows()` is the one-to-many: one child row per job the
  call applied to.

### 7. Write

`push_to_supabase()` POSTs in chunks of 500 with
`Prefer: resolution=merge-duplicates` and `on_conflict=call_id`, so
re-pushing a batch updates the existing rows instead of duplicating them, and
retries five times on dropped connections and 502/503/504. The
`kkb_applications` children go **after** the parents, because their `call_id`
is a foreign key onto the mastersheet, and are deduplicated on
`(call_id, job_id, applied)` before they are sent.

### The inbound detour

Inbound calls skip all of the above. They have no contact record and belong
to no batch, so walking batches cannot see them at all - they came in through
`load_inbound.py`, which uses `GET /api/call?agent_id=` and infers direction,
since Raya records none: inbound means `caller_no` is set and `to_number` is
empty. `make_inbound_row()` then reshapes the call into what `make_row()`
expects, so the scoring and the apply extraction are literally the same code
as outbound. Three fields differ deliberately: `channel` is "Inbound",
`call_id` is the call's uuid because there is no contact_id to key on, and
`batch_id` and `campaign_day` are null.

### 8. Roll up

`build_seeker_journey.py` produces one row per Blue Dot seeker, joined to the
calls on phone and upserted on `seeker_id`. Every seeker gets a row whether
or not we ever called them, and anyone we called who has no Blue Dot record
gets one too, keyed `phone:<number>` and flagged `in_bluedot = false`.
`build_provider_journey.py` does the same on the DKB side.

`call_confidence.py` then scores who is worth calling next, 0-10. The weights
are set so the best possible case sums to exactly 10 and nothing real reaches
it, which keeps the ranking intact at the top instead of flattening against a
clamp. The strongest signal is somebody asking to be called back (+3.0);
untried beats unresponsive (never called, +3.0); a missed call is treated as
mild (-0.4 each) rather than a refusal; and having already applied is a
deprioritisation (-2.0), not a suppression.

### 9. The sheets

All three writers share one rule: **refresh what we own, append what is new,
clear nothing.** Per row:

- A key is built from the key columns, and **every** part must be present. With
  `any()` instead, a row with a blank job_id keys on the phone alone and two
  different applications by the same seeker collapse into one row.
- On a key match, only the OWNED columns are overwritten, and only where the
  value actually differs **numerically**. `USER_ENTERED` writes `30000` and
  reads back `30,000`, so a string comparison makes that cell oscillate forever.
- Hand-typed columns - `Remarks` and the rest - are carried over, never
  blanked.
- New rows are appended at an explicit `A{n}` anchor, never with
  `append_rows()`. That function lets the Sheets API guess where the table ends,
  and on a tab whose column A is blank for its first hundred rows it guessed
  B537: seven live rows overwritten and twenty-eight appended one column right.
- The batch counter only advances once a run has actually appended something.
  Advancing it on every run burned 23 numbers on format-only passes and left
  gaps in the one column whose job is to say which push a row arrived in.

A re-run with no new data must report `0 refreshed, 0 appended`. Anything
else means a cell is oscillating.

"""

# One short code per source, so the provenance can ride along in the column
# table instead of splitting every table into eight sections with the same
# eight explanations repeated underneath them.
CODE = {
    "Raya · call record": "record",
    "Raya · transcript": "transcript",
    "Raya · agent_args": "args",
    "computed locally": "local",
    "Raya · call_output": "output",
    "filled by a backfill": "backfill",
    "database default": "default",
}
ORDER = list(CODE) + ["never filled"]

LEGEND = """## Reading the tables

Every column below carries a `src` code. In trust order:

| src | what it is | trust |
|---|---|---|
| `record` | Raya's dialler record - who, when, how long, status | fact |
| `transcript` | dug out of the conversation's tool calls | strong: a tool call fired or it did not |
| `args` | what we sent the bot when the batch was made, echoed back | fact, but ours not Raya's |
| `local` | derived by our own code | ours to change |
| `output` | an AI's summary written after the call | a claim; exists only when somebody answered |
| `backfill` | the row builder leaves it null and a script fills it later | goes stale if not re-run |
| `default` | a serial id or a database default | - |

`filled` is the share of rows carrying a value. In `expression`, `·` stands for the
column's own name - the field fetched or the variable passed through - so
`yes_no(·)` means "taken as-is, coerced to yes/no". Anything spelled out in
full is doing something less obvious, like reading a differently-named field
or falling back to a second source. Columns that are declared and never
populated are listed under each table rather than given a row of their own.

"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    md = ["# Column catalogue",
          "",
          "Generated by `describe_columns.py` - do not edit. Re-run it after "
          "any change to a transform or a table.",
          "",
          STRUCTURE,
          LEGEND]
    sql = ["-- Generated by describe_columns.py. Run in the Blue Dots project.",
           "-- Puts each column's provenance where someone browsing the table",
           "-- in Supabase will actually see it.",
           ""]

    for table, (module, func) in BUILDERS.items():
        exprs = expressions(module, func)
        cols, fill, total, props = live(url, key, table)
        print(f"  {table}: {len(cols)} columns, {total} rows")

        live_rows, dead, notes = [], [], {}
        for c in cols:
            e = exprs.get(c)
            n = fill.get(c, 0)
            src, why = (classify(e, n) if e
                        else ("database default",
                              "Not in the row builder at all - a serial id or "
                              "a default the database supplies."))
            notes[c] = (src, why, e or "-")
            if src == "never filled":
                dead.append(c)
            else:
                live_rows.append((ORDER.index(src), -n, c, CODE[src], n, e or "-"))

        md += [f"## `{table}`", "",
               f"{len(cols)} columns, {total:,} rows. "
               f"`{module}` → `{func}()`.", "",
               "| column | src | filled | expression |", "|---|---|---:|---|"]
        for _, _, c, code, n, e in sorted(live_rows):
            pct = f"{100 * n / total:.0f}%" if total else "-"
            md.append(f"| `{c}` | {code} | {pct} | `{compact(c, e)}` |")
        md.append("")
        if dead:
            md += [f"**Never filled** ({len(dead)} column"
                   f"{'s' if len(dead) != 1 else ''}): "
                   + ", ".join(f"`{c}`" for c in dead) + ".", ""]

        # The database comments stay verbose - nobody browsing a table in
        # Supabase has the legend in front of them.
        for c, (src, why, e) in notes.items():
            note = f"{src}. {why}"
            if src != "never filled":
                note += f" Expression: {e[:120]}"
            note = note.replace("'", "''")
            sql.append(f"comment on column public.{table}.{c} is\n"
                       f"    '{note}';")
        sql.append("")

    os.makedirs("docs", exist_ok=True)
    if args.stdout:
        print("\n".join(md))
        return
    io.open("docs/columns.md", "w", encoding="utf-8").write("\n".join(md))
    io.open("docs/column_comments.sql", "w", encoding="utf-8").write("\n".join(sql))
    print("\n  wrote docs/columns.md and docs/column_comments.sql")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"ERROR: {exc}")
