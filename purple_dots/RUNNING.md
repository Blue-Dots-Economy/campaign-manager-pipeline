# Running the Purple Dots loader

Raya calls -> Supabase. A batch job: it runs, writes, exits. You need
Docker and three credentials; you do not need Python.

## Everyone shares one database

 **Re-running is safe** - rows upsert on `call_id`,
so loading a batch twice gives `0 new`.

 **A wrong scope is not** - a run
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

```
cd purple_dots
docker build -t purple-dots .
```

Rebuild whenever you pull. The tables already exist; on a fresh Supabase
project run `sql/create_purple_dots.sql` first.

## Every run: three commands

**1. Check if everything reachable** 
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

**2. List of batches**

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
name.
**in db** means if it is done. 

**3. Load it.**

```
docker run --rm --env-file .env purple-dots --batch 3031
```

Then re-run `--batches` and check `in db` went up. 



