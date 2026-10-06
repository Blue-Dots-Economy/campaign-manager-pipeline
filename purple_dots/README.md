# Purple Dots pipeline

Raya voice-bot calls -> Supabase. Disability support, not jobs: the
vocabulary is beneficiaries, disability types, solution enablers and support
centres. There is no seeker, no employer and no job.

**To run it, see [RUNNING.md](RUNNING.md).**

## Files

    load_purple.py              fetch, transform, push - the entry point
    transform_purple.py         one Raya call -> one purple_dots_calls row
    raya_client.py              Raya API, standalone copy
    sql/create_purple_dots.sql  the tables
    Dockerfile, .dockerignore   the container
    .env.example                what you supply

## Why this is a separate folder

Different programme, different Supabase project: Blue Dots is
`vqvonmoktpvfvzlhtiqo`, this is `blzzscjqvaqfwxzlqtfn`. Two credential sets,
two schemas, no shared rows.

**Nothing here imports from the Blue Dots pipeline.** `raya_client.py` is a
copy, not a shared module - a few hundred duplicated lines is the price of
the two projects being unable to reach into each other's data. An old
`bluedot_fetch/.env` once pointed at the wrong project, and anything that
loaded it wrote to the wrong database. Separate folders make that impossible
rather than merely discouraged.

The Blue Dots loader also lists the Purple Dots inbound agent in
`EXCLUDED_AGENTS`, so these calls cannot leak into `kkb_mastersheet`. That
exclusion should stay.

## Where the fields come from

`call_output` carries the outcome, identity, needs, enabler and API-result
fields - 27 of them.

Profile and disability (name, age, gender, address, documents, disability
type and percentage, what they are looking for) are **not in `call_output`**.
They are in the arguments the bot sends its `update_profile` tool, and
`profile_from_tools` digs them out of the transcript. An earlier survey
concluded they were unsourceable by sampling only the pilot agents, whose
tools are reference lookups.

`call_value_score` has no source in either - it is scored by whoever builds
the output sheet, not by the bot. The column exists so a rule can be written
back once we have one.

## The Basti master sheet

Deleted on 6 Oct 2026. It carried names, phone numbers, ages, genders and
addresses for 16 people - exactly what this project exists not to store. It
was needed once, to map `sheet_call_id` to Raya call uuids by phone; those
mappings now live in `purple_dots_calls.sheet_call_id`. Ask the sheet owner
if you need it again.

Its layout is still the output target: a **two-row header** - row 1 names
nine sections (A PASSTHROUGH, B CALL METADATA, C CALL OUTCOME, D IDENTITY,
E PROFILE & DISABILITY, F NEEDS & CHALLENGES, G OPTIONS & ENABLERS,
H API RESULTS, I QUALITY), row 2 names 44 fields. Anything reading or
writing it skips two rows, not one.
