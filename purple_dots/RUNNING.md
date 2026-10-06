# Running the Purple Dots loader

Pulls Purple Dots call data out of Raya and loads it into Supabase. It is a
batch job: it runs, writes, and exits. Nothing is left behind on your machine.

You need Docker and three credentials. You do not need Python.

---

## Before anything: this is the live database

Everyone runs against the **same Supabase project**. There is no staging copy
and no per-person sandbox. What you load, the whole team sees, and it is what
the reporting reads.

Two things follow:

**Re-running is safe.** Rows are upserted on `call_id`, and a call already in
the table is skipped before it is even fetched. Two people loading batch 3031
at the same time cannot produce duplicates, and loading a batch twice is a
no-op — you will see `0 new`.

**A wrong scope is not safe, and there is no undo.** A run without `--batch`
takes every call the key can see, including roughly 950 test rows that were
deleted deliberately on 30 September 2026. Nobody can roll that back; it would
have to be identified and deleted again by hand.

So: say in the team channel which batch you are loading before you load it.
Not because of conflicts — there are none — but because an unexpected jump in
the row count is otherwise a mystery for whoever notices it next.

Everyone also shares one service key, so Supabase cannot tell you who ran
what. The row count is the only record, which is the other reason to announce
it.

---

## One-time setup

**1. Credentials.** Create a file called `.env` with these three lines:

```
SUPABASE_URL=https://blzzscjqvaqfwxzlqtfn.supabase.co
SUPABASE_SECRET_KEY=<the Purple Dots service key>
RAYA_API_KEY=<the ALIMCO / Purple Dots Raya key>
```

The URL above is the shared Purple Dots project and is correct as written.
Ask the data team for the two keys — and treat them as production
credentials, because they are: the Supabase one is a **service key**, which
bypasses row-level security and can read and write everything in that
project.

Do not commit `.env`, do not paste the keys into a chat, and do not put them
in a `docker build` argument — anything baked into an image travels with it.
They are only ever passed at run time via `--env-file`.

**2. Build the image.**

```bash
cd purple_dots
docker build -t purple-dots .
```

Takes about a minute. Repeat it whenever you pull new code.

**3. The tables** only need creating once, and they already exist. If you are
pointing at a fresh Supabase project, run `sql/create_purple_dots.sql` in the
SQL editor first.

---

## Every run: three commands

### 1. Check everything is reachable

```bash
docker run --rm --env-file .env purple-dots --check
```

```
Raya
  [ok ] RAYA_API_KEY set         raya_g0Xz...
  [ok ] api reachable            4 agents visible
  [ok ] Purple Dots agents       2 of 2 found
Supabase
  [ok ] SUPABASE_URL set         https://blzzscjq....supabase.co
  [ok ] SUPABASE_SECRET_KEY set  ***
  [ok ] purple_dots_calls        877 rows
  [ok ] purple_dots_connections  0 rows
  [ok ] write permission

all checks passed
```

Two seconds, and it exits non-zero if anything fails — so it works in a cron
job or a CI step too. `purple_dots_connections  0 rows` is expected, not a
fault: see **What the numbers mean** below.

### 2. See what is new

```bash
docker run --rm --env-file .env purple-dots --batches
```

```
batch    dialled  total   in db  created     name
2385           8     15      16  2026-08-19  'Purple Dots_Basti_Day1_19thAug'
2441           6      9      15  2026-08-25  'Purple Dots_Basti_Day2_25thAug'
3018         223    307     660  2026-09-30  'Basti_1_Final'
3031          83    100     186  2026-10-01  'Tmf_Basti_1oct'
2992           1      1       0  2026-09-30  'purplec'   <-- NOT LOADED
1852           0      1       0  2026-06-26  'Purple dot test'   never dialled
```

Read it like this:

| column | meaning |
|---|---|
| **dialled** | contacts actually called. `0` means nothing happened — nothing to load whatever the name says |
| **total** | contacts loaded into the batch, dialled or not |
| **in db** | rows already in Supabase. A number here means it is done |
| `<-- NOT LOADED` | dialled, but absent. The only rows worth a second look |

### 3. Load the batch you want

```bash
docker run --rm --env-file .env purple-dots --batch 3031
```

Then re-run `--batches` and check `in db` went up.

---

## The one rule

**Always pass `--batch`.** Without it the loader takes every call the key can
see, which puts back roughly 950 test rows that were deleted deliberately on
30 September 2026. There is no undo.

Running with no arguments at all is safe — it does a dry run — so forgetting
the flag entirely costs you nothing. It is a *wrong* batch id that bites.

### Is this batch real or test?

Judge it by **dialled**, not by the name. A real campaign calls hundreds; the
test batches called one. Known real as of 6 October 2026:

| batch | campaign |
|---|---|
| 2385 | Purple Dots_Basti_Day1_19thAug |
| 2441 | Purple Dots_Basti_Day2_25thAug |
| 3018 | Basti_1_Final |
| 3031 | Tmf_Basti_1oct |

Anything named `purplec`, `testpurplec` or `_with_reference` is testing.

### If you are unsure

```bash
docker run --rm --env-file .env purple-dots --dry-run --batch 3031
```

Does everything except the write, and tells you how many rows would land. Use
it when the batch id is new or you typed it from memory; skip it otherwise.

---

## What the numbers mean

**`purple_dots_calls`** is one row per call — 877 as of 6 October 2026.

**`purple_dots_connections`** is one row per provider a beneficiary was
actually connected to, and it is **empty on purpose**. Across all 877 calls
the bot called `connect_provider` once, and that call sent an empty
`item_id` for both the seeker and the provider, so there was no connection to
record. The loader refuses to write a connection to nobody rather than
inventing one. It is an accurate zero, not a loading failure.

Three other calls report `providers_connected = 3` while having no transcript
at all — that is Raya's summariser describing an outcome that left no trace.
Trust the tool calls over the summary.

---

## It stores nothing

The container writes no files. The rows carry no names and no phone numbers
by design — the Purple Dots tables hold platform ids and a coded disability
category, and the beneficiary's name, age, address and documents stay on the
platform where they already are.

If you genuinely need the rows on disk, `--csv` writes them, and you should
delete the file when you are done:

```bash
docker run --rm --env-file .env -v "$PWD/out:/app/data" purple-dots \
    --dry-run --batch 3031 --csv
```

---

## When something goes wrong

| what you see | what it means |
|---|---|
| `missing: RAYA_API_KEY` | `--env-file .env` was not passed, or the file is short a line |
| `Purple Dots agents 0 of 2 found` | the Raya key is for the wrong account |
| `purple_dots_calls does not exist` | run `sql/create_purple_dots.sql` |
| `write permission FAIL 403` | you have the anon key, not the service key |
| `SUPABASE_URL points at the Blue Dots project` | wrong project. The loader stops rather than writing disability data into the seeker database |
| `0 new` after `--batch` | already loaded. Safe, and nothing was written |

Anything else: run `--check` first. It fails in two seconds instead of twenty
minutes.
