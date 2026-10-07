-- The date a posting last received an application.
--
-- Not a deadline, and deliberately not part of job_urgency_score. It is
-- derived from OUR calling, so a stale date can mean the job is dead or
-- simply that we stopped calling for it - and 43 of the 115 unpaused
-- postings are over 180 days old yet still receiving applications, which is
-- us sending seekers into postings nobody has closed.
--
-- kkb_applications carries no date of its own (loaded_at is when our sync
-- ran), so the date comes through the call: application -> call_id ->
-- kkb_mastersheet.campaign_date.
--
-- NULL means no application has ever been recorded against the posting,
-- which is true of most of them.

alter table public.bluedot_items
    add column if not exists last_application_date date;

comment on column public.bluedot_items.last_application_date is
    'Date a seeker last chose this posting, successful or not, via '
    'kkb_applications -> kkb_mastersheet.campaign_date. Failed attempts '
    'count: the apply API was ours to get wrong, the choice was theirs. '
    'NULL = never any. Measures our calling, not whether the job is open.';
