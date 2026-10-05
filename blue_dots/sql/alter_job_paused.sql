-- Adds a manual pause flag to job postings.
--
-- WHY IT GOES ON bluedot_items, NOT THE VIEW
-- bluedot_job_postings is a view (and not even writable - a PATCH through it
-- returns 42501 permission denied). The column has to exist on the table
-- underneath, then be exposed by replacing the view.
--
-- WHY A RE-SYNC WILL NOT WIPE IT
-- load_bluedot.py upserts on (instance, item_id) with
-- Prefer: resolution=merge-duplicates, and sends only the columns its row
-- builder produces. A PostgREST upsert does not null out columns absent from
-- the payload, so `paused` survives every re-load of the S3 dumps. It is set
-- by us and never touched by the sync - which is exactly the "filled by a
-- backfill" pattern the column catalogue already documents.
--
-- WHY TEXT AND NOT BOOLEAN
-- 'Y' marks a pause, NULL means not paused. Asked for as a Y flag so it reads
-- the same way in the sheets, where a blank cell and a 'Y' are what the team
-- already works with.

alter table public.bluedot_items
    add column if not exists paused text;

comment on column public.bluedot_items.paused is
    'Manual pause. ''Y'' = do not recommend or dial against this item. Set by '
    'us, never by the S3 sync. NULL means active.';

-- Only the paused ones are ever looked up, and they are a tiny minority of
-- 3,722 rows, so a partial index is the whole cost of this.
create index if not exists items_paused_idx
    on public.bluedot_items (instance, item_id)
    where paused is not null;

-- Re-expose the view with the new column. `create or replace` keeps the
-- existing grants and requires the original columns to stay in the same
-- order, so `paused` is appended at the end.
create or replace view public.bluedot_job_postings as
select instance, item_id, lifecycle_status, created_by, lat, lng, is_default_geo,
       created_at, updated_at,
       item_state ->> 'jobProviderName'              as employer,
       item_state ->> 'role'                         as role_raw,
       nullif(item_state ->> 'natureOfJob', '')      as nature_of_job,
       (item_state ->> 'positions')::int             as positions,
       nullif((item_state ->> 'salaryMin')::numeric, 0) as salary_min,
       nullif((item_state ->> 'salaryMax')::numeric, 0) as salary_max,
       paused
from public.bluedot_items
where item_domain = 'provider';

notify pgrst, 'reload schema';
