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

-- On the shared database the dashboard's bootstrap creates this schema.
do $$
begin
    if not exists (select 1 from pg_namespace where nspname = 'platform') then
        create schema platform;
    end if;
end $$;

-- Move tables created in public before this schema existed.
do $$
declare t text;
begin
    foreach t in array array['purple_users', 'purple_items', 'purple_actions'] loop
        if to_regclass('public.' || t) is not null and to_regclass('platform.' || t) is null then
            execute format('alter table public.%I set schema platform', t);
        end if;
    end loop;
end $$;

-- Matches the dump as it actually arrives. There is no user_state here -
-- the Blue Dots dump has one, Purple Dots does not - so no age or gender
-- field exists to mask.
create table if not exists platform.purple_users (
    instance             text        not null,
    user_id              text        not null,   -- "id" in the dump
    created_at           timestamptz,
    updated_at           timestamptz,
    domains              jsonb       not null default '[]'::jsonb,
    onboarded_by_org_id  text,
    onboarded_via        text,
    onboarded_source_id  text,
    onboarded_at         timestamptz,
    tags                 jsonb       not null default '{}'::jsonb,
    loaded_at            timestamptz not null default now(),
    primary key (instance, user_id)
);

create table if not exists platform.purple_items (
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

create table if not exists platform.purple_actions (
    instance             text        not null,
    action_id            text        not null,
    partition_network    text,
    action_type          text,
    action_status        text,
    update_count         integer,
    source_item_network  text,
    source_item_domain   text,
    source_item_type     text,
    source_item_id       text,
    source_item_owner    text,
    target_item_network  text,
    target_item_domain   text,
    target_item_type     text,
    target_item_id       text,
    target_item_owner    text,
    performed_by_org_id  text,
    created_at           timestamptz,
    updated_at           timestamptz,
    loaded_at            timestamptz not null default now(),
    primary key (instance, action_id)
);

-- Earlier versions of this table guessed the action columns (actor_user_id,
-- item_id, action_state). The export's item_actions allowlist uses the
-- source/target tuple above instead, so bring an existing table into line.
-- Safe: no action row has ever loaded - the dump carried no action_id.
alter table platform.purple_actions
    add column if not exists update_count         integer,
    add column if not exists source_item_network  text,
    add column if not exists source_item_domain   text,
    add column if not exists source_item_type     text,
    add column if not exists source_item_id       text,
    add column if not exists source_item_owner    text,
    add column if not exists target_item_network  text,
    add column if not exists target_item_domain   text,
    add column if not exists target_item_type     text,
    add column if not exists target_item_id       text,
    add column if not exists target_item_owner    text,
    add column if not exists performed_by_org_id  text,
    drop column if exists actor_user_id,
    drop column if exists item_id,
    drop column if exists action_state;

-- No foreign keys, deliberately. A torn snapshot - the three files written
-- seconds apart while the exporter ran - produces actions whose item or user
-- has not arrived yet. A constraint would reject the whole load; load_s3.py
-- checks the references itself and reports them instead.

create index if not exists purple_items_domain_idx
    on platform.purple_items (item_domain, lifecycle_status);
drop index if exists platform.purple_actions_item_idx;
drop index if exists platform.purple_actions_actor_idx;
create index if not exists purple_actions_source_idx
    on platform.purple_actions (instance, source_item_id);
create index if not exists purple_actions_target_idx
    on platform.purple_actions (instance, target_item_id);
