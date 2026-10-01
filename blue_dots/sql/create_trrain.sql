-- TRRAIN: the post-application service-offer call.
--
-- A later stage of the SAME funnel as KKB, not a separate programme — the bot
-- rings seekers who already applied through us and makes one offer of a free
-- support service. All 400 sampled TRRAIN phones already exist in
-- kkb_mastersheet, so `phone` is a real join back to the seeker.
--
-- It is a separate table because the payloads barely overlap: 10 of its 15
-- call_output fields have no KKB column, there is no job data at all, and so no
-- intent score exists. Folding these rows into kkb_mastersheet would leave ten
-- columns permanently empty and distort every rate computed from it.
--
-- Keyed on call_id = Raya's contact_id, matching dkb_mastersheet's convention.

create table if not exists public.trrain_mastersheet (
    id                     bigserial primary key,

    -- identity
    call_id                text not null unique,   -- Raya contact_id
    batch_id               text,
    campaign_name          text,
    campaign_date          date,
    call_datetime_ist      timestamp,
    agent_id               text,
    agent_name             text,
    jfc_campaign           text,
    channel                text,                   -- 'Outbound'

    -- who we reached
    phone                  text,                   -- the join back to the seeker
    contact_name           text,
    seeker_name            text,                   -- 'Unknown' on ~90% of calls
    right_person           text,                   -- Yes / No / Proxy
    call_answered          boolean,
    audio_check_confirmed  text,

    -- the offer, which is the point of the call.
    -- Yes/No/Maybe is kept as text, not a boolean: "Maybe" is a real answer on
    -- a quarter of calls and collapsing it either way invents a decision.
    trrain_pitched         boolean,
    trrain_interest        text,                   -- Yes / No / Maybe
    partner_named          boolean,
    offer_repeated         boolean,
    remembered_application text,                   -- Yes / No / Maybe
    questions_asked        text[],

    -- compliance
    do_not_call            boolean,                -- an explicit opt-out; honour it
    promised_outcome       boolean,                -- the bot must never promise one
    callback_requested     text,                   -- a real timestamp, not a flag

    -- call mechanics
    call_outcome           text,
    call_duration_seconds  numeric,
    contact_attempts       integer,
    drop_reason            text,
    call_summary           text,
    call_transcript        jsonb,
    call_recording_url     text,

    loaded_at              timestamptz not null default now()
);

create index if not exists trrain_phone_idx    on public.trrain_mastersheet (phone);
create index if not exists trrain_batch_idx    on public.trrain_mastersheet (batch_id);
create index if not exists trrain_interest_idx on public.trrain_mastersheet (trrain_interest);
create index if not exists trrain_date_idx     on public.trrain_mastersheet (campaign_date);
-- the opt-outs have to be cheap to find, since every future campaign must exclude them
create index if not exists trrain_dnc_idx      on public.trrain_mastersheet (do_not_call)
    where do_not_call is true;

alter table public.trrain_mastersheet enable row level security;

grant select, insert, update, delete on public.trrain_mastersheet to service_role;
grant usage, select on sequence public.trrain_mastersheet_id_seq to service_role;

notify pgrst, 'reload schema';
