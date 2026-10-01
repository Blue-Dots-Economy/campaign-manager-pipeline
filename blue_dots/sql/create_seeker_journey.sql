-- Aggregated Seeker Journey: one row per seeker (keyed on phone), rolled up
-- across every campaign they appear in.
create table if not exists public.aggregated_seeker_journey (
    id                    bigserial primary key,

    -- identifiers
    phone                 text not null unique,   -- the seeker key (last 10 digits)
    seeker_id             text,                   -- Blue Dot profile id, where we found one
    seeker_name           text,
    source_id             text,                   -- reserved, left empty for now

    -- call volume
    total_calls_made      integer,                -- real call attempts, incl. retries
    total_campaigns       integer,                -- distinct campaigns they appear in
    total_application     integer,                -- sum of applications_count

    -- recency / last call
    last_call_eng         date,                   -- last date they engaged
    last_call_answered    date,                   -- last date they answered
    last_call_date        date,                   -- last date we dialled at all
    drop_reason           text,                   -- most recent drop reason

    -- scores
    avg_intent_score      numeric,
    max_intent_score      numeric,
    avg_match_score       numeric,                -- no source yet
    call_confidence_score numeric,                -- no source yet

    -- context
    jfc_campaign          text,                   -- Ghaziabad / Hubli-Dharwad
    agent_name            text,                   -- most recent bot that called them
    ever_applied          boolean,
    ever_answered         boolean,
    ever_engaged          boolean,

    -- meta
    call_meta_data        jsonb,
    app_session_meta_data jsonb,

    updated_at            timestamptz default now()
);

create index if not exists asj_seeker_id_idx  on public.aggregated_seeker_journey (seeker_id);
create index if not exists asj_jfc_idx        on public.aggregated_seeker_journey (jfc_campaign);
create index if not exists asj_intent_idx     on public.aggregated_seeker_journey (avg_intent_score);

grant select, insert, update, delete on public.aggregated_seeker_journey to service_role;
grant usage, select on sequence public.aggregated_seeker_journey_id_seq to service_role;

notify pgrst, 'reload schema';
