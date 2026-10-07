# campaign_manager_pipeline — Purple Dots on Postgres

Voice bots call people. This reads those calls out of Raya and loads them
into a local Postgres.

**To run it: [purple_dots/RUNNING.md](purple_dots/RUNNING.md)** — Docker for
the database, one command for the pipeline.

## This branch is Purple Dots only

`main` carries both programmes and writes to Supabase:

| | programme | where it writes |
|---|---|---|
| `blue_dots/` | Kaam Ki Baat, Dhande Ki Baat, TRRAIN — jobs | Supabase `vqvonmoktpvfvzlhtiqo` |
| `purple_dots/` | Purple Dots — disability support | Supabase `blzzscjqvaqfwxzlqtfn` |

Here, `blue_dots/` is deleted and Purple Dots writes to Postgres instead.
Blue Dots has no Postgres build and is not part of this work, so carrying a
copy of it here would only mean two versions of it drifting apart.

Which is also why **this branch is not meant to be merged**. Merging would
delete Blue Dots from `main`. See the pull request for the storage-layer
diff.

## Keeping the two in step

Only `purple_dots/db.py` and the credentials differ between the branches.
The loader, the transform, the Raya client and the batch classification are
identical, so a fix to any of those belongs on both.
