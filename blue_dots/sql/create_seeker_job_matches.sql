-- Seeker <-> job match scores. One row per recommended pair.
--
-- Top-N per seeker rather than the full cross product: 47,709 seekers x 115
-- unpaused jobs is 5.5M pairs, and nobody ever wants the 115th best match.
-- N is set in match_seekers.py.
--
-- Scores go stale. They depend on which jobs are unpaused and on the seeker
-- roster, so this is rebuilt rather than appended to - match_seekers.py
-- replaces a seeker's rows on every run.

create table if not exists public.seeker_job_matches (
    instance        text    not null,          -- 'UP' | 'KA'
    seeker_item_id  text    not null,
    job_item_id     text    not null,
    match_score     numeric not null,          -- 0-10, see match_seekers.py
    match_reason    text,                      -- which signals fired, and how
    match_rank      int     not null,          -- 1 = best for this seeker
    scored_at       timestamptz not null default now(),
    primary key (instance, seeker_item_id, job_item_id),
    constraint matches_seeker_fk
        foreign key (instance, seeker_item_id) references public.bluedot_items (instance, item_id),
    constraint matches_job_fk
        foreign key (instance, job_item_id)    references public.bluedot_items (instance, item_id)
);

-- the two questions anyone asks: best jobs for a seeker, best seekers for a job
create index if not exists matches_seeker_idx on public.seeker_job_matches (instance, seeker_item_id, match_rank);
create index if not exists matches_job_idx    on public.seeker_job_matches (instance, job_item_id, match_score desc);

comment on table public.seeker_job_matches is
    'Match confidence per seeker-job pair, top N per seeker. Built by '
    'match_seekers.py from role, distance, qualification and experience - the '
    'four signals present on both sides. Age and gender are masked in '
    'bluedot_items and are deliberately not used.';

-- RLS on with no policies, like every other table here: service_role only.
alter table public.seeker_job_matches enable row level security;
grant select, insert, update, delete on public.seeker_job_matches to service_role;

notify pgrst, 'reload schema';
