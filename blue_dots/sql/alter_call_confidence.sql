-- call_confidence_score already exists on both aggregator tables but has never
-- had a source. This adds the companion column that records WHY a row scored
-- what it did, so any number in the queue can be explained without re-running
-- the scorer.

alter table public.aggregated_seeker_journey
  add column if not exists call_confidence_reason text;

alter table public.aggregated_provider_journey
  add column if not exists call_confidence_score  numeric,
  add column if not exists call_confidence_reason text;

-- the whole point of the score is ordering a call queue, so index it
create index if not exists asj_confidence_idx
  on public.aggregated_seeker_journey (call_confidence_score desc);
create index if not exists apj_confidence_idx
  on public.aggregated_provider_journey (call_confidence_score desc);

notify pgrst, 'reload schema';
