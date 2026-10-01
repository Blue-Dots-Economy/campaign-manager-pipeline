# Campaign Manager Pipeline

Fetches call data from the Raya API, transforms it, and pushes it into Supabase
(`kkb_mastersheet`).

## Layout

The Python modules at the root import each other, so they stay side by side.
Everything else is grouped.

| Path | What |
| --- | --- |
| `main.py` | KKB entrypoint — push a batch to Supabase |
| `push_dkb.py` | DKB (employer) push |
| `pipeline.py` | Shared: agent/batch listing, row building, campaign-name lookup, JFC mapping |
| `raya_client.py` | Raya API reads — agents, batches, contacts, per-call transcripts |
| `transform.py` / `transform_dkb.py` / `transform_trrain.py` | Pure transformation, a (batch, contact) pair to a row. No network |
| `supabase_client.py` | Supabase reads and writes |
| `backfill_fields.py` | Recompute fields on rows already pushed |
| `build_seeker_journey.py` / `build_provider_journey.py` | Roll the call tables up to one row per person |
| `call_confidence.py` / `score_confidence.py` | The call confidence score, and the job that writes it |
| `load_bluedot.py` | Loads the Blue Dot S3 dumps into `public.bluedot_*` |
| `load_inbound.py` | Inbound calls, which come through no batch |
| `bot_schemas.py` | Per-bot `agent_args` schemas, with `--check` against Raya |
| `sheet_format.py` | Shared colour scheme and column widths for the output sheets |
| `sql/` | Every DDL statement, in the order it was applied |
| `data/` | `callids_to_campaignname.xlsx` (**required at runtime**), agent ids, reference sheets |
| `docs/` | SETUP.md (Blue Dot fetch), the intent-score explainer |
| `bluedot_fetch/` | Standalone: pulls the non-PII S3 dumps. Own `.env` and `requirements.txt` |
| `dumps/`, `seeker_csv_dumps_*/` | Blue Dot exports the rollup scripts read. Not code |
| `_deprecated/` | Superseded, kept only until you say delete |

## Tables

| Table | Grain | Rows |
| --- | --- | --- |
| `kkb_mastersheet` | one row per seeker call | 63,379 |
| `dkb_mastersheet` | one row per employer call | 14,994 |
| `dkb_newjobs` | child of `dkb_mastersheet` on `call_id` | 123 |
| `aggregated_seeker_journey` | one row per Blue Dot seeker | 54,635 |
| `aggregated_provider_journey` | one row per Blue Dot provider | 3,836 |
| `bluedot_users` / `bluedot_items` / `bluedot_item_actions` | the S3 dumps, split by `instance` | 102,923 |

## Rebuild order

The aggregates are snapshots, and the confidence score derives from them, so
order matters:

```powershell
python main.py ; python push_dkb.py                        # new calls -> mastersheets
python build_seeker_journey.py ; python build_provider_journey.py
python score_confidence.py --table seeker
python score_confidence.py --table provider
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env   # then fill in RAYA_API_KEY, SUPABASE_URL, SUPABASE_SECRET_KEY
```

## Run

Interactive — lists agents, then shows which of that agent's batches are not yet
in Supabase and lets you choose:

```powershell
python main.py
```

Non-interactive — pushes every batch for one agent:

```powershell
python main.py --agent-id "<raya-agent-uuid>"
```

All batches under an agent are treated the same; there is no name-based filtering.

## How rows are built

`transform.make_row` maps one Raya contact to one `kkb_mastersheet` row. Three
fields are computed rather than copied:

- **`intent_score` / `intent_score_reasoning`** — recomputed for every row from
  an additive rubric (call duration, `jobs_shown`, `call_engaged`, plus
  short-circuits for applied / apply-failed / never-dialled). Raya's own
  `intent_score` is deliberately ignored, since most agents never emit it.
- **`apply_api_success`** — read from the actual `apply_job` tool-call result in
  the call transcript (a failed apply shows up as
  `[Error: apply_job request failed (HTTP 404).]`). Falls back to the
  `applied_to_job` / `jobs_failed_to_apply` fields when no transcript is available.
- **`campaign_name` / `jfc_campaign`** — looked up in
  `callids_to_campaignname.xlsx`. Raya's own batch name is an auto-generated
  codename ("Steady Cinder 182-UP"), not the real campaign name, so it is only
  used as a fallback when a call has no entry in the sheet.

### The campaign-name sheet is mixed-format

Its "Call ID" column contains **two** kinds of key, and both must be checked:

- campaigns up to 2026-06-07 use Raya's `call.uuid`
- everything after uses Raya's numeric `call.id`

`contact_id` is **not** a valid key — it shares a 7-digit range with `call.id`,
so matching on it produces false positives. See `pipeline.campaign_lookup_keys`.

## Backfills

Only needed for rows pushed *before* a field existed or was computed correctly.
New pushes already include everything.

```powershell
# real campaign names (cheap - no transcript fetch)
python backfill_fields.py --agent-id "<uuid>" --fields campaign_name

# intent_score + apply_api_success (slow - one transcript fetch per contact)
python backfill_fields.py --agent-id "<uuid>" --fields intent

# campaign_day / campaign_date
python backfill_fields.py --agent-id "<uuid>" --fields campaign
```

Writes go through a bulk upsert on the `call_id` unique constraint, so a
backfill of ~30k rows takes about a minute. `--agent-id` can be repeated.

## Notes and gotchas

- **Raya paginates everything.** `limit` defaults to 10, so all list endpoints
  are wrapped in `fetch_all_*` helpers that page until exhausted.
- **Raya rate-limits.** All reads go through `_get_with_retry`, which backs off
  on HTTP 429. Transcript fetches run on a shared thread pool (32 workers).
- **`kkb_mastersheet.call_id` must stay unique.** Pushes upsert on it, which is
  what stops a re-pushed batch from creating duplicate rows:
  ```sql
  ALTER TABLE public.kkb_mastersheet
    ADD CONSTRAINT kkb_mastersheet_call_id_key UNIQUE (call_id);
  NOTIFY pgrst, 'reload schema';
  ```
- **`call_id` holds Raya's `contact_id`**, not a call UUID — one row per contact,
  even when that contact was dialled several times.
- **The pipeline walks batches → contacts → calls**, so any call whose batch was
  deleted is invisible to it. `GET /api/call?agent_id=<uuid>` lists calls
  directly and would catch those.
- **`Pending` contacts get rows too** (loaded into a batch but never dialled).
  They have no call data, so they score 0 and have no campaign name. Filter them
  out when computing rates.

## Common error

`403 permission denied for table kkb_mastersheet` → grant privileges in the
Supabase SQL Editor:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON public.kkb_mastersheet TO service_role;
NOTIFY pgrst, 'reload schema';
```
