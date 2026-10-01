-- STEP 1 - the column the DKB push is waiting on
ALTER TABLE public.dkb_mastersheet ADD COLUMN IF NOT EXISTS campaign_date date;

-- STEP 2 - Aggregated Provider Journey: one row per provider (employer),
-- keyed on the contact phone, rolled up across every call and job.
create table if not exists public.aggregated_provider_journey (
    id                      bigserial primary key,

    -- identifiers
    phone                   text not null unique,  -- the provider key (last 10 digits)
    company_name            text,                  -- as most recently heard
    source_id               text,                   -- reserved, left empty

    -- volume
    total_calls_made        integer,               -- rows in dkb_mastersheet
    total_jobs              integer,               -- distinct job_id we called about
    total_campaigns         integer,               -- distinct campaigns

    -- recency
    last_call_date          date,
    last_call_answered      date,                  -- last date they picked up
    last_call_engaged       date,                  -- last date we got past phase 1
    drop_reason             text,                  -- most recent

    -- scores (DKB rubric, 0-10, half points preserved)
    avg_intent_score        numeric,
    max_intent_score        numeric,

    -- hiring signal
    latest_job_status       text,                  -- Active / Closed / Unverified / ...
    ever_verified_active    boolean,               -- ever confirmed a job Active
    ever_new_job_mentioned  boolean,
    ever_new_job_posted     boolean,
    total_new_jobs_posted   integer,
    total_fields_updated    integer,               -- job details corrected across calls
    total_vacancies         integer,               -- sum of confirmed vacancy counts
    max_phase_reached       integer,               -- furthest point in the script

    -- context
    jfc_campaign            text,                  -- Ghaziabad / Hubli-Dharwad
    ever_answered           boolean,

    updated_at              timestamptz default now()
);

create index if not exists apj_company_idx on public.aggregated_provider_journey (company_name);
create index if not exists apj_jfc_idx     on public.aggregated_provider_journey (jfc_campaign);
create index if not exists apj_intent_idx  on public.aggregated_provider_journey (max_intent_score);
create index if not exists apj_status_idx  on public.aggregated_provider_journey (latest_job_status);

grant select, insert, update, delete on public.aggregated_provider_journey to service_role;
grant usage, select on sequence public.aggregated_provider_journey_id_seq to service_role;

notify pgrst, 'reload schema';
