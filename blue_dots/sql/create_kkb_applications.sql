-- One row per job a call applied to - succeeded or failed.
--
-- A different grain from kkb_mastersheet: a single call can apply to several
-- jobs. Same pattern as dkb_newjobs, which hangs off dkb_mastersheet.
--
-- Deliberately NOT stored here: campaign_name, campaign_date, jfc_campaign,
-- seeker_name, phone. All five already live on kkb_mastersheet under the same
-- call_id, and campaign_name alone was renamed three times in two days - a
-- second copy would simply drift. The view at the bottom joins them back on.

create table if not exists public.kkb_applications (
    id                     bigserial primary key,

    call_id                text not null
        references public.kkb_mastersheet (call_id) on delete cascade,
    job_id                 text not null,
    applied                boolean not null,          -- true = it went through
    failure_reason         text,                      -- PROFILE_NOT_LIVE, HTTP 404 ...

    -- true when job_id is "<provider phone>_<role>" rather than a Blue Dot
    -- uuid. Those come from job-feed entries that were never posted to Blue
    -- Dot; the bot passes the id through unchanged.
    synthetic_job_id       boolean,

    -- the job AS OFFERED ON THE CALL, from recommendations_input. Not the
    -- posting's current state: what the seeker was actually told.
    job_role               text,
    company_name           text,
    provider_phone         text,                      -- often only recoverable
                                                      -- from a synthetic job_id
    job_location           text,
    vacancies              text,                      -- free text: "2", "100"
    salary                 text,                      -- free text: "12000 - 16000"
    qualification_required text,

    loaded_at              timestamptz not null default now(),

    -- makes a re-push idempotent: the same application never duplicates
    unique (call_id, job_id, applied)
);

create index if not exists kkbapp_job_idx    on public.kkb_applications (job_id);
create index if not exists kkbapp_synth_idx  on public.kkb_applications (synthetic_job_id)
    where synthetic_job_id is true;
-- the failures are the point of this table; keep them cheap to scan
create index if not exists kkbapp_failed_idx on public.kkb_applications (failure_reason)
    where applied is false;

alter table public.kkb_applications enable row level security;

grant select, insert, update, delete on public.kkb_applications to service_role;
grant usage, select on sequence public.kkb_applications_id_seq to service_role;

-- What the sheet builder reads: the application plus the seeker and campaign
-- columns, joined rather than copied, so a rename on the parent propagates.
create or replace view public.kkb_applications_full as
select a.id,
       a.call_id,
       a.job_id,
       a.applied,
       a.failure_reason,
       a.synthetic_job_id,
       a.job_role,
       a.company_name,
       a.provider_phone,
       a.job_location,
       a.vacancies,
       a.salary,
       a.qualification_required,
       a.loaded_at,
       m.campaign_name,
       m.campaign_date,
       m.jfc_campaign,
       m.seeker_name,
       m.phone,
       m.intent_score
from public.kkb_applications a
join public.kkb_mastersheet m on m.call_id = a.call_id;

grant select on public.kkb_applications_full to service_role;

notify pgrst, 'reload schema';
