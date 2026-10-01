-- Re-grain aggregated_seeker_journey: one row per Blue Dot SEEKER (user),
-- not one row per phone we happened to call. Adds the Blue Dot identifiers,
-- the call_ids array, and relaxes phone so Blue Dot users without a phone
-- (355 of them) can still have a row.

alter table public.aggregated_seeker_journey
  add column if not exists seeker_id      text,          -- primary key: Blue Dot user id, or phone:<number> when unmatched
  add column if not exists profile_ids    text[],        -- their Blue Dot profile ids
  add column if not exists call_ids        text[],       -- every call made against them
  add column if not exists instance        text,         -- UP / KA
  add column if not exists in_bluedot      boolean,      -- false = we called someone with no Blue Dot record
  add column if not exists bluedot_name    text,
  add column if not exists onboarded_at    timestamptz,
  add column if not exists ever_called     boolean;

-- phone can no longer be the key, and can no longer be mandatory
alter table public.aggregated_seeker_journey alter column phone drop not null;
alter table public.aggregated_seeker_journey drop constraint if exists aggregated_seeker_journey_phone_key;

create unique index if not exists asj_seeker_id_key on public.aggregated_seeker_journey (seeker_id);
create index if not exists asj_phone_idx    on public.aggregated_seeker_journey (phone);
create index if not exists asj_called_idx   on public.aggregated_seeker_journey (ever_called);
create index if not exists asj_instance_idx on public.aggregated_seeker_journey (instance);

notify pgrst, 'reload schema';
