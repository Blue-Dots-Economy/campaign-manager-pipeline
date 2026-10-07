# Purple Dots pipeline

Raya voice-bot calls -> Supabase. Disability support, not jobs: beneficiaries,
disability types, solution enablers and support centres. No seeker, no
employer, no job.

**To run it: [RUNNING.md](RUNNING.md).**

    run_pipeline.py             all four stages in one command
    load_purple.py              calls: fetch, transform, push
    sync_s3.py                  platform data: users, items, actions
    transform_purple.py         one Raya call -> one purple_dots_calls row
    raya_client.py              Raya API, standalone copy
    db.py                       every read and write to the database
    sql/                        the tables
    Dockerfile, .dockerignore   the container

Two sources, two scripts, no overlap. `load_purple.py` records what happened
on the phone; `sync_s3.py` records what exists on the platform. They meet
only through `profile_item_id`.

## Separate from Blue Dots on purpose

Different Supabase project (`blzzscjqvaqfwxzlqtfn`, not
`vqvonmoktpvfvzlhtiqo`), different credentials, no shared rows. **Nothing
here imports from the Blue Dots pipeline** - `raya_client.py` is a copy, and
the duplication is the price of the two projects being unable to reach into
each other's data. An old `bluedot_fetch/.env` once pointed at the wrong
project and everything loading it wrote to the wrong database; separate
folders make that impossible rather than discouraged.

Blue Dots lists the Purple Dots inbound agent in `EXCLUDED_AGENTS` so these
calls cannot leak into `kkb_mastersheet`. Keep that.

## Where the fields come from

`call_output` carries 27 - outcome, identity, needs, enablers, API results.

Profile and disability (name, age, gender, address, documents, disability
type and percentage) are **not in `call_output`**. They are in the arguments
the bot sends its `update_profile` tool; `profile_from_tools` digs them out
of the transcript. An earlier survey called them unsourceable by sampling
only the pilot agents, whose tools are reference lookups.

`call_value_score` is in neither - whoever builds the output sheet scores it.
The column exists so a rule can be written back later.

## The Basti master sheet

Deleted 6 Oct 2026: names, phones, ages, genders and addresses for 16 people,
exactly what this project exists not to store. Its one use - mapping
`sheet_call_id` to Raya uuids by phone - is already done and in
`purple_dots_calls.sheet_call_id`. Ask the sheet owner if you need it again.

Its layout is still the output target: a **two-row header** (row 1 = nine
sections A PASSTHROUGH .. I QUALITY, row 2 = 44 fields), so anything reading
or writing it skips two rows, not one.
