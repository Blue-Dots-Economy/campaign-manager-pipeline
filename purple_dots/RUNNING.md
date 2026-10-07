# Running the Purple Dots loader

Raya calls -> Supabase. A batch job: it runs, writes, exits.

## Setup, once

Create `.env`:

```
SUPABASE_URL=https://blzzscjqvaqfwxzlqtfn.supabase.co
SUPABASE_SECRET_KEY=<Purple Dots service key>
RAYA_API_KEY=<ALIMCO / Purple Dots Raya key>
```

```
cd purple_dots
docker build -t purple-dots .
```

Rebuild whenever you pull. The tables already exist; on a fresh Supabase
project run `sql/create_purple_dots.sql` first.

## Every run

**1. Check everything is reachable**
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

**2. Load everything**

```
docker run --rm --env-file .env purple-dots
```

It finds every batch, skips the calls it already has, and loads the rest.
No batch id to look up, nothing to choose:

```
  2385         8 answered     16 in db   Purple Dots_Basti_Day1_19thA  load
  2992         1 answered      1 in db   purplec                       load + test_flag
  3018       223 answered    663 in db   Basti_1_Final                 load

  883 already loaded, 3 new
```

**answered** is how many people picked up - NOT how many were called. A
batch reading 646 of 1098 had all 1098 dialled; 452 simply did not answer.

**Run it as often as you like.** It compares call by call, so re-running is a
no-op and `0 new` means there was nothing to do. It also means a batch
loaded last week picks up any calls Raya has retried since - which is real,
and is how three calls in batch 3018 were found a week late.

**3. Inbound**

Calls people made TO the bot are a separate run, because they belong to no
batch:

```
docker run --rm --env-file .env purple-dots --inbound
```

### Or everything in one command

```
docker run --rm --env-file .env --entrypoint python purple-dots run_pipeline.py
```

Four stages: check, outbound, inbound, platform. A stage that cannot run
says so and the rest carry on.

---

## Test batches

Trial batches are loaded like any other but marked `test_flag = true`, so
reporting ignores them. The list is one line in `.env`:

```
PD_TEST_BATCHES=1852,2042,2044,2100,2988,2991,2992
```

Set once. A new **campaign** needs no edit - it loads and counts straight
away. Add an id here only when a batch turns out to be a trial.

Marked rather than skipped deliberately: a wrong mark is one `UPDATE` to
undo, while a wrongly skipped batch goes missing silently for months.
