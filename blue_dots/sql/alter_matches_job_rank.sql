-- A second ranking, from the provider's side.
--
-- match_rank alone made the table seeker-centric: a pair only existed if the
-- job made that seeker's top N. The effect was severe - 10 jobs ended up with
-- fewer than 10 candidates and 3 with none at all, not because no one fitted
-- but because every good candidate had five better options.
--
-- So a row now earns its place by being in EITHER top N, and carries whichever
-- ranks apply:
--   match_rank  this job's position among that seeker's options  (null if not in their top N)
--   job_rank    this seeker's position among that job's candidates (null if not in its top N)
alter table public.seeker_job_matches
    add column if not exists job_rank int;

alter table public.seeker_job_matches
    alter column match_rank drop not null;

comment on column public.seeker_job_matches.match_rank is
    'Rank of this job among the seeker''s matches, 1 = best. NULL when the row '
    'is here only because it made the JOB''s shortlist.';
comment on column public.seeker_job_matches.job_rank is
    'Rank of this seeker among the job''s candidates, 1 = best. NULL when the '
    'row is here only because it made the SEEKER''s shortlist.';

create index if not exists matches_jobrank_idx
    on public.seeker_job_matches (instance, job_item_id, job_rank)
    where job_rank is not null;

notify pgrst, 'reload schema';
