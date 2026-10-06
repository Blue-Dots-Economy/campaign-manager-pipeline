# Running the Purple Dots loader

Raya calls -> Supabase. Runs, writes, exits. Needs Docker and three
credentials; no Python.

**We all share one live database.** Re-running is safe - rows upsert on
`call_id`, so a second load gives `0 new`. A run **without `--batch`** is
not: it restores ~950 test rows deleted on purpose on 30 Sept 2026, with no
undo. Say in the channel which batch you are loading - we share one service
key, so the row count is the only record of who ran what.

## Setup, once

`.env`:

```
SUPABASE_URL=https://blzzscjqvaqfwxzlqtfn.supabase.co
SUPABASE_SECRET_KEY=<Purple Dots service key>
RAYA_API_KEY=<ALIMCO / Purple Dots Raya key>
```

The URL is correct as written; ask the data team for the keys. The Supabase
one bypasses row-level security - never commit it or pass it to
`docker build`, since anything baked into an image travels with it.

```
docker build -t purple-dots .
```

Rebuild whenever you pull. Tables exist already; on a fresh project run
`sql/create_purple_dots.sql`.

## Every run

```
docker run --rm --env-file .env purple-dots --check     # 2s, all connections
docker run --rm --env-file .env purple-dots --batches   # what is new
docker run --rm --env-file .env purple-dots --batch 3031
```

```
batch    dialled  total   in db  created     name
3031          83    100     186  2026-10-01  'Tmf_Basti_1oct'
2992           1      1       0  2026-09-30  'purplec'   <-- NOT LOADED
```

Judge a batch by **dialled**, not by its name - a real campaign calls
hundreds, the test batches called one. **in db** means it is done;
`<-- NOT LOADED` rows are the only ones worth a look. Real batches as of
6 Oct 2026: **2385, 2441, 3018, 3031**. Unsure? `--dry-run --batch N` does
everything except the write.

## Two things that look wrong and are not

`purple_dots_connections 0 rows` - the bot called `connect_provider` once in
877 calls, with an empty `item_id` on both sides. An accurate zero.

No files are written, and rows carry no names or phone numbers by design.
`--csv` writes to `/app/data` if you need them on disk; mount it.

## When something goes wrong

| what you see | what it means |
|---|---|
| `missing: RAYA_API_KEY` | no `--env-file .env`, or the file is short a line |
| `Purple Dots agents 0 of 2 found` | Raya key is for the wrong account |
| `purple_dots_calls does not exist` | run `sql/create_purple_dots.sql` |
| `write permission FAIL 403` | that is the anon key, not the service key |
| `points at the Blue Dots project` | wrong project; it stops rather than write disability data into the seeker database |
| `0 new` | already loaded. Nothing was written |

Anything else: `--check` first. It fails in two seconds, not twenty minutes.
