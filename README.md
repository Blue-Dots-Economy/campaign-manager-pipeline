# campaign_manager_pipeline

Voice bots call people. This reads those calls out of Raya, stores them in
Supabase, and fills the Google Sheets the team works from.

    blue_dots/     Kaam Ki Baat, Dhande Ki Baat, TRRAIN   vqvonmoktpvfvzlhtiqo
    purple_dots/   Purple Dots (disability support)       blzzscjqvaqfwxzlqtfn

Separate Supabase projects, Raya accounts and `.env` files. They share the
virtualenv at the root and nothing else — neither folder imports from the
other, so one can never write to the other's database.

## How a call becomes a row

Raya gives us three things per call, and they are not equally trustworthy:

| source | what it is | trust |
|---|---|---|
| the call record | who, when, how long, dialler status | fact |
| the transcript + tool calls | what was said, and which API calls fired | strong evidence |
| `call_output` | an AI's summary of the call | a claim, sometimes wrong |

So when `applied_to_job` (summary) disagrees with `apply_api_success`
(transcript), believe the transcript. A few fields — intent score, call
confidence — we compute ourselves and are ours to change.

## Running it

`cd` into the folder first. Every script uses paths relative to its own folder
and a bare `load_dotenv()`, so running from the root will not find `.env`.
Everything takes `--dry-run`: it writes CSVs and touches nothing remote.

    cd blue_dots
    python push_sheets.py --dry-run            # Rozgar sheet
    python push_he_sheets.py --dry-run         # HE sheet
    python push_inbound_sheets.py --dry-run    # inbound tabs
    python main.py --dry-run --batch-id 2777 --campaign-name kkb_up_sept21
    python bot_schemas.py --check              # bot input schemas vs Raya

    cd purple_dots
    python load_purple.py --dry-run --batch 3018

Campaign names follow `<programme>_<jfc>_<segment>_<month><day>`, lowercase,
month before day: `kkb_up_sept21`, `trrain_gzb_aug18`. Raya's own batch names
are auto-generated nonsense ("Quiet Glacier 040"), so pass the real one.

## Gotchas

1. **Scope the Purple Dots loader.** Only batches 2385 and 3018 are real
   campaigns; the rest was test data and was deleted deliberately. An
   unscoped `load_purple.py` puts 950 rows straight back. Use `--batch`.
2. **Sheet writes merge, never clear.** Each pusher refreshes the columns it
   owns, appends new rows, and leaves hand-typed columns alone. A re-run with
   no new data must report `0 refreshed, 0 appended` — anything else means a
   cell is oscillating, usually a number Sheets reformats (`30000` → `30,000`).
3. **`kkb_mastersheet` is a view, not a table.** Adding a column means
   altering whatever sits underneath and updating the view.
4. **Never `import` a module just to check it parses.** A since-deleted
   one-off had no `main()` guard and ran a production migration on import.
   Use `py_compile`.
5. **Raya records no call direction.** Inbound is inferred — `caller_no` set,
   `to_number` empty — and inbound calls belong to no batch, so walking
   batches misses them entirely. That is what `load_inbound.py` is for.

Purple Dots stores **no personal data**: no name, phone, age, address,
transcript or recording. It keeps `profile_item_id` and joins back to the
platform for anything else, because the table holds disability status. RLS is
on with no policies, so only `service_role` can read it.

## Next

`blue_dots/README.md` · `purple_dots/README.md` ·
`blue_dots/docs/bot_input_formats.txt` (what `agent_args` each bot expects) ·
`blue_dots/bot_schemas.py` (the same in code, with `--check`) ·
`blue_dots/docs/SETUP.md`

`/run-campaign-manager-pipeline` drives Blue Dots read-only; `driver.py check`
is a two-second credential and runtime preflight.
