Written for: whoever picks this up next — you, or another engineer.

# Purple Dots pipeline

Pulls Purple Dots voice-bot calls out of Raya and into their own Supabase
project, then fills the Purple Dots output sheets.

## Why this is a separate folder

Purple Dots is a different programme in a different Supabase project, and
nothing here shares a table, a key or a row with Blue Dots.

`../campaign_manager_pipeline` covers Blue Dots — Kaam Ki Baat (seekers),
Dhande Ki Baat (employers) and TRRAIN. It talks to Supabase project
`vqvonmoktpvfvzlhtiqo`. This one talks to `blzzscjqvaqfwxzlqtfn`. Two
projects, two credential sets, two schemas.

Keeping them in one folder would mean one `.env` holding two Supabase
projects' keys, and every script needing to know which one it meant. That is
exactly the mistake that has already cost time on this codebase: an old
`bluedot_fetch/.env` pointed at a *different* Supabase project, and anything
that loaded it wrote to the wrong database. Separate folders make that
impossible rather than merely discouraged.

The Blue Dots pipeline already refuses Purple Dots traffic — the inbound
loader lists `2f57fa97-…` (Purple-dots-with-APIs-V2-Inbound) in
`EXCLUDED_AGENTS`, so those calls cannot leak into `kkb_mastersheet`. That
exclusion should stay.

**Nothing in this folder imports from `../campaign_manager_pipeline`.**
`raya_client.py` is a copy rather than a shared import. That duplicates a few
hundred lines, which is the price of the two projects being unable to
accidentally reach into each other's data.

## Domain

Purple Dots is disability support, not jobs. A call reaches a person with a
disability (or someone calling on their behalf), works out what they need,
and maps it to solution categories and local providers. The vocabulary is
beneficiaries, disability types, solution enablers and support centres —
there is no seeker, no employer and no job.

## The output sheet

`data/Purple_Dots_Basti_Master Sheet - Purple_Dots_Day1.csv` is the format,
copied here from the Blue Dots folder where it did not belong.

It carries a **two-row header**: row 1 names nine sections (`A — PASSTHROUGH`
through `I — QUALITY`), row 2 names the 44 fields. Anything reading or writing
it has to skip two rows, not one.

    A  PASSTHROUGH            who we called and who referred them
    B  CALL METADATA          ids, timing, recording
    C  CALL OUTCOME           status, phase reached, engagement
    D  IDENTITY               calling for themselves or someone else
    E  PROFILE & DISABILITY   name, age, gender, documents, disability
    F  NEEDS & CHALLENGES     what they said they need
    G  OPTIONS & ENABLERS     what we mapped it to
    H  API RESULTS            profile update and provider connect outcomes
    I  QUALITY                value score, drop reason, notes

## State of play

Blocked on two things, both external:

**1. The Raya account is wrong, or the bot is.** This Raya key sees 11 Purple
Dots agents holding 308 calls between them and **zero batches** — pilots and
demos. The Day1 sheet's `call_id 4695231` is a numeric batch contact_id, and
no Purple Dots batch exists in this account. `Basti Launch Agent` has 19
calls and emits **no `call_output` at all**, so it did not produce that sheet.
Either Purple Dots production runs on a different Raya account, or the sheet
was filled from something other than this API.

**2. No Supabase credentials.** The connection string supplied still had
`[YOUR-PASSWORD]` in it, and the loader talks PostgREST rather than Postgres
directly. See `.env.example`.

Every sheet section except one is sourced and tested against real calls.

Sections **C, D, F, G and H** come straight from `call_output` — 27 fields.

Section **E — PROFILE & DISABILITY** is not in `call_output` at all, which at
first made it look unsourceable. It is in the **arguments the bot sends to its
`update_profile` tool**: `item_state` carries all ten fields
(`beneficiary_name`, `age`, `gender`, `address`, `documents_available`,
`disability_type`, `disability_percentage`, `looking_for`,
`looking_for_details`, `mobile_number`). `profile_from_tools` in
`transform_purple.py` digs them out of the transcript, the same way the Blue
Dots pipeline recovers failed job applications from its own tool calls.

An earlier survey missed this because it sampled only the three pilot agents,
whose tools really are just reference lookups (`Disabilitytypes`,
`DisabilitySchemes`, `ProvidersList`). The bots that complete the journey
call `get_profile`, `update_profile`, `get_providers_for_location` and
`connect_provider` as well.

One field still has no source: **`call_value_score`**. It is not in
`call_output` and not in any tool call, yet the sheet carries it on every
row — so it is scored by whoever builds the sheet, not by the bot. The column
exists so a score can be written back once we know the rule.

## Files

    raya_client.py              Raya API, standalone copy
    transform_purple.py         one Raya call -> one purple_dots_calls row
    load_purple.py              fetch, transform, push
    sql/create_purple_dots.sql  the table
    .env.example                what you need to supply

## What is loaded, and what is not

Only **batch 2385** - `Purple Dots_Basti_Day1_19thAug`, 16 calls - is real.
Everything else this agent has produced is testing, confirmed by the sheet
owner on 30 Sept 2026, and was deleted from Supabase on purpose. That
included 933 batch-less calls holding 151 completed journeys and 235 provider
connections; they looked like production and were not.

So an unscoped run puts the test data back. Scope it:

    python load_purple.py --batch 2385

`sheet_call_id` on each row is the id the Basti master sheet uses. It is not a
Raya identifier - the sheet's range (4694548-4699905) does not overlap Raya's
contact_ids (2812932-3289859) - so it was mapped once by phone, from an older
export of the sheet that still carried the numbers, and the phone was never
stored. The recording URL in the sheet also embeds the Raya call uuid, which
cross-checks 8 of the 15 (the answered ones); both methods agree.

## Running it

    cp .env.example .env        # then fill it in
    python load_purple.py --dry-run          # writes a CSV, touches nothing
    python load_purple.py --dry-run --limit 20
    python load_purple.py                    # pushes to Supabase
