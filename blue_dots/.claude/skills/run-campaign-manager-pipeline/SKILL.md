---
name: run-campaign-manager-pipeline
description: Build, run, verify and debug the campaign_manager_pipeline (Raya voice-bot API to Supabase). Use when asked to run the pipeline, push a batch, run the driver or smoke test, check Supabase connectivity, rebuild the seeker/provider journey, or rescore call confidence.
---

# Running campaign_manager_pipeline

Pulls call data from the Raya voice-bot API, transforms it, and pushes it into
Supabase. No web framework, no build step: a set of Python CLIs.

**Drive it with the committed driver**, not by running the pushers:

```bash
python .claude/skills/run-campaign-manager-pipeline/driver.py all
```

All paths below are relative to the repo root.

## THE ONE THING TO KNOW FIRST

**There is no staging environment.** `SUPABASE_URL` and `RAYA_API_KEY` in
`.env` point at the live database that backs real dashboards and the live
voice-bot platform. `python main.py ...` pushes thousands of rows to
production, and the loaders write to live tables.

So the driver never writes. Every stage runs through a `--dry-run` / `--check`
path or through pure in-memory functions. Verify with the driver; push only
when you actually mean to.

## Prerequisites

Python 3.10+ (developed on 3.13.7). Windows or Linux; this was verified on
Windows 11.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt                   # Linux
```

`requirements.txt` is three packages: `requests`, `python-dotenv` and
`openpyxl`. There is no build step.

`.env` must exist at the repo root with:

```
RAYA_API_KEY=raya_...
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
SUPABASE_PUBLISHABLE_KEY=sb_publishable_...   # optional; enables the RLS check
```

## Run (agent path) — the driver

```bash
# 22 assertions, no network, no credentials, under a second.
# This is the inner loop: most changes land in the transform/scoring layer.
python .claude/skills/run-campaign-manager-pipeline/driver.py transforms

python .claude/skills/run-campaign-manager-pipeline/driver.py check      # env + creds + runtime files
python .claude/skills/run-campaign-manager-pipeline/driver.py connect    # read-only row counts + RLS assertion
python .claude/skills/run-campaign-manager-pipeline/driver.py dryrun     # every stage, dry (~4 min)
python .claude/skills/run-campaign-manager-pipeline/driver.py all
```

Exit code is non-zero if any check fails. `--timeout` (default 1200s) bounds
each dry-run stage.

Verified output of `transforms` and `connect`:

```
== transforms (pure, no network, no credentials) ==
  [ok ] merged_output backfills blanks from earlier attempts
  [ok ] make_row campaign_date is a string  '2026-09-18'
  [ok ] opt-out scores 0 despite intent 9  do not call: seeker asked us to stop (TRRAIN)
  ...
== 22 passed, 0 failed in 0s ==

== connectivity (read-only) ==
  [ok ] kkb_mastersheet readable  71546 rows
  [ok ] anon key cannot read any table  all denied
```

## Direct invocation (no app, no network)

The transforms are pure functions. Import and call them — this is how to check
a scoring or parsing change in a second rather than a twenty-minute push:

```bash
python -c "
import transform as T
c = {'contact_id': 1, 'phone': '9999999999', 'calls': [
  {'call_start_time': '2026-09-18T07:00:00Z', 'call_output': {'call_answered': 'No'}},
  {'call_start_time': '2026-09-17T07:00:00Z', 'call_output': {'call_answered': 'Yes', 'call_engaged': 'Yes'}}]}
print(T.merged_output(c))
print(T.make_row({'id': 1, 'name': 'b'}, c)['campaign_date'])
"
```

```bash
python -c "
import call_confidence as C
print(C.compute_call_confidence({'trrain_do_not_call': True, 'max_intent_score': 9, 'ever_called': True}))
"
# -> (0.0, 'do not call: seeker asked us to stop (TRRAIN)')
```

## Run (writes to production — deliberate only)

```bash
# always dry-run first; it prints fill rates before writing anything
python main.py --dry-run --batch-id 2777 --campaign-name kkb_up_sept21
python main.py           --batch-id 2777 --campaign-name kkb_up_sept21

python push_trrain.py --dry-run          # TRRAIN service-offer calls
python push_dkb.py    --dry-run          # employer calls
# Blue Dot S3 dumps -> public.bluedot_*. Needs dumps/ on disk FIRST; it is
# transient and gets cleaned up, so expect "dumps not found" on a fresh clone:
python bluedot_fetch/fetchdata.py --env bluedot_fetch/GZB.postman_environment.json     --out-dir dumps/UP
python bluedot_fetch/fetchdata.py --env bluedot_fetch/Dharwad.postman_environment.json --out-dir dumps/KA
python load_bluedot.py --check
python load_bluedot.py --public

# the aggregates are snapshots and the score derives from them, so order matters
python build_seeker_journey.py
python build_provider_journey.py
python score_confidence.py --table seeker
python score_confidence.py --table provider

python export_table.py kkb_mastersheet   # -> data/kkb_mastersheet.csv
```

`main.py --batch-id` is repeatable and resolves IDs across every mapped agent.
`--campaign-name` overrides both the mapping sheet and Raya's meaningless
auto-generated batch name ("Quiet Glacier 040").

## Test

There is no test suite. `driver.py transforms` is the closest thing and is the
check to run after touching `transform*.py`, `call_confidence.py` or
`pipeline.py`.

## Gotchas

These all cost real time. None are guessable from the source.

- **`calls[0]` is the LATEST attempt, not the first.** Raya returns attempts
  newest-first. Reading `calls[0]` alone silently discards what a seeker said on
  an earlier attempt when a later retry rang out. `merged_output()` in
  `transform.py` / `transform_dkb.py` now backfills blank fields from earlier
  attempts; the newest real value still wins. This affected 176 rows before it
  was fixed.
- **`dkb_mastersheet.call_id` holds Raya's `contact_id`, NOT `call.id`.** So does
  `trrain_mastersheet.call_id`. Diffing DKB on `call.id` produces a 100%
  "everything is missing" result that is entirely an artefact of the wrong key.
  `kkb_mastersheet` also keys on `contact_id`.
- **Never "check that every module imports".** `finish_dkb.py` used to sit
  here with no `main()` guard, so importing it ran a DKB migration against
  production — which is exactly what a naive import-everything check did once.
  That file has been deleted, but use `py_compile` rather than `import` when
  checking a module is valid; the next one-off script may be written the same
  way.
- **PostgREST caps a page at 1000 rows regardless of `limit`.** Asking for 5000
  and breaking when `len(page) < 5000` silently truncates after one page. Always
  page with `limit=1000` and break on `len(page) < 1000`.
- **PostgREST rejects the whole write if one column is unknown** (`PGRST204`),
  after every transcript has been fetched. `main.check_columns()` compares a
  probe row against the live table up front and fails in two seconds with the
  exact `ALTER TABLE`. Run `driver.py dryrun` before any push.
- **PostgREST pagination without `order` returns unstable pages**, which once
  produced a completely fictitious duplicate count. Every paged read here sets
  `order`.
- **`requests` cannot serialise a `date`.** `campaign_date` must be
  `.isoformat()`'d in the transform or the push dies at the first chunk.
- **Raya sits behind Cloudflare and intermittently returns 522** with an HTML
  body. `_get_with_retry` now retries 429 *and* 5xx and dropped connections;
  before that a twenty-minute push died on a one-second hiccup.
- **Raya's list endpoints default to `limit=10`.** Use the `fetch_all_*`
  wrappers, never the raw `fetch_*`.
- **Raya rate-limits above roughly a dozen concurrent requests.** 32 workers
  trips it; the code uses 6–16.
- **Blue Dot dumps are gzip despite the `.jsonl` extension.** Sniff the magic
  bytes (`\x1f\x8b`); `head` shows binary.
- **`bluedot_fetch/.env` is a DIFFERENT project's credentials** — Keycloak plus a
  `SUPABASE_DB_URL` for another Supabase project entirely. Using it points writes
  at the wrong database.
- **Windows console is cp1252.** Seeker names in Devanagari and ₹ in job data
  raise `UnicodeEncodeError` and kill a run mid-way. Write diagnostics to a UTF-8
  file, or `.encode("ascii", "replace")`. The driver's `say()` does this.
- **`python -u`, always.** Piping to `tail`/`head` buffers everything, so a long
  run appears to produce no output at all.
- **Neither mastersheet has a `loaded_at` column**, so "when was this pushed?"
  cannot be answered from SQL — only from batch IDs present.
- **RLS is enabled on every table and `service_role` bypasses it.** The pipeline
  is unaffected. `driver.py connect` asserts the anon key can read nothing; treat
  a failure there as a security regression.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `PGRST204 Could not find the '<col>' column` | `transform.py` writes a column the table lacks. `driver.py dryrun` prints the exact `ALTER TABLE`. |
| `Failed to fetch agents: 522 <!DOCTYPE html>` | Transient Cloudflare. Retried automatically now; just re-run. |
| `TypeError: cannot unpack non-iterable NoneType` | `classify_apply_job_tool_calls()` returns `True`/`False`/`None`, not a tuple. |
| `Object of type date is not JSON serializable` | A transform emitted a `date`. `.isoformat()` it. |
| `UnicodeEncodeError: 'charmap' codec` | cp1252 console. Redirect to a UTF-8 file. |
| `permission denied for table` (42501) with the service key | Table created without grants. `grant select, insert, update, delete on public.<t> to service_role;` |
| `Invalid schema: bluedot` (PGRST106) | Only `public` is REST-exposed. The Blue Dot tables live in `public` as `bluedot_*`; use `sql/supabase_schema_public.sql`. |
| Rollup writes `phone:<number>` rows for everyone | `seeker_csv_dumps_*` is missing. `build_seeker_journey.py` now recovers the roster from `aggregated_seeker_journey` and refuses to run with no roster at all. |
| `load_bluedot.py`: `dumps not found - run fetchdata.py first` | `dumps/` is transient and not in the repo. Re-fetch with the two `fetchdata.py` lines above; the pre-signed S3 URLs expire in 10 minutes, so just re-run on failure. |
