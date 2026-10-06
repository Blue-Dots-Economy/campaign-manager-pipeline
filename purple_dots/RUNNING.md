# Running the Purple Dots loader

Raya calls -> Supabase. A batch job: it runs, writes, exits. You need
Docker and three credentials; you do not need Python.

## Everyone shares one database

There is no staging copy. **Re-running is safe** - rows upsert on `call_id`,
so loading a batch twice gives `0 new`. **A wrong scope is not** - a run
without `--batch` restores ~950 test rows that were deleted on purpose on
30 September 2026, and there is no undo.

Say in the team channel which batch you are loading. We share one service
key, so the row count is the only record of who ran what.

## Setup, once

Create `.env`:

```
SUPABASE_URL=https://blzzscjqvaqfwxzlqtfn.supabase.co
SUPABASE_SECRET_KEY=<Purple Dots service key>
RAYA_API_KEY=<ALIMCO / Purple Dots Raya key>
```

The URL is correct as written; ask the data team for the keys. The Supabase
one is a **service key** - it bypasses row-level security. Never commit it,
paste it in chat, or pass it to `docker build`; anything baked into an image
travels with it. Run time only, via `--env-file`.

```
cd purple_dots
docker build -t purple-dots .
```

Rebuild whenever you pull. The tables already exist; on a fresh Supabase
project run `sql/create_purple_dots.sql` first.

## Every run: three commands

**1. Is everything reachable?** Two seconds, exits non-zero on failure.

```
docker run --rm --env-file .env purple-dots --check
```

```
Raya
  [ok ] api reachable            4 agents visible
  [ok ] Purple Dots agents       2 of 2 found
Supabase
  [ok ] purple_dots_calls        877 rows
  [ok ] purple_dots_connections  0 rows
  [ok ] write permission
```

**2. What is new?**

```
docker run --rm --env-file .env purple-dots --batches
```

```
batch    dialled  total   in db  created     name
3018         223    307     660  2026-09-30  'Basti_1_Final'
3031          83    100     186  2026-10-01  'Tmf_Basti_1oct'
2992           1      1       0  2026-09-30  'purplec'   <-- NOT LOADED
1852           0      1       0  2026-06-26  'Purple dot test'   never dialled
```

**dialled** is contacts actually called - judge a batch by that, not by its
name. A real campaign calls hundreds; the test batches called one.
**in db** means it is done. `<-- NOT LOADED` rows are the only ones worth a
second look.

**3. Load it.**

```
docker run --rm --env-file .env purple-dots --batch 3031
```

Then re-run `--batches` and check `in db` went up. Unsure about a batch id?
`--dry-run --batch 3031` does everything except the write.

Real batches as of 6 October 2026: **2385, 2441, 3018, 3031**. Anything
named `purplec`, `testpurplec` or `_with_reference` is testing.

## Two things that look wrong and are not

`purple_dots_connections  0 rows` - across all 877 calls the bot called
`connect_provider` once, with an empty `item_id` for both sides. The loader
refuses to write a connection to nobody. An accurate zero, not a failure.

The container writes no files. Rows carry no names or phone numbers by
design. `--csv` writes them to `/app/data` if you genuinely need them on
disk - mount it, and delete the file afterwards.

## When something goes wrong

| what you see | what it means |
|---|---|
| `missing: RAYA_API_KEY` | no `--env-file .env`, or the file is short a line |
| `Purple Dots agents 0 of 2 found` | the Raya key is for the wrong account |
| `purple_dots_calls does not exist` | run `sql/create_purple_dots.sql` |
| `write permission FAIL 403` | that is the anon key, not the service key |
| `SUPABASE_URL points at the Blue Dots project` | wrong project. The loader stops rather than writing disability data into the seeker database |
| `0 new` after `--batch` | already loaded. Nothing was written |

Anything else: run `--check`. It fails in two seconds instead of twenty
minutes.
