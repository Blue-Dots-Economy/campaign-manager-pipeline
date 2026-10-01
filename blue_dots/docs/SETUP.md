# Replicating this project on a new Supabase instance

End-to-end: pull the non-PII campaign dumps from one or more Blue Dot deployments,
and load them into a Supabase Postgres database.

Takes about 15 minutes, most of it waiting on the download.

---

## What you end up with

A `bluedot` schema holding both deployments in one set of tables:

| Object | Type | Rows (3 Sep 2026 snapshot) |
|---|---|---|
| `bluedot.users` | table | 43,840 |
| `bluedot.items` | table | 50,397 |
| `bluedot.item_actions` | table | 6,753 |
| `bluedot.job_postings` | view | — |
| `bluedot.seeker_profiles` | view | — |

Deployments are separated by an `instance` column (`UP`, `KA`), not by separate tables.

---

## 1. Prerequisites

- **Python 3.9+** (developed on 3.10.11)
- A **Supabase project** — free tier is fine; the dataset is roughly 60 MB
- **Credentials for each deployment** you want to pull, as a Postman environment
  export (`*.postman_environment.json`). Each must contain `base_url`,
  `keycloak_url`, `realm`, `client_id`, and `client_secret`.

```sh
pip install -r bluedot_fetch/requirements.txt
```

> The `client_secret` must belong to a **system** service account — a
> `client_credentials` client with no `aggregator_id` / `signalstack_org_id`. A
> coordinator token is rejected by the dump route with `403 NOT_SYSTEM_CLIENT`.

---

## 2. Download the dumps

One command per deployment. The directory name under `dumps/` becomes the
`instance` value in Postgres, so name it deliberately.

```sh
python bluedot_fetch/fetchdata.py --env bluedot_fetch/GZB.postman_environment.json     --out-dir dumps/UP
python bluedot_fetch/fetchdata.py --env bluedot_fetch/Dharwad.postman_environment.json --out-dir dumps/KA
```

Check the metadata without downloading:

```sh
python bluedot_fetch/fetchdata.py --env bluedot_fetch/GZB.postman_environment.json --no-download
```

**Notes**

- The pre-signed S3 URLs expire in **10 minutes**. If a download fails, just re-run —
  a fresh token and fresh URLs are issued each time.
- The script refuses to download if the three files' `last_modified` values differ by
  more than 60 seconds, since that means the snapshot caught the exporter mid-write.
  Override with `--allow-torn` if you accept the risk.
- **The files are gzip-compressed despite the `.jsonl` extension.** The API serves
  `user.ndjson.gz`; `fetchdata.py` writes it verbatim as `user.jsonl`. Every script
  here sniffs the magic bytes rather than trusting the name, but `head dumps/UP/user.jsonl`
  will show you binary. Use `gzip.open()` or `zcat`.

Expected output:

```
dumps/
  UP/   user.jsonl  items.jsonl  item_actions.jsonl
  KA/   user.jsonl  items.jsonl  item_actions.jsonl
```

---

## 3. Get a working Supabase connection string

**This is the step most likely to waste your time.** Supabase offers three connection
modes and only one of them works here.

In the dashboard: **Project Settings → Database → Connection string**.

| Mode | Port | Verdict |
|---|---|---|
| **Session pooler** | 5432 | ✅ **Use this one** |
| Direct connection | 5432 | ❌ IPv6-only on current projects — unreachable from most networks |
| Transaction pooler | 6543 | ❌ Does not support the prepared statements psycopg2 issues |

The session pooler URI looks like this — note the username carries the project ref,
and the host is a regional pooler, not `db.<ref>.supabase.co`:

```
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

If the dashboard only shows you the direct connection, you can check whether it is
even reachable:

```sh
python -c "import socket; print(socket.getaddrinfo('db.<project-ref>.supabase.co',5432))"
```

If that returns only IPv6 addresses (starting `2406:`, `2600:` etc.) and no IPv4,
the direct string will not work — use the pooler.

**Percent-encode special characters** in the password: `@` → `%40`, `#` → `%23`,
`/` → `%2F`, `:` → `%3A`.

Store it in `.env` (which is gitignored):

```sh
echo 'SUPABASE_DB_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres' >> .env
```

### Finding your region

If you don't know which region your project is in, the pooler returns a clear,
distinguishable error for the wrong one (`Tenant or user not found`) versus the right
one. This probes the common regions:

```python
import psycopg2, concurrent.futures as cf
REF, PW = "<project-ref>", "<password>"
regions = ["us-east-1","us-east-2","us-west-1","us-west-2","ca-central-1","sa-east-1",
           "eu-west-1","eu-west-2","eu-west-3","eu-central-1","eu-north-1",
           "ap-south-1","ap-southeast-1","ap-southeast-2","ap-northeast-1","ap-northeast-2"]
hosts = [f"aws-{n}-{r}.pooler.supabase.com" for r in regions for n in (0, 1)]

def probe(h):
    try:
        psycopg2.connect(f"postgresql://postgres.{REF}:{PW}@{h}:5432/postgres",
                         connect_timeout=8).close()
        return (h, "CONNECTED")
    except Exception as e:
        return (h, "wrong region") if "not found" in str(e) else (h, str(e)[:60])

with cf.ThreadPoolExecutor(max_workers=12) as ex:
    for h, res in ex.map(probe, hosts):
        if res != "wrong region":
            print(h, res)
```

---

## 4. Create the schema

Either paste [`supabase_schema.sql`](supabase_schema.sql) into the Supabase **SQL Editor**
and run it, or:

```sh
psql "$SUPABASE_DB_URL" -f supabase_schema.sql
```

Or from Python, if you don't have `psql`:

```python
import psycopg2, os
from pathlib import Path
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("SUPABASE_DB_URL="):
        os.environ["SUPABASE_DB_URL"] = line.split("=", 1)[1].strip()
conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"]); conn.autocommit = True
conn.cursor().execute(Path("supabase_schema.sql").read_text(encoding="utf-8"))
```

The script is idempotent (`create ... if not exists`), so re-running is safe.

---

## 5. Load the data

```sh
python push_to_supabase.py --dry-run     # parse everything, write nothing
python push_to_supabase.py               # load every instance under dumps/
```

Other options:

```sh
python push_to_supabase.py --instance UP        # one deployment only
python push_to_supabase.py --truncate           # empty the tables first
python push_to_supabase.py --db-url "postgres://..."   # bypass .env
```

Every row upserts on its primary key, so **re-running after a fresh download updates
in place rather than duplicating**. A full reload of ~101k rows takes a couple of
minutes over the pooler.

---

## 6. Verify

```sql
select 'users' as t, instance, count(*) from bluedot.users group by 2
union all select 'items', instance, count(*) from bluedot.items group by 2
union all select 'item_actions', instance, count(*) from bluedot.item_actions group by 2
order by 1, 2;
```

Confirm the composite key did its job — this must return **78**, not 0:

```sql
select count(*) from (
  select item_id from bluedot.items group by item_id having count(distinct instance) = 2
) x;
```

If it returns 0, your primary key is on `item_id` alone and you have silently lost
one copy of each colliding posting. See the warning below.

---

## Things that will bite you

### The tables are in `bluedot`, not `public`

Supabase's **Table Editor only shows the `public` schema by default**. Use the schema
dropdown at the top-left to switch to `bluedot`. The SQL Editor works regardless.

A custom schema is also **not exposed to the REST/client API** by default. If you want
to query from an application rather than the SQL editor, add `bluedot` under
**Settings → API → Exposed schemas**.

### `item_id` is not unique across deployments

**78 job postings exist in both dumps with the same `item_id` and different values.**
Every primary key here is therefore `(instance, id)`. If you redesign the schema,
keep the composite key — a bare `item_id` primary key drops 78 rows without error.

The duplicates are also stale in one direction: the copies appearing in the second
deployment carry older `updated_at` values and have lost their salary data.

### Row Level Security is on, with no policies

`anon` and `authenticated` keys return **nothing**; only `service_role` (which bypasses
RLS) can read. This is deliberate — the data carries employer names in the clear. To
allow client-side reads, add explicit policies:

```sql
create policy "read for authenticated" on bluedot.items
  for select to authenticated using (true);
```

### `source_item_id` has no foreign key

13 rows in `item_actions` (11 in UP, 2 in KA) reference a source profile that is absent
from the items dump. The constraint would abort the load, so it is intentionally
omitted. `created_by` and `target_item_id` both have working foreign keys.

### Data quality caveats baked into the schema

- **Sentinel coordinates.** `(28.64, 77.34)` and `(20.59, 78.96)` are placeholder
  values substituted for suppressed locations, not real places. They cover **41% of
  UP's located records** and 11% of KA's, and appear almost exclusively on seeker
  (person) records. The `is_default_geo` column flags them — always filter geographic
  queries with `where not is_default_geo`.
- **Zero-filled salaries.** `salaryMin` is present on 79% of UP postings, but 86% of
  those values are the number `0`, meaning "not stated". Averaging the raw column
  returns ₹2,275, which is not a wage anyone is paid. The `job_postings` view wraps
  both fields in `nullif(…, 0)` so they read as `NULL`. Query the view, not the raw
  `item_state`, unless you specifically want the stored value.
- **`role` is pipe-delimited multi-value** — `"Any | Crew Member"`. Split on `|`
  before counting roles.
- **Seeker attributes are masked at source** — `gender: "M***"`, `age: "2***"`.
  Provider fields are entirely unmasked. No processing reverses this.

---

## Adding a third deployment

No schema change needed.

```sh
python bluedot_fetch/fetchdata.py --env NewCity.postman_environment.json --out-dir dumps/XX
python push_to_supabase.py --instance XX
```

The loader derives `instance` from the directory name. `INSTANCE_LABELS` in
[`push_to_supabase.py`](push_to_supabase.py) maps legacy directory names (`gzb`,
`dharwad`) onto the current labels so a stale checkout cannot load a duplicate set
under the old names — add an entry there if you rename a directory.

---

## Optional: CSV export

Independent of Supabase, if you want flat files:

```sh
python to_csv.py --in-dir dumps/UP --out-dir csv/UP
```

This **flattens** rather than preserving structure: the 36 sparse `item_state` keys
become 36 separate `item_state__*` columns, so `items.csv` is 47 columns wide against
the database's 14. Nested values are JSON-encoded; arrays are pipe-joined. Written
UTF-8 with BOM so Excel opens it correctly.

---

## File reference

| File | Purpose |
|---|---|
| `fetchdata.py` | Two-step auth + download from the dump API |
| `to_csv.py` | Flatten the dumps to CSV (optional, not part of the DB path) |
| `supabase_schema.sql` | Tables, indexes, foreign keys, RLS, views |
| `push_to_supabase.py` | Upsert the dumps into Postgres |
| `curl.yaml` | OpenAPI spec for the dump API (endpoint only — does not document `item_state`) |
| `.env` | Secrets. **Gitignored** — never commit |
| `.env.example` | Template showing every variable |

### Secrets checklist

`.gitignore` covers `.env`, `dumps/`, `csv/`, and `*.postman_environment.json`. The
Postman environment files contain live 64-character client secrets, so confirm they
are ignored before your first commit:

```sh
git check-ignore -v *.postman_environment.json .env
```
