-- Purple Dots calls. One row per call.
--
-- Run this in the Purple Dots Supabase project (blzzscjqvaqfwxzlqtfn), NOT
-- the Blue Dots one. They share no tables.
--
-- NO PERSONAL DATA. The bot learns a beneficiary's name, age, gender,
-- address, documents and disability percentage, and writes them to the
-- platform through its update_profile tool. None of that is read into this
-- table. What is kept is profile_item_id - the platform's own id for that
-- person - which is enough to join back when someone genuinely needs the
-- detail, and keeps a named individual's disability status out of a second
-- database. There is no phone number and no name here.
--
-- disability_category_mapped is the one judgement call: a coded category
-- rather than free text, but still health information attached to an id.
-- It is kept because without it the table cannot answer a single question
-- about the programme. Drop it if that trade is not wanted.
--
-- call_id is the call's own uuid, always. contact_id was the obvious key -
-- it is what the Blue Dots tables use - but a batch contact with two
-- attempts is TWO calls sharing one contact_id, and this table is one row
-- per call. The contact id is kept as its own column for joining.

create table if not exists public.purple_dots_calls (
    call_id                                       text primary key,
    call_uuid                                     text not null,
    -- Raya's batch contact id, where the call came through a batch. NOT
    -- the key: a contact with two attempts is two calls sharing one
    -- contact_id, and this table is one row per call.
    contact_id                                    text,
    -- The id the Basti master sheet uses. It is NOT a Raya identifier -
    -- the sheet's range (4694548-4699905) does not overlap Raya's
    -- contact_ids (2812932-3289859) - so it was written by some other
    -- system. Mapped once by phone and stored here, because phone is
    -- the only thing the two ever shared and it is not kept anywhere.
    sheet_call_id                                 text,
    batch_id                                      text,
    campaign_name                                 text,
    agent_id                                      text,
    agent_name                                    text,
    persona                                       text,
    channel                                       text,
    contact_reference_type                        text,
    contact_reference                             text,
    call_date_ist                                 date,
    call_datetime_ist                             timestamptz,
    call_duration_seconds                         numeric,
    contact_attempts                              integer,
    call_status                                   text,
    call_answered                                 boolean,
    call_engaged                                  boolean,
    call_dropped_abruptly                         boolean,
    full_journey_completed                        boolean,
    abandoned_at_stage                            text,
    callback_requested                            text,
    on_behalf_of                                  boolean,
    demographic_info_shared                       boolean,
    profile_item_id                               text,
    profile_user_id                               text,
    disability_category_mapped                    text[],
    user_unsure_disability                        boolean,
    user_unsure_needs                             boolean,
    solution_option_mapped_categories             text[],
    missing_solution_enabler_mapped_categories    text[],
    solution_option_relevance                     text,
    solution_enablers_discussed                   boolean,
    user_unsure_solution_options                  boolean,
    update_profile_api_triggered                  boolean,
    update_profile_api_successful                 boolean,
    matching_providers_found                      integer,
    connect_provider_api_triggered                boolean,
    connect_provider_api_successful               boolean,
    providers_connected                           integer,
    call_value_score                              numeric,
    drop_reason                                   text,
    tools_used                                    text[],
    test_flag                                     boolean,
    loaded_at                                     timestamptz not null default now()
);

comment on table public.purple_dots_calls is
    'One row per Purple Dots voice call. Disability support, not jobs. '
    'Carries no personal data - profile_item_id joins back to the platform '
    'record, which is where the person actually lives.';

comment on column public.purple_dots_calls.profile_item_id is
    'The platform id for this beneficiary, from the update_profile tool call. '
    'Use it to look the person up on the platform; their details are '
    'deliberately not duplicated here.';

comment on column public.purple_dots_calls.channel is
    'Inferred, because Raya records no direction: Inbound means caller_no is '
    'set and to_number is empty, Outbound the other way round.';

create index if not exists purple_dots_calls_sheet_idx
    on public.purple_dots_calls (sheet_call_id);
create index if not exists purple_dots_calls_contact_idx
    on public.purple_dots_calls (contact_id);
create index if not exists purple_dots_calls_profile_idx
    on public.purple_dots_calls (profile_item_id);
create index if not exists purple_dots_calls_date_idx
    on public.purple_dots_calls (call_date_ist);
create index if not exists purple_dots_calls_agent_idx
    on public.purple_dots_calls (agent_id);

-- RLS on from the start. Even without names, a disability category tied to a
-- platform id is sensitive. service_role bypasses RLS and is what the loader
-- uses; no policy is granted to anon or authenticated, so neither can read a
-- row until somebody deliberately adds one.
alter table public.purple_dots_calls enable row level security;

grant select, insert, update on public.purple_dots_calls to service_role;


-- One row per provider connection.
--
-- A call that gets that far usually connects to TWO providers - 8 of the 9
-- observed did - and purple_dots_calls can only hold providers_connected as
-- a count. The provider ids exist solely in the connect_provider tool call,
-- so without this table the only record of who a beneficiary was actually
-- put in touch with is the number 2.
--
-- Still no personal data: both sides are platform item ids.

create table if not exists public.purple_dots_connections (
    id                      bigserial primary key,
    call_id                 text not null
        references public.purple_dots_calls (call_id) on delete cascade,
    seeker_item_id          text,
    provider_item_id        text not null,
    acting_as_user_id       text,
    -- the deployment these ids resolve against, so a bare uuid stays
    -- resolvable later: https://signals-purpledots.depwd.gov.in
    provider_instance_url   text,
    consent_acknowledged    boolean,
    consent_version         integer,
    loaded_at               timestamptz not null default now(),
    unique (call_id, provider_item_id)
);

comment on table public.purple_dots_connections is
    'One row per beneficiary-to-provider connection made on a call. Both '
    'sides are platform item ids; no personal data here either.';

create index if not exists purple_dots_connections_provider_idx
    on public.purple_dots_connections (provider_item_id);
create index if not exists purple_dots_connections_seeker_idx
    on public.purple_dots_connections (seeker_item_id);

alter table public.purple_dots_connections enable row level security;

grant select, insert, update on public.purple_dots_connections to service_role;
