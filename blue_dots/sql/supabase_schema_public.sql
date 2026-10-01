-- Blue Dot dumps in the `public` schema, as bluedot_* tables.
--
-- Identical to supabase_schema.sql in columns, composite keys, FKs and indexes.
-- The only difference is where it lives: `public` is already served by the REST
-- API, so this needs no "Exposed schemas" change and no database password.
--
-- `instance` is part of every primary key because item_id is NOT unique across
-- deployments: 78 postings appear in both dumps with the same id and different
-- values. A bare item_id primary key would silently drop one copy of each.
--
-- Run once in the SQL editor, then load with: python load_bluedot.py --public

-- ---------------------------------------------------------------- users
create table if not exists public.bluedot_users (
    instance            text        not null,
    id                  text        not null,
    created_at          timestamptz,
    updated_at          timestamptz,
    domains             text[],                 -- ["seeker"] / ["provider"] / null
    onboarded_by_org_id text,
    onboarded_via       text,
    onboarded_source_id text,
    onboarded_at        timestamptz,
    tags                jsonb,                  -- always {} in both dumps so far
    loaded_at           timestamptz not null default now(),
    primary key (instance, id)
);

-- ---------------------------------------------------------------- items
-- Seeker profiles and job postings share this table, split by item_domain.
-- item_state is a sparse free-form payload (36 distinct keys across the two
-- deployments), so it stays jsonb rather than being flattened into columns.
create table if not exists public.bluedot_items (
    instance         text        not null,
    item_id          text        not null,
    item_network     text,
    item_domain      text,                      -- 'seeker' | 'provider'
    item_type        text,                      -- 'profile_1.0' | 'job_posting_1.0'
    lifecycle_status text,                      -- 'live' | 'draft'
    created_by       text,
    lat              double precision,
    lng              double precision,
    is_default_geo   boolean,                   -- true for the known sentinel coordinates
    created_at       timestamptz,
    updated_at       timestamptz,
    item_state       jsonb       not null default '{}'::jsonb,
    loaded_at        timestamptz not null default now(),
    primary key (instance, item_id),
    constraint items_created_by_fk
        foreign key (instance, created_by) references public.bluedot_users (instance, id)
);

-- ---------------------------------------------------------------- item_actions
-- One row per application (every row in both dumps is action_type='apply',
-- profile -> job_posting).
--
-- NOTE: no foreign key on (instance, source_item_id). 13 actions -- 11 in Ghaziabad,
-- 2 in Dharwad -- reference a source profile that is absent from the items dump, so
-- the constraint would reject the load. The target side is complete and does take one.
create table if not exists public.bluedot_item_actions (
    instance            text        not null,
    action_id           text        not null,
    partition_network   text,
    action_type         text,
    action_status       text,                   -- 'created' | 'accepted' | 'rejected'
    update_count        integer,
    source_item_network text,
    source_item_domain  text,
    source_item_type    text,
    source_item_id      text,
    source_item_owner   text,
    target_item_network text,
    target_item_domain  text,
    target_item_type    text,
    target_item_id      text,
    target_item_owner   text,
    performed_by_org_id text,
    created_at          timestamptz,
    updated_at          timestamptz,
    loaded_at           timestamptz not null default now(),
    primary key (instance, action_id),
    constraint actions_target_item_fk
        foreign key (instance, target_item_id) references public.bluedot_items (instance, item_id)
);

-- ---------------------------------------------------------------- indexes
create index if not exists items_domain_idx     on public.bluedot_items (instance, item_domain, lifecycle_status);
create index if not exists items_created_by_idx on public.bluedot_items (instance, created_by);
create index if not exists items_state_gin      on public.bluedot_items using gin (item_state);
create index if not exists items_geo_idx        on public.bluedot_items (lat, lng) where lat is not null;
create index if not exists actions_target_idx   on public.bluedot_item_actions (instance, target_item_id);
create index if not exists actions_source_idx   on public.bluedot_item_actions (instance, source_item_id);
create index if not exists actions_status_idx   on public.bluedot_item_actions (instance, action_status);
create index if not exists users_org_idx        on public.bluedot_users (instance, onboarded_by_org_id);

-- ---------------------------------------------------------------- access
-- RLS on with no policies: the anon and authenticated API keys get nothing, the
-- service_role key (which bypasses RLS) gets everything. This data carries employer
-- names and masked seeker attributes, so it should not be readable from a browser
-- by default. Add explicit policies later if you need client-side reads.
alter table public.bluedot_users        enable row level security;
alter table public.bluedot_items        enable row level security;
alter table public.bluedot_item_actions enable row level security;

-- ---------------------------------------------------------------- convenience views
create or replace view public.bluedot_job_postings as
select instance, item_id, lifecycle_status, created_by, lat, lng, is_default_geo,
       created_at, updated_at,
       item_state ->> 'jobProviderName'              as employer,
       item_state ->> 'role'                         as role_raw,
       nullif(item_state ->> 'natureOfJob', '')      as nature_of_job,
       (item_state ->> 'positions')::int             as positions,
       nullif((item_state ->> 'salaryMin')::numeric, 0) as salary_min,
       nullif((item_state ->> 'salaryMax')::numeric, 0) as salary_max
from public.bluedot_items
where item_domain = 'provider';

create or replace view public.bluedot_seeker_profiles as
select instance, item_id, lifecycle_status, created_by, lat, lng, is_default_geo,
       created_at, updated_at,
       item_state ->> 'gender'                     as gender_masked,
       item_state ->> 'age'                        as age_masked,
       item_state ->> 'educationCategory'          as education_category,
       item_state ->> 'workExperience'             as work_experience,
       item_state ->> 'nameOfJobRolesInterestedIn' as role_wanted_raw
from public.bluedot_items
where item_domain = 'seeker';

-- ---------------------------------------------------------------- grants
-- RLS above governs the anon/authenticated keys; this governs whether the
-- service_role key may touch the tables at all. Without it every write comes
-- back 42501 "permission denied", because a table created here inherits no
-- privileges of its own.
grant select, insert, update, delete on
    public.bluedot_users,
    public.bluedot_items,
    public.bluedot_item_actions
    to service_role;

grant select on
    public.bluedot_job_postings,
    public.bluedot_seeker_profiles
    to service_role;

notify pgrst, 'reload schema';
