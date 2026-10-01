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
    "purple_dots_calls": ("transform_purple.py", "make_row"),
    "purple_dots_connections": ("transform_purple.py", "make_connections"),
}
# heavy text columns: counted with a HEAD request rather than pulled
# Purple Dots stores no long text - no transcript, no summary, no recording -
# so nothing needs the count-only treatment.
HEAVY: set[str] = set()

# where a value comes from, in trust order. The order matters and is the
# single most useful thing in the catalogue: when the bot's own summary
# disagrees with the transcript, the transcript is right.
SOURCES = [
    ("Raya · call record", ("call.get", "contact.get", "batch.get",
                            "call_date", "first_call"),
     "Recorded by the dialler. Fact."),
    ("Raya · agent_args", ("agent_args", "args.get"),
     "What WE sent the bot when the batch was made. Echoed back unchanged."),
    ("Raya · transcript", ("raw_transcript", "prof.get", "transcript",
                           "tool_call", "_as_dict"),
     "Dug out of the conversation's tool calls. The profile ids live only in "
     "the arguments the bot sent to update_profile, nowhere else."),
    ("Raya · call_output", ("call_output", "merged_output", "out.get",
                            "latest_answer", "any_yes"),
     "An AI's summary written after the call. A claim, not a measurement, "
     "and only present when somebody answered."),
    ("computed locally", ("OUTCOME_AS_STATUS", "is_inbound", "call_date_ist",
                          "call_datetime_ist", "whole(", "number(",
                          "str_list(", "yes_no(", "clean("),
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

    Raya API                  ALIMCO / Purple Dots account, 3 agents
       |
       |  raya_client.py      reads only. A standalone COPY of the Blue Dots
       |                      one - the two folders never import each other,
       v                      so neither can write to the other's database
    transform_purple.py       pure functions. One call -> one row, plus one
       |                      row per provider connection. No network
       v
    load_purple.py            the only thing that writes
       |
       v
    Supabase                  purple_dots_calls, purple_dots_connections

One entry point, `load_purple.py`. Scope every run:

    python load_purple.py --agents              what can this key see?
    python load_purple.py --dry-run --batch 3018
    python load_purple.py --batch 3018

**Only batches 2385 and 3018 are real campaigns.** Everything else on that
agent is testing and was deleted deliberately, so an unscoped run puts 950
test rows back.

### No personal data, on purpose

The bot learns a beneficiary's name, age, gender, address, documents and
disability percentage, and writes them to the platform through its
`update_profile` tool. **None of that is read into these tables.** What is
kept is `profile_item_id` - the platform's own id for that person - which is
enough to join back when someone needs the detail, and keeps a named
individual's disability status out of a second database. There is no phone
number, no name, no transcript and no recording here.

`disability_category_mapped` is the one judgement call: a coded category
rather than free text, but still health information attached to an id. It is
kept because without it the table cannot answer a single question about the
programme.

Both tables have RLS on with **zero policies**, so only `service_role` can
read them.

Two provenance notes specific to this project: `profile_item_id` exists only
in the arguments the bot sent to `update_profile`, never in the summary; and
for `call_status` the batch contact says "Unanswered" where the per-call
endpoint says "Failure" for the same call - the contact's word wins.

### Two things about the ids

`call_id` is the call's own **uuid**. `contact_id` was the obvious key - it is
what the Blue Dots tables use - but a batch contact with two attempts is two
calls sharing one contact_id, and this table is one row per call.

`sheet_call_id` is the id the Basti master sheet uses. It is **not** a Raya
identifier: the sheet's range (4694548-4699905) does not overlap Raya's
contact_ids (2812932-3289859). It was mapped once by phone, from an older
export of the sheet that still carried the numbers, and the phone was never
stored.

## The journey, step by step

Shorter than the Blue Dots one, and differently shaped: there are no batches
to walk here.

### 1. Discovery

`GET /api/agent` lists what the key can see. The three Purple Dots bots are
named explicitly in `AGENTS` rather than matched on "purple" in the
name - a rename, or a new "Purple-dots-v3", should be a decision somebody
makes, not something a substring quietly picks up and loads into a live
table.

### 2. Fetch

None of these agents has a batch in the account this key can see, so the
loader walks `GET /api/call?agent_id=` per agent instead of
`GET /api/batch/{id}/contacts`. That endpoint returns every call an agent
made, batch or no batch, so it is the right one either way. `--batch` scopes
a run to one batch and `--agent` to one agent.

**Always scope the run.** Only batches 2385 and 3018 are real campaigns;
everything else on those agents was testing and was deleted deliberately, so
an unscoped `load_purple.py` puts about 950 test rows straight back.
`existing_ids()` pages the table first and skips any call already present, but
that only helps against re-pushing the same thing - it will not stop test data
arriving the first time.

### 3. Fetch the per-call detail

`GET /api/call/{uuid}` for the transcript and its tool calls. This is where
`profile_item_id` comes from, and the only place it exists - it is in the
arguments the bot sent to `update_profile`, never in the summary.

### 4. Transform, with the privacy boundary in it

`make_row()` reads the `update_profile` tool arguments and keeps exactly three
things from them: `item_id`, `user_id`, `acting_as_user_id`. Everything else
the bot learned - name, age, gender, address, documents, disability
percentage - is deliberately dropped on the floor. That tuple **is** the
privacy boundary, and it lives in `PROFILE_FIELDS` in `transform_purple.py`
so there is one place to look.

Two other judgement calls are made here. `call_status` takes the batch
contact's word over the per-call endpoint's, because for the same call the
contact says "Unanswered" where the call says "Failure". And `call_id` is the
call's own uuid rather than `contact_id`, because a contact with two attempts
is two calls and this table is one row per call - `contact_id` is kept as its
own column.

`make_connections()` is the one-to-many: one row per provider actually
connected, keyed `(call_id, provider_item_id)`.

### 5. Write

Chunks of 200 with `on_conflict=call_id`. Two PostgREST constraints are
handled before anything is sent, both learned the hard way:

- Duplicate keys inside one chunk are collapsed first, because PostgREST
  answers `ON CONFLICT DO UPDATE command cannot affect row a second time` and
  rejects the batch.
- Every object in a chunk is squared off to the same key set, because
  PostgREST rejects the whole batch unless they match.

There is no sheet writer and no rollup on this side yet.

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

    md = ["# Column catalogue - Purple Dots",
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
