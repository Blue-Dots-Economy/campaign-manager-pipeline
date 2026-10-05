-- Job urgency score, on the postings themselves.
--
-- Scored ONLY where pause_status = 'N'. A paused posting is a decision, not a
-- low priority, so it gets NULL rather than 0 - the same distinction
-- call_confidence.py draws between "do not call" and "call last".
--
-- Two columns, matching how every other score in this pipeline is stored:
-- the number, and the reason it came out that way. intent_score has
-- intent_score_reasoning and call_confidence returns a reason string, because
-- a score nobody can explain is a score nobody trusts.
--
-- Like pause_status, these are OURS: load_bluedot.py upserts on
-- (instance, item_id) and never sends either column, so an S3 re-sync leaves
-- them alone. They do go stale though - the score depends on posting age, so
-- it needs re-running, and score_job_urgency.py is safe to re-run any time.

alter table public.bluedot_items
    add column if not exists job_urgency_score  numeric,
    add column if not exists job_urgency_reason text;

comment on column public.bluedot_items.job_urgency_score is
    'How urgently this posting needs attention, -2 to 5. Only set where '
    'pause_status = ''N''; NULL on paused postings. Age + application-count '
    'based, so it goes stale - recompute with score_job_urgency.py.';

comment on column public.bluedot_items.job_urgency_reason is
    'Which rules fired, e.g. "age>180 (+3) | no applications (+2)".';

create index if not exists items_urgency_idx
    on public.bluedot_items (job_urgency_score desc)
    where job_urgency_score is not null;

notify pgrst, 'reload schema';
