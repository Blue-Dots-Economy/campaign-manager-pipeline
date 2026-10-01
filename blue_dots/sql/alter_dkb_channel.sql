-- dkb_mastersheet has no channel column, so every row in it is implicitly an
-- outbound call. That was true until we went looking for inbound traffic:
-- the two DKB bots have taken ~277 inbound calls (employers ringing back),
-- and loading those without a channel column would mix them into the
-- outbound population and quietly distort every rate derived from it -
-- answer rate, job-update rate, intent distribution.
--
-- kkb_mastersheet already has this column, with 'Outbound' on every row, so
-- this brings DKB in line rather than inventing a convention.
--
-- Run this before load_inbound.py --dkb.

alter table public.dkb_mastersheet
    add column if not exists channel text;

-- everything loaded so far came through a batch, and a batch is always dialled
update public.dkb_mastersheet
   set channel = 'Outbound'
 where channel is null;

comment on column public.dkb_mastersheet.channel is
    'Outbound (dialled from a batch) or Inbound (the employer called us). '
    'Inbound rows have no batch and are keyed on the call uuid, not a contact_id.';

-- the same index kkb_mastersheet has, for the channel filters the sheets use
create index if not exists dkb_mastersheet_channel_idx
    on public.dkb_mastersheet (channel);

grant select on public.dkb_mastersheet to anon, authenticated;
