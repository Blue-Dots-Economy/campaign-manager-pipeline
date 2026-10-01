-- Re-grain aggregated_provider_journey: one row per Blue Dot PROVIDER, not one
-- row per phone we happened to call. Providers are identified by their Blue Dot
-- user id (the owner of their job postings); the phone comes from our call data,
-- since the Blue Dot dumps carry no phone numbers for providers.

alter table public.aggregated_provider_journey
  add column if not exists provider_id   text,        -- primary key: Blue Dot user id, or phone:<number> when unmatched
  add column if not exists job_ids       text[],      -- their Blue Dot job postings
  add column if not exists call_ids      text[],      -- every DKB call made against them
  add column if not exists instance      text,        -- UP / KA
  add column if not exists in_bluedot    boolean,     -- false = we called someone with no Blue Dot record
  add column if not exists onboarded_at  timestamptz,
  add column if not exists ever_called   boolean,
  add column if not exists total_postings integer;    -- postings in Blue Dot, called or not

-- phone is no longer the key, and providers in Blue Dot may have no phone
alter table public.aggregated_provider_journey alter column phone drop not null;
alter table public.aggregated_provider_journey drop constraint if exists aggregated_provider_journey_phone_key;

create unique index if not exists apj_provider_id_key on public.aggregated_provider_journey (provider_id);
create index if not exists apj_phone_idx2    on public.aggregated_provider_journey (phone);
create index if not exists apj_called_idx    on public.aggregated_provider_journey (ever_called);
create index if not exists apj_instance_idx  on public.aggregated_provider_journey (instance);

notify pgrst, 'reload schema';
