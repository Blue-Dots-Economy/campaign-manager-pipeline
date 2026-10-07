-- Purple Dots platform data, from the non-PII S3 dump.
--
-- Separate from purple_dots_calls, which holds what happened on the phone.
-- These three hold what exists on the platform: the people registered, the
-- items created, and the actions taken on them. They meet the call table
-- only through profile_item_id.
--
-- instance is part of every key. The Blue Dots dump arrives per deployment
-- (KA, UP) and the same item_id can appear under two of them; Purple Dots
-- has one deployment today, but keying on id alone would have to be undone
-- the first time a second one appears.
--
-- The state columns stay jsonb rather than being unpacked into columns. The
-- Purple Dots item schema is not settled - 19 distinct keys across 3,722
-- rows on the Blue Dots side, appearing on anywhere from 1 row to all of
-- them - and a column per key means a migration every time the platform
-- adds a field.

create table if not exists public.purple_users (
    instance             text        not null,
    user_id              text        not null,
    user_network         text,
    lifecycle_status     text,
    created_at           timestamptz,
    updated_at           timestamptz,
    onboarded_by_org_id  text,
    onboarded_via        text,
    onboarded_at         timestamptz,
    -- Masked at source: age arrives as '2***', gender as 'D***'. Nothing
    -- downstream can recover them; the exporter removed them before writing.
    user_state           jsonb       not null default '{}'::jsonb,
    tags                 jsonb       not null default '{}'::jsonb,
    loaded_at            timestamptz not null default now(),
    primary key (instance, user_id)
);

create table if not exists public.purple_items (
    instance          text        not null,
    item_id           text        not null,
    item_network      text,
    item_domain       text,
    item_type         text,
    lifecycle_status  text,
    created_by        text,
    lat               double precision,
    lng               double precision,
    created_at        timestamptz,
    updated_at        timestamptz,
    item_state        jsonb       not null default '{}'::jsonb,
    loaded_at         timestamptz not null default now(),
    primary key (instance, item_id)
);

create table if not exists public.purple_actions (
    instance           text        not null,
    action_id          text        not null,
    partition_network  text,
    action_type        text,
    action_status      text,
    actor_user_id      text,
    item_id            text,
    created_at         timestamptz,
    updated_at         timestamptz,
    action_state       jsonb       not null default '{}'::jsonb,
    loaded_at          timestamptz not null default now(),
    primary key (instance, action_id)
);

-- No foreign keys, deliberately. A torn snapshot - the three files written
-- seconds apart while the exporter ran - produces actions whose item or user
-- has not arrived yet. A constraint would reject the whole load; load_s3.py
-- checks the references itself and reports them instead.

create index if not exists purple_items_domain_idx
    on public.purple_items (item_domain, lifecycle_status);
create index if not exists purple_actions_item_idx
    on public.purple_actions (instance, item_id);
create index if not exists purple_actions_actor_idx
    on public.purple_actions (instance, actor_user_id);
