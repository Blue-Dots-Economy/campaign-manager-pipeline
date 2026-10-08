-- Lovable dashboard schema: 11 tables, 3 views, 30 functions. No data.
--
-- From pg_dump --schema-only of the dashboard Supabase (blzzscjqvaqfwxzlqtfn),
-- 2026-10-08. Re-runnable. Apply after create_purple_dots.sql: the views read
-- purple_dots_calls, which that file owns.
--
-- Changed from the dump:
--   CREATE ... IF NOT EXISTS / OR REPLACE; constraints inlined.
--   No RLS: enabled with no policy, it blocks every non-owner write.
--   rls_auto_enable() dropped: Supabase event trigger.
--   pgcrypto in schema extensions, where the functions' search_path expects it.

set check_function_bodies = false;

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;


-- tables

CREATE TABLE IF NOT EXISTS public.app_users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    name text,
    role text NOT NULL,
    district text,
    program text,
    node_type text,
    node_name text,
    password_hash text,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT app_users_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'jfc'::text, 'owner'::text, 'coordinator'::text, 'ecosystem'::text, 'user'::text]))),
    CONSTRAINT app_users_email_key UNIQUE (email),
    CONSTRAINT app_users_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.campaign_requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    program text NOT NULL,
    agent_id text NOT NULL,
    agent_name text,
    batch_name text NOT NULL,
    campaign_day text,
    campaign_date text,
    campaign_type text,
    region text,
    language text,
    city_campaign text,
    channel text DEFAULT 'outbound'::text,
    source text,
    cohort_intent text,
    cohort_filters jsonb,
    contacts jsonb DEFAULT '[]'::jsonb NOT NULL,
    contact_count integer DEFAULT 0 NOT NULL,
    schedule jsonb,
    concurrency integer,
    max_retries integer,
    retry_after_hrs integer,
    selected_statuses jsonb,
    requested_by text,
    status text DEFAULT 'pending'::text NOT NULL,
    reviewer_email text,
    decline_reason text,
    batch_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    note text,
    CONSTRAINT campaign_requests_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.launched_batch_inputs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    batch_id text NOT NULL,
    program text NOT NULL,
    normalized_phone text NOT NULL,
    contact_name text,
    recommendations text,
    user_intent text,
    raw jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT launched_batch_inputs_batch_id_normalized_phone_key UNIQUE (batch_id, normalized_phone),
    CONSTRAINT launched_batch_inputs_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.launched_batches (
    batch_id text NOT NULL,
    program text NOT NULL,
    agent_id text,
    agent_name text,
    batch_name text,
    campaign_day text,
    campaign_date text,
    campaign_type text,
    language text,
    city_campaign text,
    region text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT launched_batches_pkey PRIMARY KEY (batch_id)
);

CREATE TABLE IF NOT EXISTS public.north_star_config (
    program text NOT NULL,
    key text NOT NULL,
    threshold numeric,
    enabled boolean DEFAULT true NOT NULL,
    sort integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT north_star_config_pkey PRIMARY KEY (program, key)
);

CREATE TABLE IF NOT EXISTS public.program_agents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    program text NOT NULL,
    agent_id text NOT NULL,
    name text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'unknown'::text NOT NULL,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT program_agents_program_check CHECK ((program = ANY (ARRAY['seekers'::text, 'providers'::text]))),
    CONSTRAINT program_agents_pkey PRIMARY KEY (id),
    CONSTRAINT program_agents_program_agent_id_key UNIQUE (program, agent_id)
);

CREATE TABLE IF NOT EXISTS public.program_export_targets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    program text NOT NULL,
    sheet_id text DEFAULT ''::text NOT NULL,
    tab_name text,
    label text,
    enabled boolean DEFAULT true NOT NULL,
    last_exported_at timestamp with time zone,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT program_export_targets_pkey PRIMARY KEY (id),
    CONSTRAINT program_export_targets_program_key UNIQUE (program)
);

CREATE TABLE IF NOT EXISTS public.program_sync_state (
    program text NOT NULL,
    last_synced_at timestamp with time zone,
    row_count integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'idle'::text NOT NULL,
    last_error text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT program_sync_state_pkey PRIMARY KEY (program)
);

CREATE TABLE IF NOT EXISTS public.reviewers (
    email text NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reviewers_pkey PRIMARY KEY (email)
);

CREATE TABLE IF NOT EXISTS public.sheet_connections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    program text NOT NULL,
    name text NOT NULL,
    sheet_id text NOT NULL,
    tab_name text,
    enabled boolean DEFAULT true NOT NULL,
    status text DEFAULT 'unknown'::text NOT NULL,
    row_count integer,
    last_synced_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_error text,
    channel text DEFAULT 'outbound'::text NOT NULL,
    CONSTRAINT sheet_connections_program_check CHECK ((program = ANY (ARRAY['seekers'::text, 'providers'::text]))),
    CONSTRAINT sheet_connections_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.transcript_reviews (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    job_id text,
    call_id text,
    reviewer_name text,
    reviewer_email text,
    company_name text,
    campaign_day text,
    campaign_type text,
    language text,
    city_campaign text,
    contact_phone text,
    call_outcome text,
    job_status_in_master text,
    review_type text,
    quantitative_issues text,
    turn_flags text,
    overall_rating integer,
    reviewer_notes text,
    summary_match text,
    job_status_correct text,
    output_fields_accurate text,
    dataset text,
    CONSTRAINT transcript_reviews_pkey PRIMARY KEY (id)
);


-- functions

CREATE OR REPLACE FUNCTION public.app_user_login(_email text, _password text DEFAULT NULL::text) RETURNS TABLE(email text, name text, role text, district text, program text, node_type text, node_name text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'extensions'
    AS $$
  select u.email, u.name, u.role, u.district, u.program, u.node_type, u.node_name
  from public.app_users u
  where u.active and u.email = lower(btrim(_email))
    and (u.password_hash is null or (_password is not null and crypt(_password, u.password_hash) = u.password_hash))
  limit 1;
$$;

CREATE OR REPLACE FUNCTION public.get_campaign_drop_causes(_campaign text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE
  dom_region text;
  sample_calls int;
  result jsonb;
BEGIN
  WITH base AS (
    SELECT
      COALESCE(call_answered,false) AS answered,
      COALESCE(call_engaged,false)  AS engaged,
      COALESCE(intent_score,0)      AS intent_score,
      COALESCE(applied_to_job,false) AS applied_to_job,
      COALESCE(tried_to_apply,false) AS tried_to_apply,
      COALESCE(drop_reason,'')      AS drop_reason,
      COALESCE(phases_reached,'') AS phases_reached,
      btrim(COALESCE(drop_reason,'')) AS dr_clean,
      ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag,
      (jsonb_typeof(data->'jobs_applied')='array' AND jsonb_array_length(data->'jobs_applied')>0) AS jobs_applied_nonempty,
      (jsonb_typeof(data->'jobs_failed_to_apply')='array' AND jsonb_array_length(data->'jobs_failed_to_apply')>0) AS jobs_failed_nonempty,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date,1,10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist',1,10)::date
        ELSE NULL
      END AS call_date,
      COALESCE(campaign_type,'') AS ctype
    FROM public.call_rows
    WHERE program = 'seekers'
      AND (_channel = 'all' OR channel = _channel)
  ),
  filtered_all AS (
    SELECT * FROM base
    WHERE (_state = 'all' OR region_code = _state)
      AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
  ),
  campaign_rows AS (
    SELECT * FROM filtered_all WHERE ctype = _campaign
  )
  SELECT
    (SELECT COUNT(*)::int FROM campaign_rows),
    (SELECT region_code FROM campaign_rows
      WHERE region_code IS NOT NULL
      GROUP BY region_code
      ORDER BY COUNT(*) DESC
      LIMIT 1)
  INTO sample_calls, dom_region;

  WITH base AS (
    SELECT
      COALESCE(call_answered,false) AS answered,
      COALESCE(call_engaged,false)  AS engaged,
      COALESCE(intent_score,0)      AS intent_score,
      COALESCE(applied_to_job,false) AS applied_to_job,
      COALESCE(tried_to_apply,false) AS tried_to_apply,
      COALESCE(drop_reason,'')      AS drop_reason,
      COALESCE(phases_reached,'') AS phases_reached,
      btrim(COALESCE(drop_reason,'')) AS dr_clean,
      ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag,
      (jsonb_typeof(data->'jobs_applied')='array' AND jsonb_array_length(data->'jobs_applied')>0) AS jobs_applied_nonempty,
      (jsonb_typeof(data->'jobs_failed_to_apply')='array' AND jsonb_array_length(data->'jobs_failed_to_apply')>0) AS jobs_failed_nonempty,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date,1,10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist',1,10)::date
        ELSE NULL
      END AS call_date,
      COALESCE(campaign_type,'') AS ctype
    FROM public.call_rows
    WHERE program = 'seekers'
      AND (_channel = 'all' OR channel = _channel)
  ),
  date_filtered AS (
    SELECT * FROM base
    WHERE (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
  ),
  non_conv AS (
    SELECT
      (ctype = _campaign AND (_state = 'all' OR region_code = _state)) AS in_campaign,
      (dom_region IS NULL OR region_code = dom_region) AS in_region,
      answered, engaged, intent_score,
      applied_to_job, jobs_applied_nonempty,
      btrim(phases_reached) AS phase,
      -- job-era phase CASE removed; the normalised bot stage is authoritative

      -- 8 canonical drop reasons upstream, so the reason IS the bucket
      coalesce(nullif(dr_clean,''), 'Not captured') AS bucket
    FROM date_filtered
    WHERE answered = true
      AND NOT (applied_to_job OR jobs_applied_nonempty)
      AND btrim(COALESCE(phases_reached,'')) <> ''
      AND drop_reason NOT ILIKE '%suicidal%'
      AND drop_reason NOT ILIKE '%distress%'
  ),
  hi_seg AS (
    SELECT * FROM non_conv WHERE in_campaign
      AND phase IN ('Match provider', 'Connect to provider')
  ),
  hi_seg_total AS ( SELECT COUNT(*)::int AS n FROM hi_seg ),
  hi_top AS (
    SELECT phase, bucket, COUNT(*)::int AS cnt
    FROM hi_seg
    GROUP BY phase, bucket
    ORDER BY cnt DESC
    LIMIT 3
  ),
  phase_labels AS (
    SELECT * FROM (VALUES
      ('Profile fetch & name confirmation','Profile fetch',1),
      ('Profile completion & verification','Profile completion',2),
      ('Identify disability type','Disability type',3),
      ('Needs & challenges evaluation','Needs & challenges',4),
      ('Options delivery & decision making','Options delivery',5),
      ('Summary & profile update','Summary & update',6),
      ('Match provider','Match provider',7),
      ('Connect to provider','Connect to provider',8)
    ) AS t(k,l,ord)
  ),
  camp_phase AS (
    SELECT phase, COUNT(*)::int AS cnt FROM non_conv WHERE in_campaign GROUP BY phase
  ),
  camp_total AS ( SELECT COALESCE(SUM(cnt),0)::int AS n FROM camp_phase ),
  reg_phase AS (
    SELECT phase, COUNT(*)::int AS cnt FROM non_conv WHERE in_region GROUP BY phase
  ),
  reg_total AS ( SELECT COALESCE(SUM(cnt),0)::int AS n FROM reg_phase ),
  phase_share AS (
    SELECT jsonb_agg(
      jsonb_build_object(
        'phaseKey', pl.k,
        'phaseLabel', pl.l,
        'campaignPct', CASE WHEN (SELECT n FROM camp_total) = 0 THEN 0
          ELSE ROUND(100.0 * COALESCE(cp.cnt,0) / (SELECT n FROM camp_total), 1) END,
        'regionPct', CASE WHEN (SELECT n FROM reg_total) = 0 THEN 0
          ELSE ROUND(100.0 * COALESCE(rp.cnt,0) / (SELECT n FROM reg_total), 1) END
      ) ORDER BY pl.ord
    ) AS arr
    FROM phase_labels pl
    LEFT JOIN camp_phase cp ON cp.phase = pl.k
    LEFT JOIN reg_phase rp ON rp.phase = pl.k
  ),
  hi_top_json AS (
    SELECT COALESCE(jsonb_agg(
      jsonb_build_object(
        'phase', (SELECT l FROM phase_labels WHERE k = ht.phase),
        'reason', ht.bucket,
        'count', ht.cnt,
        'pct', CASE WHEN (SELECT n FROM hi_seg_total) = 0 THEN 0
          ELSE ROUND(100.0 * ht.cnt / (SELECT n FROM hi_seg_total), 1) END
      ) ORDER BY ht.cnt DESC
    ), '[]'::jsonb) AS arr
    FROM hi_top ht
  )
  SELECT jsonb_build_object(
    'sampleCalls', sample_calls,
    'region', dom_region,
    'highIntentNonApply', jsonb_build_object(
      'segment', (SELECT n FROM hi_seg_total),
      'top', (SELECT arr FROM hi_top_json)
    ),
    'phaseShare', COALESCE((SELECT arr FROM phase_share), '[]'::jsonb)
  ) INTO result;

  RETURN result;
END;
$_$;

CREATE OR REPLACE FUNCTION public.get_campaign_drop_causes_np(_campaign text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE
  dom_region text;
  sample_calls int;
  result jsonb;
BEGIN
  WITH base AS (
    SELECT
      COALESCE(call_answered,false) AS answered,
      COALESCE(call_engaged,false)  AS engaged,
      COALESCE(intent_score,0)      AS intent_score,
      COALESCE(applied_to_job,false) AS applied_to_job,
      COALESCE(tried_to_apply,false) AS tried_to_apply,
      COALESCE(drop_reason,'')      AS drop_reason,
      COALESCE(phases_reached,'') AS phases_reached,
      btrim(COALESCE(drop_reason,'')) AS dr_clean,
      ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag,
      (jsonb_typeof(data->'jobs_applied')='array' AND jsonb_array_length(data->'jobs_applied')>0) AS jobs_applied_nonempty,
      (jsonb_typeof(data->'jobs_failed_to_apply')='array' AND jsonb_array_length(data->'jobs_failed_to_apply')>0) AS jobs_failed_nonempty,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date,1,10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist',1,10)::date
        ELSE NULL
      END AS call_date,
      COALESCE(campaign_type,'') AS ctype
    FROM public.call_rows_np
    WHERE program = 'seekers'
      AND (_channel = 'all' OR channel = _channel)
  ),
  filtered_all AS (
    SELECT * FROM base
    WHERE (_state = 'all' OR region_code = _state)
      AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
  ),
  campaign_rows AS (
    SELECT * FROM filtered_all WHERE ctype = _campaign
  )
  SELECT
    (SELECT COUNT(*)::int FROM campaign_rows),
    (SELECT region_code FROM campaign_rows
      WHERE region_code IS NOT NULL
      GROUP BY region_code
      ORDER BY COUNT(*) DESC
      LIMIT 1)
  INTO sample_calls, dom_region;

  WITH base AS (
    SELECT
      COALESCE(call_answered,false) AS answered,
      COALESCE(call_engaged,false)  AS engaged,
      COALESCE(intent_score,0)      AS intent_score,
      COALESCE(applied_to_job,false) AS applied_to_job,
      COALESCE(tried_to_apply,false) AS tried_to_apply,
      COALESCE(drop_reason,'')      AS drop_reason,
      COALESCE(phases_reached,'') AS phases_reached,
      btrim(COALESCE(drop_reason,'')) AS dr_clean,
      ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag,
      (jsonb_typeof(data->'jobs_applied')='array' AND jsonb_array_length(data->'jobs_applied')>0) AS jobs_applied_nonempty,
      (jsonb_typeof(data->'jobs_failed_to_apply')='array' AND jsonb_array_length(data->'jobs_failed_to_apply')>0) AS jobs_failed_nonempty,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date,1,10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist',1,10)::date
        ELSE NULL
      END AS call_date,
      COALESCE(campaign_type,'') AS ctype
    FROM public.call_rows_np
    WHERE program = 'seekers'
      AND (_channel = 'all' OR channel = _channel)
  ),
  date_filtered AS (
    SELECT * FROM base
    WHERE (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
  ),
  non_conv AS (
    SELECT
      (ctype = _campaign AND (_state = 'all' OR region_code = _state)) AS in_campaign,
      (dom_region IS NULL OR region_code = dom_region) AS in_region,
      answered, engaged, intent_score,
      applied_to_job, jobs_applied_nonempty,
      btrim(phases_reached) AS phase,
      -- job-era phase CASE removed; the normalised bot stage is authoritative

      -- 8 canonical drop reasons upstream, so the reason IS the bucket
      coalesce(nullif(dr_clean,''), 'Not captured') AS bucket
    FROM date_filtered
    WHERE answered = true
      AND NOT (applied_to_job OR jobs_applied_nonempty)
      AND btrim(COALESCE(phases_reached,'')) <> ''
      AND drop_reason NOT ILIKE '%suicidal%'
      AND drop_reason NOT ILIKE '%distress%'
  ),
  hi_seg AS (
    SELECT * FROM non_conv WHERE in_campaign
      AND phase IN ('Match provider', 'Connect to provider')
  ),
  hi_seg_total AS ( SELECT COUNT(*)::int AS n FROM hi_seg ),
  hi_top AS (
    SELECT phase, bucket, COUNT(*)::int AS cnt
    FROM hi_seg
    GROUP BY phase, bucket
    ORDER BY cnt DESC
    LIMIT 3
  ),
  phase_labels AS (
    SELECT * FROM (VALUES
      ('Profile fetch & name confirmation','Profile fetch',1),
      ('Profile completion & verification','Profile completion',2),
      ('Identify disability type','Disability type',3),
      ('Needs & challenges evaluation','Needs & challenges',4),
      ('Options delivery & decision making','Options delivery',5),
      ('Summary & profile update','Summary & update',6),
      ('Match provider','Match provider',7),
      ('Connect to provider','Connect to provider',8)
    ) AS t(k,l,ord)
  ),
  camp_phase AS (
    SELECT phase, COUNT(*)::int AS cnt FROM non_conv WHERE in_campaign GROUP BY phase
  ),
  camp_total AS ( SELECT COALESCE(SUM(cnt),0)::int AS n FROM camp_phase ),
  reg_phase AS (
    SELECT phase, COUNT(*)::int AS cnt FROM non_conv WHERE in_region GROUP BY phase
  ),
  reg_total AS ( SELECT COALESCE(SUM(cnt),0)::int AS n FROM reg_phase ),
  phase_share AS (
    SELECT jsonb_agg(
      jsonb_build_object(
        'phaseKey', pl.k,
        'phaseLabel', pl.l,
        'campaignPct', CASE WHEN (SELECT n FROM camp_total) = 0 THEN 0
          ELSE ROUND(100.0 * COALESCE(cp.cnt,0) / (SELECT n FROM camp_total), 1) END,
        'regionPct', CASE WHEN (SELECT n FROM reg_total) = 0 THEN 0
          ELSE ROUND(100.0 * COALESCE(rp.cnt,0) / (SELECT n FROM reg_total), 1) END
      ) ORDER BY pl.ord
    ) AS arr
    FROM phase_labels pl
    LEFT JOIN camp_phase cp ON cp.phase = pl.k
    LEFT JOIN reg_phase rp ON rp.phase = pl.k
  ),
  hi_top_json AS (
    SELECT COALESCE(jsonb_agg(
      jsonb_build_object(
        'phase', (SELECT l FROM phase_labels WHERE k = ht.phase),
        'reason', ht.bucket,
        'count', ht.cnt,
        'pct', CASE WHEN (SELECT n FROM hi_seg_total) = 0 THEN 0
          ELSE ROUND(100.0 * ht.cnt / (SELECT n FROM hi_seg_total), 1) END
      ) ORDER BY ht.cnt DESC
    ), '[]'::jsonb) AS arr
    FROM hi_top ht
  )
  SELECT jsonb_build_object(
    'sampleCalls', sample_calls,
    'region', dom_region,
    'highIntentNonApply', jsonb_build_object(
      'segment', (SELECT n FROM hi_seg_total),
      'top', (SELECT arr FROM hi_top_json)
    ),
    'phaseShare', COALESCE((SELECT arr FROM phase_share), '[]'::jsonb)
  ) INTO result;

  RETURN result;
END;
$_$;

CREATE OR REPLACE FUNCTION public.get_campaign_list(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
WITH base AS (
  SELECT
    COALESCE(campaign_type,'') AS campaign_type,
    COALESCE(language,'') AS language,
    phone,
    COALESCE(call_answered, false) AS call_answered,
    COALESCE(call_engaged, false) AS call_engaged,
    COALESCE(intent_score, 0) AS intent_score,
    COALESCE(applied_to_job, false) AS applied_to_job,
    (jsonb_typeof(data->'jobs_applied') = 'array' AND jsonb_array_length(data->'jobs_applied') > 0) AS jobs_applied_flag,
    lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
    COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
    CASE
      WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
        THEN (DATE '1899-12-30' + (campaign_date)::int)::date
      WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(campaign_date,1,10)::date
      WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(data->>'call_datetime_ist',1,10)::date
      ELSE NULL
    END AS call_date
  FROM public.call_rows
  WHERE program = _program
      AND (_channel = 'all' OR channel = _channel)
),
filtered AS (
  SELECT * FROM base
  WHERE campaign_type <> ''
    AND (_state = 'all' OR region_code = _state)
    AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
    AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
),
grouped AS (
  SELECT
    campaign_type,
    call_date AS campaign_date,
    CASE WHEN COUNT(DISTINCT NULLIF(language,'')) = 1 THEN MIN(NULLIF(language,'')) ELSE NULL END AS language,
    CASE WHEN COUNT(DISTINCT region_code) FILTER (WHERE region_code IS NOT NULL) = 1
         THEN MIN(region_code)
         ELSE NULL END AS region,
    COUNT(*)::int AS total_calls,
    COUNT(*) FILTER (WHERE call_answered)::int AS answered,
    COUNT(*) FILTER (WHERE call_answered AND call_engaged)::int AS engaged,
    COUNT(*) FILTER (WHERE call_answered AND intent_score >= 5)::int AS high_intent,
    CASE WHEN _program = 'providers'
      THEN COUNT(DISTINCT phone) FILTER (WHERE js = 'active' AND phone IS NOT NULL AND phone <> '')::int
      ELSE COUNT(*) FILTER (WHERE applied_to_job OR jobs_applied_flag)::int
    END AS converted
  FROM filtered
  GROUP BY campaign_type, call_date
)
SELECT COALESCE(jsonb_agg(
  jsonb_build_object(
    'campaignType', campaign_type,
    'campaignDate', CASE WHEN campaign_date IS NULL THEN NULL ELSE to_char(campaign_date,'YYYY-MM-DD') END,
    'language', language,
    'region', region,
    'totalCalls', total_calls,
    'answered', answered,
    'engaged', engaged,
    'highIntent', high_intent,
    'converted', converted
  ) ORDER BY campaign_date DESC NULLS LAST, campaign_type
), '[]'::jsonb)
FROM grouped;
$_$;

CREATE OR REPLACE FUNCTION public.get_campaign_list_np(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
WITH base AS (
  SELECT
    COALESCE(campaign_type,'') AS campaign_type,
    COALESCE(language,'') AS language,
    phone,
    COALESCE(call_answered, false) AS call_answered,
    COALESCE(call_engaged, false) AS call_engaged,
    COALESCE(intent_score, 0) AS intent_score,
    COALESCE(applied_to_job, false) AS applied_to_job,
    (jsonb_typeof(data->'jobs_applied') = 'array' AND jsonb_array_length(data->'jobs_applied') > 0) AS jobs_applied_flag,
    lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
    COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
    CASE
      WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
        THEN (DATE '1899-12-30' + (campaign_date)::int)::date
      WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(campaign_date,1,10)::date
      WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(data->>'call_datetime_ist',1,10)::date
      ELSE NULL
    END AS call_date
  FROM public.call_rows_np
  WHERE program = _program
      AND (_channel = 'all' OR channel = _channel)
),
filtered AS (
  SELECT * FROM base
  WHERE campaign_type <> ''
    AND (_state = 'all' OR region_code = _state)
    AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
    AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
),
grouped AS (
  SELECT
    campaign_type,
    call_date AS campaign_date,
    CASE WHEN COUNT(DISTINCT NULLIF(language,'')) = 1 THEN MIN(NULLIF(language,'')) ELSE NULL END AS language,
    CASE WHEN COUNT(DISTINCT region_code) FILTER (WHERE region_code IS NOT NULL) = 1
         THEN MIN(region_code)
         ELSE NULL END AS region,
    COUNT(*)::int AS total_calls,
    COUNT(*) FILTER (WHERE call_answered)::int AS answered,
    COUNT(*) FILTER (WHERE call_answered AND call_engaged)::int AS engaged,
    COUNT(*) FILTER (WHERE call_answered AND intent_score >= 5)::int AS high_intent,
    CASE WHEN _program = 'providers'
      THEN COUNT(DISTINCT phone) FILTER (WHERE js = 'active' AND phone IS NOT NULL AND phone <> '')::int
      ELSE COUNT(*) FILTER (WHERE applied_to_job OR jobs_applied_flag)::int
    END AS converted
  FROM filtered
  GROUP BY campaign_type, call_date
)
SELECT COALESCE(jsonb_agg(
  jsonb_build_object(
    'campaignType', campaign_type,
    'campaignDate', CASE WHEN campaign_date IS NULL THEN NULL ELSE to_char(campaign_date,'YYYY-MM-DD') END,
    'language', language,
    'region', region,
    'totalCalls', total_calls,
    'answered', answered,
    'engaged', engaged,
    'highIntent', high_intent,
    'converted', converted
  ) ORDER BY campaign_date DESC NULLS LAST, campaign_type
), '[]'::jsonb)
FROM grouped;
$_$;

CREATE OR REPLACE FUNCTION public.get_dkb_campaign_causes(_campaign text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE
  dom_region text;
  sample_calls int;
  result jsonb;
BEGIN
  WITH base AS (
    SELECT
      COALESCE(call_answered,false) AS answered,
      COALESCE(drop_reason,'')      AS drop_reason,
      lower(coalesce(new_job_posted, data->'raw'->>'new_job_posted','')) IN ('true','yes','y','1') AS converted,
      lower(coalesce(job_status, data->'raw'->>'job_status','')) IN ('active','closed') AS has_status,
      (
        lower(coalesce(talent_insights_shown, data->'raw'->>'talent_insights_shown','')) IN ('true','yes','y','1')
        OR lower(coalesce(data->'raw'->>'fields_updated','')) NOT IN ('','0','none','no','false','nan')
      ) AS updated,
      lower(coalesce(data->'raw'->>'new_job_mentioned','')) IN ('true','yes','y','1') AS mentioned,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date,1,10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist',1,10)::date
        ELSE NULL
      END AS call_date,
      COALESCE(campaign_type,'') AS ctype
    FROM public.call_rows
    WHERE program = 'providers'
      AND (_channel = 'all' OR channel = _channel)
  ),
  filtered_all AS (
    SELECT * FROM base
    WHERE (_state = 'all' OR region_code = _state)
      AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
  ),
  campaign_rows AS (
    SELECT * FROM filtered_all WHERE ctype = _campaign
  )
  SELECT
    (SELECT COUNT(*)::int FROM campaign_rows),
    (SELECT region_code FROM campaign_rows
      WHERE region_code IS NOT NULL
      GROUP BY region_code
      ORDER BY COUNT(*) DESC
      LIMIT 1)
  INTO sample_calls, dom_region;

  WITH base AS (
    SELECT
      COALESCE(call_answered,false) AS answered,
      COALESCE(drop_reason,'')      AS drop_reason,
      lower(coalesce(new_job_posted, data->'raw'->>'new_job_posted','')) IN ('true','yes','y','1') AS converted,
      lower(coalesce(job_status, data->'raw'->>'job_status','')) IN ('active','closed') AS has_status,
      (
        lower(coalesce(talent_insights_shown, data->'raw'->>'talent_insights_shown','')) IN ('true','yes','y','1')
        OR lower(coalesce(data->'raw'->>'fields_updated','')) NOT IN ('','0','none','no','false','nan')
      ) AS updated,
      lower(coalesce(data->'raw'->>'new_job_mentioned','')) IN ('true','yes','y','1') AS mentioned,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date,1,10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist',1,10)::date
        ELSE NULL
      END AS call_date,
      COALESCE(campaign_type,'') AS ctype
    FROM public.call_rows
    WHERE program = 'providers'
      AND (_channel = 'all' OR channel = _channel)
  ),
  date_filtered AS (
    SELECT * FROM base
    WHERE (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
  ),
  non_conv AS (
    SELECT
      (ctype = _campaign AND (_state = 'all' OR region_code = _state)) AS in_campaign,
      (dom_region IS NULL OR region_code = dom_region) AS in_region,
      CASE
        WHEN mentioned THEN 'new_job'
        WHEN updated   THEN 'update'
        WHEN has_status THEN 'refresh'
        ELSE 'intro'
      END AS phase
    FROM date_filtered
    WHERE answered = true AND converted = false
  ),
  phase_labels AS (
    SELECT * FROM (VALUES
      ('intro','Introduction',1),
      ('refresh','Refresh',2),
      ('update','Update',3),
      ('new_job','New Job Posted',4)
    ) AS t(k,l,ord)
  ),
  camp_phase AS (
    SELECT phase, COUNT(*)::int AS cnt FROM non_conv WHERE in_campaign GROUP BY phase
  ),
  camp_total AS ( SELECT COALESCE(SUM(cnt),0)::int AS n FROM camp_phase ),
  reg_phase AS (
    SELECT phase, COUNT(*)::int AS cnt FROM non_conv WHERE in_region GROUP BY phase
  ),
  reg_total AS ( SELECT COALESCE(SUM(cnt),0)::int AS n FROM reg_phase ),
  phase_share AS (
    SELECT jsonb_agg(
      jsonb_build_object(
        'phaseKey', pl.k,
        'phaseLabel', pl.l,
        'campaignPct', CASE WHEN (SELECT n FROM camp_total) = 0 THEN 0
          ELSE ROUND(100.0 * COALESCE(cp.cnt,0) / (SELECT n FROM camp_total), 1) END,
        'regionPct', CASE WHEN (SELECT n FROM reg_total) = 0 THEN 0
          ELSE ROUND(100.0 * COALESCE(rp.cnt,0) / (SELECT n FROM reg_total), 1) END
      ) ORDER BY pl.ord
    ) AS arr
    FROM phase_labels pl
    LEFT JOIN camp_phase cp ON cp.phase = pl.k
    LEFT JOIN reg_phase rp ON rp.phase = pl.k
  )
  SELECT jsonb_build_object(
    'sampleCalls', sample_calls,
    'region', dom_region,
    'phaseShare', COALESCE((SELECT arr FROM phase_share), '[]'::jsonb)
  ) INTO result;

  RETURN result;
END;
$_$;

CREATE OR REPLACE FUNCTION public.get_dkb_campaign_causes_np(_campaign text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE
  dom_region text;
  sample_calls int;
  result jsonb;
BEGIN
  WITH base AS (
    SELECT
      COALESCE(call_answered,false) AS answered,
      COALESCE(drop_reason,'')      AS drop_reason,
      lower(coalesce(new_job_posted, data->'raw'->>'new_job_posted','')) IN ('true','yes','y','1') AS converted,
      lower(coalesce(job_status, data->'raw'->>'job_status','')) IN ('active','closed') AS has_status,
      (
        lower(coalesce(talent_insights_shown, data->'raw'->>'talent_insights_shown','')) IN ('true','yes','y','1')
        OR lower(coalesce(data->'raw'->>'fields_updated','')) NOT IN ('','0','none','no','false','nan')
      ) AS updated,
      lower(coalesce(data->'raw'->>'new_job_mentioned','')) IN ('true','yes','y','1') AS mentioned,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date,1,10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist',1,10)::date
        ELSE NULL
      END AS call_date,
      COALESCE(campaign_type,'') AS ctype
    FROM public.call_rows_np
    WHERE program = 'providers'
      AND (_channel = 'all' OR channel = _channel)
  ),
  filtered_all AS (
    SELECT * FROM base
    WHERE (_state = 'all' OR region_code = _state)
      AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
  ),
  campaign_rows AS (
    SELECT * FROM filtered_all WHERE ctype = _campaign
  )
  SELECT
    (SELECT COUNT(*)::int FROM campaign_rows),
    (SELECT region_code FROM campaign_rows
      WHERE region_code IS NOT NULL
      GROUP BY region_code
      ORDER BY COUNT(*) DESC
      LIMIT 1)
  INTO sample_calls, dom_region;

  WITH base AS (
    SELECT
      COALESCE(call_answered,false) AS answered,
      COALESCE(drop_reason,'')      AS drop_reason,
      lower(coalesce(new_job_posted, data->'raw'->>'new_job_posted','')) IN ('true','yes','y','1') AS converted,
      lower(coalesce(job_status, data->'raw'->>'job_status','')) IN ('active','closed') AS has_status,
      (
        lower(coalesce(talent_insights_shown, data->'raw'->>'talent_insights_shown','')) IN ('true','yes','y','1')
        OR lower(coalesce(data->'raw'->>'fields_updated','')) NOT IN ('','0','none','no','false','nan')
      ) AS updated,
      lower(coalesce(data->'raw'->>'new_job_mentioned','')) IN ('true','yes','y','1') AS mentioned,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date,1,10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist',1,10)::date
        ELSE NULL
      END AS call_date,
      COALESCE(campaign_type,'') AS ctype
    FROM public.call_rows_np
    WHERE program = 'providers'
      AND (_channel = 'all' OR channel = _channel)
  ),
  date_filtered AS (
    SELECT * FROM base
    WHERE (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
  ),
  non_conv AS (
    SELECT
      (ctype = _campaign AND (_state = 'all' OR region_code = _state)) AS in_campaign,
      (dom_region IS NULL OR region_code = dom_region) AS in_region,
      CASE
        WHEN mentioned THEN 'new_job'
        WHEN updated   THEN 'update'
        WHEN has_status THEN 'refresh'
        ELSE 'intro'
      END AS phase
    FROM date_filtered
    WHERE answered = true AND converted = false
  ),
  phase_labels AS (
    SELECT * FROM (VALUES
      ('intro','Introduction',1),
      ('refresh','Refresh',2),
      ('update','Update',3),
      ('new_job','New Job Posted',4)
    ) AS t(k,l,ord)
  ),
  camp_phase AS (
    SELECT phase, COUNT(*)::int AS cnt FROM non_conv WHERE in_campaign GROUP BY phase
  ),
  camp_total AS ( SELECT COALESCE(SUM(cnt),0)::int AS n FROM camp_phase ),
  reg_phase AS (
    SELECT phase, COUNT(*)::int AS cnt FROM non_conv WHERE in_region GROUP BY phase
  ),
  reg_total AS ( SELECT COALESCE(SUM(cnt),0)::int AS n FROM reg_phase ),
  phase_share AS (
    SELECT jsonb_agg(
      jsonb_build_object(
        'phaseKey', pl.k,
        'phaseLabel', pl.l,
        'campaignPct', CASE WHEN (SELECT n FROM camp_total) = 0 THEN 0
          ELSE ROUND(100.0 * COALESCE(cp.cnt,0) / (SELECT n FROM camp_total), 1) END,
        'regionPct', CASE WHEN (SELECT n FROM reg_total) = 0 THEN 0
          ELSE ROUND(100.0 * COALESCE(rp.cnt,0) / (SELECT n FROM reg_total), 1) END
      ) ORDER BY pl.ord
    ) AS arr
    FROM phase_labels pl
    LEFT JOIN camp_phase cp ON cp.phase = pl.k
    LEFT JOIN reg_phase rp ON rp.phase = pl.k
  )
  SELECT jsonb_build_object(
    'sampleCalls', sample_calls,
    'region', dom_region,
    'phaseShare', COALESCE((SELECT arr FROM phase_share), '[]'::jsonb)
  ) INTO result;

  RETURN result;
END;
$_$;

CREATE OR REPLACE FUNCTION public.get_dkb_drop_analysis(_state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
WITH base AS (
  SELECT
    COALESCE(call_answered,false) AS answered,
    COALESCE(drop_reason,'')      AS drop_reason,
    lower(coalesce(new_job_posted, data->'raw'->>'new_job_posted','')) IN ('true','yes','y','1') AS converted,
    lower(coalesce(job_status, data->'raw'->>'job_status','')) IN ('active','closed') AS has_status,
    (
      lower(coalesce(talent_insights_shown, data->'raw'->>'talent_insights_shown','')) IN ('true','yes','y','1')
      OR lower(coalesce(data->'raw'->>'fields_updated','')) NOT IN ('','0','none','no','false','nan')
    ) AS updated,
    lower(coalesce(data->'raw'->>'new_job_mentioned','')) IN ('true','yes','y','1') AS mentioned,
    COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
    CASE
      WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
        THEN (DATE '1899-12-30' + (campaign_date)::int)::date
      WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(campaign_date,1,10)::date
      WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(data->>'call_datetime_ist',1,10)::date
      ELSE NULL
    END AS call_date,
    COALESCE(campaign_type,'') AS ctype
  FROM public.call_rows
  WHERE program = 'providers'
      AND (_channel = 'all' OR channel = _channel)
),
filtered AS (
  SELECT * FROM base
  WHERE answered = true
    AND converted = false
    AND (_state = 'all' OR region_code = _state)
    AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
    AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
    AND (_campaign IS NULL OR ctype = _campaign)
    AND (
      _campaign_type = 'all' OR ctype = _campaign_type
    )
),
staged AS (
  SELECT
    drop_reason,
    CASE
      WHEN mentioned THEN 'new_job'
      WHEN updated   THEN 'update'
      WHEN has_status THEN 'refresh'
      ELSE 'intro'
    END AS stage
  FROM filtered
),
bucketed AS (
  SELECT
    stage,
    drop_reason,
    CASE
      WHEN LOWER(TRIM(drop_reason)) LIKE '%hung%'
        OR LOWER(TRIM(drop_reason)) LIKE '%hang%'
        OR LOWER(TRIM(drop_reason)) LIKE '%disengage%' THEN 'Hung up / disengaged'
      WHEN LOWER(TRIM(drop_reason)) LIKE '%audio%'
        OR LOWER(TRIM(drop_reason)) LIKE '%comprehension%'
        OR LOWER(TRIM(drop_reason)) LIKE '%unclear%'
        OR LOWER(TRIM(drop_reason)) LIKE '%inaudible%' THEN 'Audio / comprehension'
      WHEN LOWER(TRIM(drop_reason)) LIKE '%refus%'
        OR LOWER(TRIM(drop_reason)) LIKE '%declin%' THEN 'Owner refused / declined'
      WHEN LOWER(TRIM(drop_reason)) LIKE '%busy%'
        OR LOWER(TRIM(drop_reason)) LIKE '%call back%'
        OR LOWER(TRIM(drop_reason)) LIKE '%callback%'
        OR LOWER(TRIM(drop_reason)) LIKE '%call later%' THEN 'Busy / callback'
      WHEN TRIM(COALESCE(drop_reason,'')) = ''
        OR TRIM(COALESCE(drop_reason,'')) ~ '^[0-9]+(\.[0-9]+)?$' THEN 'Not captured'
      ELSE 'Other'
    END AS bucket
  FROM staged
),
agg AS (
  SELECT
    bucket,
    stage,
    COALESCE(NULLIF(TRIM(drop_reason),''), 'Not captured') AS reason_label,
    COUNT(*)::int AS cnt
  FROM bucketed
  GROUP BY 1,2,3
),
by_stage AS (
  SELECT bucket, stage, SUM(cnt)::int AS cnt FROM agg GROUP BY 1,2
),
bucket_totals AS (
  SELECT bucket, SUM(cnt)::int AS total FROM by_stage GROUP BY 1
),
raw_per_bucket AS (
  SELECT bucket,
    jsonb_agg(jsonb_build_object('reason', reason_label, 'count', cnt) ORDER BY cnt DESC) AS raw
  FROM (
    SELECT bucket, reason_label, SUM(cnt)::int AS cnt FROM agg GROUP BY 1,2
  ) s
  GROUP BY bucket
),
stage_map AS (
  SELECT bucket, jsonb_object_agg(stage, cnt) AS by_stage_obj FROM by_stage GROUP BY 1
),
buckets_json AS (
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object(
      'bucket', bt.bucket,
      'byStage', jsonb_build_object(
        'intro',   COALESCE((sm.by_stage_obj->>'intro')::int, 0),
        'refresh', COALESCE((sm.by_stage_obj->>'refresh')::int, 0),
        'update',  COALESCE((sm.by_stage_obj->>'update')::int, 0),
        'new_job', COALESCE((sm.by_stage_obj->>'new_job')::int, 0)
      ),
      'total', bt.total,
      'raw',   COALESCE(rp.raw, '[]'::jsonb)
    ) ORDER BY bt.total DESC
  ), '[]'::jsonb) AS arr
  FROM bucket_totals bt
  LEFT JOIN stage_map sm USING (bucket)
  LEFT JOIN raw_per_bucket rp USING (bucket)
),
max_cell AS ( SELECT COALESCE(MAX(cnt), 0)::int AS m FROM by_stage ),
grand    AS ( SELECT COALESCE(SUM(total),0)::int AS g FROM bucket_totals )
SELECT jsonb_build_object(
  'stages', jsonb_build_array(
    jsonb_build_object('key','intro',   'label','Introduction'),
    jsonb_build_object('key','refresh', 'label','Refresh'),
    jsonb_build_object('key','update',  'label','Update'),
    jsonb_build_object('key','new_job', 'label','New Job Posted')
  ),
  'buckets',    (SELECT arr FROM buckets_json),
  'maxCell',    (SELECT m FROM max_cell),
  'grandTotal', (SELECT g FROM grand)
);
$_$;

CREATE OR REPLACE FUNCTION public.get_dkb_drop_analysis_np(_state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
WITH base AS (
  SELECT
    COALESCE(call_answered,false) AS answered,
    COALESCE(drop_reason,'')      AS drop_reason,
    lower(coalesce(new_job_posted, data->'raw'->>'new_job_posted','')) IN ('true','yes','y','1') AS converted,
    lower(coalesce(job_status, data->'raw'->>'job_status','')) IN ('active','closed') AS has_status,
    (
      lower(coalesce(talent_insights_shown, data->'raw'->>'talent_insights_shown','')) IN ('true','yes','y','1')
      OR lower(coalesce(data->'raw'->>'fields_updated','')) NOT IN ('','0','none','no','false','nan')
    ) AS updated,
    lower(coalesce(data->'raw'->>'new_job_mentioned','')) IN ('true','yes','y','1') AS mentioned,
    COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
    CASE
      WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
        THEN (DATE '1899-12-30' + (campaign_date)::int)::date
      WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(campaign_date,1,10)::date
      WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(data->>'call_datetime_ist',1,10)::date
      ELSE NULL
    END AS call_date,
    COALESCE(campaign_type,'') AS ctype
  FROM public.call_rows_np
  WHERE program = 'providers'
      AND (_channel = 'all' OR channel = _channel)
),
filtered AS (
  SELECT * FROM base
  WHERE answered = true
    AND converted = false
    AND (_state = 'all' OR region_code = _state)
    AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
    AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
    AND (_campaign IS NULL OR ctype = _campaign)
    AND (
      _campaign_type = 'all' OR ctype = _campaign_type
    )
),
staged AS (
  SELECT
    drop_reason,
    CASE
      WHEN mentioned THEN 'new_job'
      WHEN updated   THEN 'update'
      WHEN has_status THEN 'refresh'
      ELSE 'intro'
    END AS stage
  FROM filtered
),
bucketed AS (
  SELECT
    stage,
    drop_reason,
    CASE
      WHEN LOWER(TRIM(drop_reason)) LIKE '%hung%'
        OR LOWER(TRIM(drop_reason)) LIKE '%hang%'
        OR LOWER(TRIM(drop_reason)) LIKE '%disengage%' THEN 'Hung up / disengaged'
      WHEN LOWER(TRIM(drop_reason)) LIKE '%audio%'
        OR LOWER(TRIM(drop_reason)) LIKE '%comprehension%'
        OR LOWER(TRIM(drop_reason)) LIKE '%unclear%'
        OR LOWER(TRIM(drop_reason)) LIKE '%inaudible%' THEN 'Audio / comprehension'
      WHEN LOWER(TRIM(drop_reason)) LIKE '%refus%'
        OR LOWER(TRIM(drop_reason)) LIKE '%declin%' THEN 'Owner refused / declined'
      WHEN LOWER(TRIM(drop_reason)) LIKE '%busy%'
        OR LOWER(TRIM(drop_reason)) LIKE '%call back%'
        OR LOWER(TRIM(drop_reason)) LIKE '%callback%'
        OR LOWER(TRIM(drop_reason)) LIKE '%call later%' THEN 'Busy / callback'
      WHEN TRIM(COALESCE(drop_reason,'')) = ''
        OR TRIM(COALESCE(drop_reason,'')) ~ '^[0-9]+(\.[0-9]+)?$' THEN 'Not captured'
      ELSE 'Other'
    END AS bucket
  FROM staged
),
agg AS (
  SELECT
    bucket,
    stage,
    COALESCE(NULLIF(TRIM(drop_reason),''), 'Not captured') AS reason_label,
    COUNT(*)::int AS cnt
  FROM bucketed
  GROUP BY 1,2,3
),
by_stage AS (
  SELECT bucket, stage, SUM(cnt)::int AS cnt FROM agg GROUP BY 1,2
),
bucket_totals AS (
  SELECT bucket, SUM(cnt)::int AS total FROM by_stage GROUP BY 1
),
raw_per_bucket AS (
  SELECT bucket,
    jsonb_agg(jsonb_build_object('reason', reason_label, 'count', cnt) ORDER BY cnt DESC) AS raw
  FROM (
    SELECT bucket, reason_label, SUM(cnt)::int AS cnt FROM agg GROUP BY 1,2
  ) s
  GROUP BY bucket
),
stage_map AS (
  SELECT bucket, jsonb_object_agg(stage, cnt) AS by_stage_obj FROM by_stage GROUP BY 1
),
buckets_json AS (
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object(
      'bucket', bt.bucket,
      'byStage', jsonb_build_object(
        'intro',   COALESCE((sm.by_stage_obj->>'intro')::int, 0),
        'refresh', COALESCE((sm.by_stage_obj->>'refresh')::int, 0),
        'update',  COALESCE((sm.by_stage_obj->>'update')::int, 0),
        'new_job', COALESCE((sm.by_stage_obj->>'new_job')::int, 0)
      ),
      'total', bt.total,
      'raw',   COALESCE(rp.raw, '[]'::jsonb)
    ) ORDER BY bt.total DESC
  ), '[]'::jsonb) AS arr
  FROM bucket_totals bt
  LEFT JOIN stage_map sm USING (bucket)
  LEFT JOIN raw_per_bucket rp USING (bucket)
),
max_cell AS ( SELECT COALESCE(MAX(cnt), 0)::int AS m FROM by_stage ),
grand    AS ( SELECT COALESCE(SUM(total),0)::int AS g FROM bucket_totals )
SELECT jsonb_build_object(
  'stages', jsonb_build_array(
    jsonb_build_object('key','intro',   'label','Introduction'),
    jsonb_build_object('key','refresh', 'label','Refresh'),
    jsonb_build_object('key','update',  'label','Update'),
    jsonb_build_object('key','new_job', 'label','New Job Posted')
  ),
  'buckets',    (SELECT arr FROM buckets_json),
  'maxCell',    (SELECT m FROM max_cell),
  'grandTotal', (SELECT g FROM grand)
);
$_$;

CREATE OR REPLACE FUNCTION public.get_funnel_call_ids(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _stage text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE ids jsonb;
BEGIN
  IF _program = 'seekers' THEN
    WITH base AS (
      SELECT COALESCE(call_id,'') AS call_id,
        COALESCE(call_answered,false) AS answered,
        COALESCE(call_engaged,false) AS engaged,
        COALESCE(intent_score,0) AS intent,
        ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag,
        (COALESCE(applied_to_job,false) OR (jsonb_typeof(data->'jobs_applied')='array' AND jsonb_array_length(data->'jobs_applied')>0)) AS submitted_flag,
        (jsonb_typeof(data->'jobs_failed_to_apply')='array' AND jsonb_array_length(data->'jobs_failed_to_apply')>0) AS blocked_flag,
          (lower(btrim(COALESCE(data->'raw'->>'profile_completeness',''))) NOT IN ('', '0', 'no', 'n', 'false')) AS profile_flag,
          (lower(btrim(COALESCE(data->'raw'->>'needs_mentioned',''))) IN ('yes', 'y', 'true', '1')) AS needs_flag,
          (COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'matching_providers_found',''), '[^0-9]', '', 'g'), '')::int, 0) > 0) AS found_flag,
          (COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'providers_connected',''), '[^0-9]', '', 'g'), '')::int, 0) > 0) AS connected_flag,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$' THEN (DATE '1899-12-30' + (campaign_date)::int)::date
             WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(campaign_date,1,10)::date
             WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(data->>'call_datetime_ist',1,10)::date
             ELSE NULL END AS call_date
      FROM public.call_rows WHERE program='seekers'
      AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state='all' OR region_code=_state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type=_campaign)
        AND (_campaign_type = 'all' OR campaign_type = _campaign_type)
    ),
    sel AS (
      SELECT call_id FROM f WHERE call_id <> '' AND (
        CASE _stage
          WHEN 'calls' THEN true
          WHEN 'picked' THEN answered
          WHEN 'engaged' THEN answered AND engaged
          WHEN 'jobs' THEN answered AND engaged AND jobs_shown_flag
          WHEN 'intent' THEN answered AND intent >= 5
          WHEN 'apps' THEN answered AND (submitted_flag OR blocked_flag)
          WHEN 'profile' THEN answered AND engaged AND profile_flag
          WHEN 'needs' THEN answered AND engaged AND profile_flag AND needs_flag
          WHEN 'found' THEN answered AND engaged AND profile_flag AND needs_flag AND found_flag
          WHEN 'connected' THEN answered AND engaged AND profile_flag AND needs_flag AND found_flag AND connected_flag
          ELSE false END)
    )
    SELECT jsonb_agg(call_id) INTO ids FROM sel;
  ELSIF _program='providers' THEN
    WITH base AS (
      SELECT COALESCE(call_id,'') AS call_id,
        lower(COALESCE(call_status,'')) AS cs, call_duration_seconds AS dur,
        lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
        lower(COALESCE(new_job_posted, data->'raw'->>'new_job_posted','')) AS njp,
        phone, COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$' THEN (DATE '1899-12-30' + (campaign_date)::int)::date
             WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(campaign_date,1,10)::date
             WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(data->>'call_datetime_ist',1,10)::date
             ELSE NULL END AS call_date
      FROM public.call_rows WHERE program='providers'
      AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state='all' OR region_code=_state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type=_campaign)
    ),
    sel AS (
      SELECT call_id FROM f WHERE call_id <> '' AND phone IS NOT NULL AND phone <> '' AND (
        CASE _stage
          WHEN 'called' THEN true
          WHEN 'picked' THEN (cs LIKE 'answered%' OR cs='completed')
          WHEN 'engaged' THEN (cs LIKE 'answered%' OR cs='completed') AND COALESCE(dur,0) > 30
          WHEN 'active' THEN js='active'
          WHEN 'new_jobs' THEN njp='yes'
          ELSE false END)
    )
    SELECT jsonb_agg(call_id) INTO ids FROM sel;
  END IF;
  RETURN jsonb_build_object('stage', _stage, 'count', COALESCE(jsonb_array_length(ids),0), 'ids', COALESCE(ids,'[]'::jsonb));
END;
$_$;

CREATE OR REPLACE FUNCTION public.get_funnel_call_ids_np(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _stage text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE ids jsonb;
BEGIN
  IF _program = 'seekers' THEN
    WITH base AS (
      SELECT COALESCE(call_id,'') AS call_id,
        COALESCE(call_answered,false) AS answered,
        COALESCE(call_engaged,false) AS engaged,
        COALESCE(intent_score,0) AS intent,
        ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag,
        (COALESCE(applied_to_job,false) OR (jsonb_typeof(data->'jobs_applied')='array' AND jsonb_array_length(data->'jobs_applied')>0)) AS submitted_flag,
        (jsonb_typeof(data->'jobs_failed_to_apply')='array' AND jsonb_array_length(data->'jobs_failed_to_apply')>0) AS blocked_flag,
          (lower(btrim(COALESCE(data->'raw'->>'profile_completeness',''))) NOT IN ('', '0', 'no', 'n', 'false')) AS profile_flag,
          (lower(btrim(COALESCE(data->'raw'->>'needs_mentioned',''))) IN ('yes', 'y', 'true', '1')) AS needs_flag,
          (COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'matching_providers_found',''), '[^0-9]', '', 'g'), '')::int, 0) > 0) AS found_flag,
          (COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'providers_connected',''), '[^0-9]', '', 'g'), '')::int, 0) > 0) AS connected_flag,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$' THEN (DATE '1899-12-30' + (campaign_date)::int)::date
             WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(campaign_date,1,10)::date
             WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(data->>'call_datetime_ist',1,10)::date
             ELSE NULL END AS call_date
      FROM public.call_rows_np WHERE program='seekers'
      AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state='all' OR region_code=_state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type=_campaign)
        AND (_campaign_type = 'all' OR campaign_type = _campaign_type)
    ),
    sel AS (
      SELECT call_id FROM f WHERE call_id <> '' AND (
        CASE _stage
          WHEN 'calls' THEN true
          WHEN 'picked' THEN answered
          WHEN 'engaged' THEN answered AND engaged
          WHEN 'jobs' THEN answered AND engaged AND jobs_shown_flag
          WHEN 'intent' THEN answered AND intent >= 5
          WHEN 'apps' THEN answered AND (submitted_flag OR blocked_flag)
          WHEN 'profile' THEN answered AND engaged AND profile_flag
          WHEN 'needs' THEN answered AND engaged AND profile_flag AND needs_flag
          WHEN 'found' THEN answered AND engaged AND profile_flag AND needs_flag AND found_flag
          WHEN 'connected' THEN answered AND engaged AND profile_flag AND needs_flag AND found_flag AND connected_flag
          ELSE false END)
    )
    SELECT jsonb_agg(call_id) INTO ids FROM sel;
  ELSIF _program='providers' THEN
    WITH base AS (
      SELECT COALESCE(call_id,'') AS call_id,
        lower(COALESCE(call_status,'')) AS cs, call_duration_seconds AS dur,
        lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
        lower(COALESCE(new_job_posted, data->'raw'->>'new_job_posted','')) AS njp,
        phone, COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$' THEN (DATE '1899-12-30' + (campaign_date)::int)::date
             WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(campaign_date,1,10)::date
             WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(data->>'call_datetime_ist',1,10)::date
             ELSE NULL END AS call_date
      FROM public.call_rows_np WHERE program='providers'
      AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state='all' OR region_code=_state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type=_campaign)
    ),
    sel AS (
      SELECT call_id FROM f WHERE call_id <> '' AND phone IS NOT NULL AND phone <> '' AND (
        CASE _stage
          WHEN 'called' THEN true
          WHEN 'picked' THEN (cs LIKE 'answered%' OR cs='completed')
          WHEN 'engaged' THEN (cs LIKE 'answered%' OR cs='completed') AND COALESCE(dur,0) > 30
          WHEN 'active' THEN js='active'
          WHEN 'new_jobs' THEN njp='yes'
          ELSE false END)
    )
    SELECT jsonb_agg(call_id) INTO ids FROM sel;
  END IF;
  RETURN jsonb_build_object('stage', _stage, 'count', COALESCE(jsonb_array_length(ids),0), 'ids', COALESCE(ids,'[]'::jsonb));
END;
$_$;

CREATE OR REPLACE FUNCTION public.get_funnel_durations(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE res jsonb;
BEGIN
  IF _program = 'seekers' THEN
    WITH base AS (
      SELECT
        COALESCE(call_answered,false) AS answered,
        COALESCE(call_engaged,false) AS engaged,
        COALESCE(intent_score,0) AS intent,
        call_duration_seconds AS dur,
        ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag,
        (COALESCE(applied_to_job,false) OR (jsonb_typeof(data->'jobs_applied')='array' AND jsonb_array_length(data->'jobs_applied')>0)) AS submitted_flag,
        (jsonb_typeof(data->'jobs_failed_to_apply')='array' AND jsonb_array_length(data->'jobs_failed_to_apply')>0) AS blocked_flag,
          (lower(btrim(COALESCE(data->'raw'->>'profile_completeness',''))) NOT IN ('', '0', 'no', 'n', 'false')) AS profile_flag,
          (lower(btrim(COALESCE(data->'raw'->>'needs_mentioned',''))) IN ('yes', 'y', 'true', '1')) AS needs_flag,
          (COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'matching_providers_found',''), '[^0-9]', '', 'g'), '')::int, 0) > 0) AS found_flag,
          (COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'providers_connected',''), '[^0-9]', '', 'g'), '')::int, 0) > 0) AS connected_flag,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$' THEN (DATE '1899-12-30' + (campaign_date)::int)::date
             WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(campaign_date,1,10)::date
             WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(data->>'call_datetime_ist',1,10)::date
             ELSE NULL END AS call_date
      FROM public.call_rows WHERE program='seekers'
      AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state='all' OR region_code=_state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type=_campaign)
        AND (_campaign_type = 'all' OR campaign_type = _campaign_type)
    )
    SELECT jsonb_build_object(
      'calls',   COALESCE(ROUND(AVG(dur) FILTER (WHERE answered)),0)::int,
      'picked',  COALESCE(ROUND(AVG(dur) FILTER (WHERE answered)),0)::int,
      'engaged', COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged)),0)::int,
      'jobs',    COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND jobs_shown_flag)),0)::int,
      'intent',  COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND intent>=5)),0)::int,
      'apps',    COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND (submitted_flag OR blocked_flag))),0)::int,
      'profile',   COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND profile_flag)),0)::int,
      'needs',     COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND profile_flag AND needs_flag)),0)::int,
      'found',     COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND profile_flag AND needs_flag AND found_flag)),0)::int,
      'connected', COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND profile_flag AND needs_flag AND found_flag AND connected_flag)),0)::int
    ) INTO res FROM f;
  ELSIF _program='providers' THEN
    WITH base AS (
      SELECT
        lower(COALESCE(call_status,'')) AS cs,
        call_duration_seconds AS dur,
        lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
        lower(COALESCE(new_job_posted, data->'raw'->>'new_job_posted','')) AS njp,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$' THEN (DATE '1899-12-30' + (campaign_date)::int)::date
             WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(campaign_date,1,10)::date
             WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(data->>'call_datetime_ist',1,10)::date
             ELSE NULL END AS call_date
      FROM public.call_rows WHERE program='providers'
      AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT *, (cs LIKE 'answered%' OR cs='completed') AS ans FROM base
      WHERE (_state='all' OR region_code=_state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type=_campaign)
    )
    SELECT jsonb_build_object(
      'called',   COALESCE(ROUND(AVG(dur) FILTER (WHERE ans)),0)::int,
      'picked',   COALESCE(ROUND(AVG(dur) FILTER (WHERE ans)),0)::int,
      'engaged',  COALESCE(ROUND(AVG(dur) FILTER (WHERE ans AND COALESCE(dur,0)>30)),0)::int,
      'active',   COALESCE(ROUND(AVG(dur) FILTER (WHERE ans AND js='active')),0)::int,
      'new_jobs', COALESCE(ROUND(AVG(dur) FILTER (WHERE ans AND njp='yes')),0)::int
    ) INTO res FROM f;
  END IF;
  RETURN COALESCE(res, '{}'::jsonb);
END;
$_$;

CREATE OR REPLACE FUNCTION public.get_funnel_durations_np(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE res jsonb;
BEGIN
  IF _program = 'seekers' THEN
    WITH base AS (
      SELECT
        COALESCE(call_answered,false) AS answered,
        COALESCE(call_engaged,false) AS engaged,
        COALESCE(intent_score,0) AS intent,
        call_duration_seconds AS dur,
        ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag,
        (COALESCE(applied_to_job,false) OR (jsonb_typeof(data->'jobs_applied')='array' AND jsonb_array_length(data->'jobs_applied')>0)) AS submitted_flag,
        (jsonb_typeof(data->'jobs_failed_to_apply')='array' AND jsonb_array_length(data->'jobs_failed_to_apply')>0) AS blocked_flag,
          (lower(btrim(COALESCE(data->'raw'->>'profile_completeness',''))) NOT IN ('', '0', 'no', 'n', 'false')) AS profile_flag,
          (lower(btrim(COALESCE(data->'raw'->>'needs_mentioned',''))) IN ('yes', 'y', 'true', '1')) AS needs_flag,
          (COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'matching_providers_found',''), '[^0-9]', '', 'g'), '')::int, 0) > 0) AS found_flag,
          (COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'providers_connected',''), '[^0-9]', '', 'g'), '')::int, 0) > 0) AS connected_flag,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$' THEN (DATE '1899-12-30' + (campaign_date)::int)::date
             WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(campaign_date,1,10)::date
             WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(data->>'call_datetime_ist',1,10)::date
             ELSE NULL END AS call_date
      FROM public.call_rows_np WHERE program='seekers'
      AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state='all' OR region_code=_state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type=_campaign)
        AND (_campaign_type = 'all' OR campaign_type = _campaign_type)
    )
    SELECT jsonb_build_object(
      'calls',   COALESCE(ROUND(AVG(dur) FILTER (WHERE answered)),0)::int,
      'picked',  COALESCE(ROUND(AVG(dur) FILTER (WHERE answered)),0)::int,
      'engaged', COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged)),0)::int,
      'jobs',    COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND jobs_shown_flag)),0)::int,
      'intent',  COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND intent>=5)),0)::int,
      'apps',    COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND (submitted_flag OR blocked_flag))),0)::int,
      'profile',   COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND profile_flag)),0)::int,
      'needs',     COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND profile_flag AND needs_flag)),0)::int,
      'found',     COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND profile_flag AND needs_flag AND found_flag)),0)::int,
      'connected', COALESCE(ROUND(AVG(dur) FILTER (WHERE answered AND engaged AND profile_flag AND needs_flag AND found_flag AND connected_flag)),0)::int
    ) INTO res FROM f;
  ELSIF _program='providers' THEN
    WITH base AS (
      SELECT
        lower(COALESCE(call_status,'')) AS cs,
        call_duration_seconds AS dur,
        lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
        lower(COALESCE(new_job_posted, data->'raw'->>'new_job_posted','')) AS njp,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$' THEN (DATE '1899-12-30' + (campaign_date)::int)::date
             WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(campaign_date,1,10)::date
             WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}' THEN substring(data->>'call_datetime_ist',1,10)::date
             ELSE NULL END AS call_date
      FROM public.call_rows_np WHERE program='providers'
      AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT *, (cs LIKE 'answered%' OR cs='completed') AS ans FROM base
      WHERE (_state='all' OR region_code=_state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type=_campaign)
    )
    SELECT jsonb_build_object(
      'called',   COALESCE(ROUND(AVG(dur) FILTER (WHERE ans)),0)::int,
      'picked',   COALESCE(ROUND(AVG(dur) FILTER (WHERE ans)),0)::int,
      'engaged',  COALESCE(ROUND(AVG(dur) FILTER (WHERE ans AND COALESCE(dur,0)>30)),0)::int,
      'active',   COALESCE(ROUND(AVG(dur) FILTER (WHERE ans AND js='active')),0)::int,
      'new_jobs', COALESCE(ROUND(AVG(dur) FILTER (WHERE ans AND njp='yes')),0)::int
    ) INTO res FROM f;
  END IF;
  RETURN COALESCE(res, '{}'::jsonb);
END;
$_$;

CREATE OR REPLACE FUNCTION public.get_kkb_call_outcomes(_state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
  WITH base AS (
    SELECT
      COALESCE(NULLIF(btrim(call_outcome), ''), 'Unknown') AS outcome,
      COALESCE(campaign_type,'') AS campaign_type,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date, 1, 10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist', 1, 10)::date
        ELSE NULL END AS call_date
    FROM public.call_rows
    WHERE program = 'seekers' AND (_channel = 'all' OR channel = _channel)
  ),
  f AS (
    SELECT * FROM base
    WHERE (_state = 'all' OR region_code = _state)
      AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
      AND (_campaign IS NULL OR campaign_type = _campaign)
      AND (
        _campaign_type = 'all' OR campaign_type = _campaign_type
      )
  ),
  g AS (
    SELECT outcome, COUNT(*)::int AS n FROM f GROUP BY outcome ORDER BY n DESC
  )
  SELECT COALESCE(jsonb_agg(jsonb_build_object('outcome', outcome, 'n', n)), '[]'::jsonb) FROM g;
$_$;

CREATE OR REPLACE FUNCTION public.get_kkb_call_outcomes_np(_state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
  WITH base AS (
    SELECT
      COALESCE(NULLIF(btrim(call_outcome), ''), 'Unknown') AS outcome,
      COALESCE(campaign_type,'') AS campaign_type,
      COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
      CASE
        WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
          THEN (DATE '1899-12-30' + (campaign_date)::int)::date
        WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(campaign_date, 1, 10)::date
        WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
          THEN substring(data->>'call_datetime_ist', 1, 10)::date
        ELSE NULL END AS call_date
    FROM public.call_rows_np
    WHERE program = 'seekers' AND (_channel = 'all' OR channel = _channel)
  ),
  f AS (
    SELECT * FROM base
    WHERE (_state = 'all' OR region_code = _state)
      AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
      AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
      AND (_campaign IS NULL OR campaign_type = _campaign)
      AND (
        _campaign_type = 'all' OR campaign_type = _campaign_type
      )
  ),
  g AS (
    SELECT outcome, COUNT(*)::int AS n FROM f GROUP BY outcome ORDER BY n DESC
  )
  SELECT COALESCE(jsonb_agg(jsonb_build_object('outcome', outcome, 'n', n)), '[]'::jsonb) FROM g;
$_$;

CREATE OR REPLACE FUNCTION public.get_kkb_drop_analysis(_state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
WITH base AS (
  SELECT
    COALESCE(call_answered,false)   AS answered,
    COALESCE(applied_to_job,false)  AS converted,
    COALESCE(drop_reason,'')        AS drop_reason,
    phases_reached                  AS stage_label,
    COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
    CASE
      WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
        THEN (DATE '1899-12-30' + (campaign_date)::int)::date
      WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(campaign_date,1,10)::date
      WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(data->>'call_datetime_ist',1,10)::date
      ELSE NULL
    END AS call_date,
    COALESCE(campaign_type,'') AS ctype
  FROM public.call_rows
  WHERE program = 'seekers'
    AND (_channel = 'all' OR channel = _channel)
),
scoped AS (
  SELECT * FROM base
  WHERE answered = true
    AND NOT converted
    AND (_state = 'all' OR region_code = _state)
    AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
    AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
    AND (_campaign IS NULL OR ctype = _campaign)
    AND (_campaign_type = 'all' OR ctype = _campaign_type)
),
flagged AS (
  SELECT COUNT(*)::int AS n FROM scoped
  WHERE drop_reason ILIKE '%suicidal%' OR drop_reason ILIKE '%distress%'
),
drops AS (
  SELECT
    COALESCE(NULLIF(TRIM(drop_reason),''), 'Not captured') AS bucket,
    CASE
      WHEN stage_label = 'Connect to provider'                then 'connect'
      WHEN stage_label = 'Match provider'                     then 'match'
      WHEN stage_label = 'Summary & profile update'           then 'summary'
      WHEN stage_label = 'Options delivery & decision making' then 'options'
      WHEN stage_label = 'Needs & challenges evaluation'      then 'needs'
      WHEN stage_label = 'Identify disability type'           then 'disability'
      WHEN stage_label = 'Profile completion & verification'  then 'completion'
      WHEN stage_label = 'Profile fetch & name confirmation'  then 'fetch'
      ELSE 'other'
    END AS stage
  FROM scoped
  WHERE stage_label IS NOT NULL AND btrim(stage_label) <> ''
    AND NOT (drop_reason ILIKE '%suicidal%' OR drop_reason ILIKE '%distress%')
),
by_stage AS (
  SELECT bucket, stage, COUNT(*)::int AS cnt FROM drops GROUP BY 1,2
),
bucket_totals AS (
  SELECT bucket, SUM(cnt)::int AS total FROM by_stage GROUP BY 1
),
stage_map AS (
  SELECT bucket, jsonb_object_agg(stage, cnt) AS by_stage_obj FROM by_stage GROUP BY 1
),
buckets_json AS (
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object(
      'bucket', bt.bucket,
      'byStage', jsonb_build_object(
        'fetch',      COALESCE((sm.by_stage_obj->>'fetch')::int, 0),
        'completion', COALESCE((sm.by_stage_obj->>'completion')::int, 0),
        'disability', COALESCE((sm.by_stage_obj->>'disability')::int, 0),
        'needs',      COALESCE((sm.by_stage_obj->>'needs')::int, 0),
        'options',    COALESCE((sm.by_stage_obj->>'options')::int, 0),
        'summary',    COALESCE((sm.by_stage_obj->>'summary')::int, 0),
        'match',      COALESCE((sm.by_stage_obj->>'match')::int, 0),
        'connect',    COALESCE((sm.by_stage_obj->>'connect')::int, 0),
        'other',      COALESCE((sm.by_stage_obj->>'other')::int, 0)
      ),
      'total', bt.total,
      'raw',   jsonb_build_array(jsonb_build_object('reason', bt.bucket, 'count', bt.total))
    ) ORDER BY bt.total DESC
  ), '[]'::jsonb) AS arr
  FROM bucket_totals bt
  LEFT JOIN stage_map sm USING (bucket)
),
max_cell AS ( SELECT COALESCE(MAX(cnt), 0)::int AS m FROM by_stage ),
grand     AS ( SELECT COALESCE(SUM(total),0)::int AS g FROM bucket_totals )
SELECT jsonb_build_object(
  'stages', jsonb_build_array(
    jsonb_build_object('key','fetch',      'label','Profile fetch'),
    jsonb_build_object('key','completion', 'label','Profile completion'),
    jsonb_build_object('key','disability', 'label','Disability type'),
    jsonb_build_object('key','needs',      'label','Needs & challenges'),
    jsonb_build_object('key','options',    'label','Options delivery'),
    jsonb_build_object('key','summary',    'label','Summary & update'),
    jsonb_build_object('key','match',      'label','Match provider'),
    jsonb_build_object('key','connect',    'label','Connect to provider'),
    jsonb_build_object('key','other',      'label','Unclassified')
  ),
  'buckets',             (SELECT arr FROM buckets_json),
  'maxCell',             (SELECT m FROM max_cell),
  'grandTotal',          (SELECT g FROM grand),
  'safeguardingFlagged', (SELECT n FROM flagged)
);
$_$;

CREATE OR REPLACE FUNCTION public.get_kkb_drop_analysis_np(_state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
WITH base AS (
  SELECT
    COALESCE(call_answered,false)   AS answered,
    COALESCE(applied_to_job,false)  AS converted,
    COALESCE(drop_reason,'')        AS drop_reason,
    phases_reached                  AS stage_label,
    COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
    CASE
      WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
        THEN (DATE '1899-12-30' + (campaign_date)::int)::date
      WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(campaign_date,1,10)::date
      WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(data->>'call_datetime_ist',1,10)::date
      ELSE NULL
    END AS call_date,
    COALESCE(campaign_type,'') AS ctype
  FROM public.call_rows_np
  WHERE program = 'seekers'
    AND (_channel = 'all' OR channel = _channel)
),
scoped AS (
  SELECT * FROM base
  WHERE answered = true
    AND NOT converted
    AND (_state = 'all' OR region_code = _state)
    AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
    AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
    AND (_campaign IS NULL OR ctype = _campaign)
    AND (_campaign_type = 'all' OR ctype = _campaign_type)
),
flagged AS (
  SELECT COUNT(*)::int AS n FROM scoped
  WHERE drop_reason ILIKE '%suicidal%' OR drop_reason ILIKE '%distress%'
),
drops AS (
  SELECT
    COALESCE(NULLIF(TRIM(drop_reason),''), 'Not captured') AS bucket,
    CASE
      WHEN stage_label = 'Connect to provider'                then 'connect'
      WHEN stage_label = 'Match provider'                     then 'match'
      WHEN stage_label = 'Summary & profile update'           then 'summary'
      WHEN stage_label = 'Options delivery & decision making' then 'options'
      WHEN stage_label = 'Needs & challenges evaluation'      then 'needs'
      WHEN stage_label = 'Identify disability type'           then 'disability'
      WHEN stage_label = 'Profile completion & verification'  then 'completion'
      WHEN stage_label = 'Profile fetch & name confirmation'  then 'fetch'
      ELSE 'other'
    END AS stage
  FROM scoped
  WHERE stage_label IS NOT NULL AND btrim(stage_label) <> ''
    AND NOT (drop_reason ILIKE '%suicidal%' OR drop_reason ILIKE '%distress%')
),
by_stage AS (
  SELECT bucket, stage, COUNT(*)::int AS cnt FROM drops GROUP BY 1,2
),
bucket_totals AS (
  SELECT bucket, SUM(cnt)::int AS total FROM by_stage GROUP BY 1
),
stage_map AS (
  SELECT bucket, jsonb_object_agg(stage, cnt) AS by_stage_obj FROM by_stage GROUP BY 1
),
buckets_json AS (
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object(
      'bucket', bt.bucket,
      'byStage', jsonb_build_object(
        'fetch',      COALESCE((sm.by_stage_obj->>'fetch')::int, 0),
        'completion', COALESCE((sm.by_stage_obj->>'completion')::int, 0),
        'disability', COALESCE((sm.by_stage_obj->>'disability')::int, 0),
        'needs',      COALESCE((sm.by_stage_obj->>'needs')::int, 0),
        'options',    COALESCE((sm.by_stage_obj->>'options')::int, 0),
        'summary',    COALESCE((sm.by_stage_obj->>'summary')::int, 0),
        'match',      COALESCE((sm.by_stage_obj->>'match')::int, 0),
        'connect',    COALESCE((sm.by_stage_obj->>'connect')::int, 0),
        'other',      COALESCE((sm.by_stage_obj->>'other')::int, 0)
      ),
      'total', bt.total,
      'raw',   jsonb_build_array(jsonb_build_object('reason', bt.bucket, 'count', bt.total))
    ) ORDER BY bt.total DESC
  ), '[]'::jsonb) AS arr
  FROM bucket_totals bt
  LEFT JOIN stage_map sm USING (bucket)
),
max_cell AS ( SELECT COALESCE(MAX(cnt), 0)::int AS m FROM by_stage ),
grand     AS ( SELECT COALESCE(SUM(total),0)::int AS g FROM bucket_totals )
SELECT jsonb_build_object(
  'stages', jsonb_build_array(
    jsonb_build_object('key','fetch',      'label','Profile fetch'),
    jsonb_build_object('key','completion', 'label','Profile completion'),
    jsonb_build_object('key','disability', 'label','Disability type'),
    jsonb_build_object('key','needs',      'label','Needs & challenges'),
    jsonb_build_object('key','options',    'label','Options delivery'),
    jsonb_build_object('key','summary',    'label','Summary & update'),
    jsonb_build_object('key','match',      'label','Match provider'),
    jsonb_build_object('key','connect',    'label','Connect to provider'),
    jsonb_build_object('key','other',      'label','Unclassified')
  ),
  'buckets',             (SELECT arr FROM buckets_json),
  'maxCell',             (SELECT m FROM max_cell),
  'grandTotal',          (SELECT g FROM grand),
  'safeguardingFlagged', (SELECT n FROM flagged)
);
$_$;

CREATE OR REPLACE FUNCTION public.get_program_aggregate_payload(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
WITH aggregate_result AS (
  SELECT public.get_program_aggregates(_program, _state, _date_from, _date_to, _campaign_type, _campaign, _channel) AS aggregates
),
groups AS (
  SELECT public.get_program_metric_groups(_program, _state, _date_from, _date_to, _campaign_type, _campaign, _channel) AS metric_groups
),
metrics AS (
  SELECT public.get_program_metrics_raw(_program, _state, _date_from, _date_to, _campaign_type, _campaign, _channel) AS metrics_raw
),
state_row AS (
  SELECT last_synced_at, row_count, status FROM public.program_sync_state WHERE program = _program
),
connection_count AS (
  SELECT COUNT(*)::int AS count FROM public.sheet_connections WHERE program = _program AND enabled = true
)
SELECT jsonb_build_object(
  'connectionCount', COALESCE((SELECT count FROM connection_count), 0),
  'lastSyncedAt', (SELECT last_synced_at FROM state_row),
  'syncStatus', COALESCE((SELECT status FROM state_row), 'idle'),
  'stateRowCount', COALESCE((SELECT row_count FROM state_row), 0),
  'aggregates', (SELECT aggregates FROM aggregate_result),
  'metricGroups', (SELECT metric_groups FROM groups),
  'metrics', (SELECT metrics_raw FROM metrics)
);
$$;

CREATE OR REPLACE FUNCTION public.get_program_aggregate_payload_np(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
WITH aggregate_result AS (
  SELECT public.get_program_aggregates_np(_program, _state, _date_from, _date_to, _campaign_type, _campaign, _channel) AS aggregates
),
groups AS (
  SELECT public.get_program_metric_groups_np(_program, _state, _date_from, _date_to, _campaign_type, _campaign, _channel) AS metric_groups
),
metrics AS (
  SELECT public.get_program_metrics_raw_np(_program, _state, _date_from, _date_to, _campaign_type, _campaign, _channel) AS metrics_raw
),
state_row AS (
  SELECT last_synced_at, row_count, status FROM public.program_sync_state WHERE program = _program
),
connection_count AS (
  SELECT COUNT(*)::int AS count FROM public.sheet_connections WHERE program = _program AND enabled = true
)
SELECT jsonb_build_object(
  'connectionCount', COALESCE((SELECT count FROM connection_count), 0),
  'lastSyncedAt', (SELECT last_synced_at FROM state_row),
  'syncStatus', COALESCE((SELECT status FROM state_row), 'idle'),
  'stateRowCount', COALESCE((SELECT row_count FROM state_row), 0),
  'aggregates', (SELECT aggregates FROM aggregate_result),
  'metricGroups', (SELECT metric_groups FROM groups),
  'metrics', (SELECT metrics_raw FROM metrics)
);
$$;

CREATE OR REPLACE FUNCTION public.get_program_aggregates(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
WITH base AS (
  SELECT
    campaign_day,
    COALESCE(campaign_date, '') AS campaign_date,
    COALESCE(campaign_type, '') AS campaign_type,
    COALESCE(language, '') AS language,
    COALESCE(intent_score, 0) AS intent_score,
    COALESCE(call_answered, false) AS call_answered,
    COALESCE(call_engaged, false) AS call_engaged,
    COALESCE(applied_to_job, false) AS applied_to_job,
    COALESCE(call_status, '') AS call_status,
    COALESCE(job_status, '') AS job_status,
    COALESCE(new_job_posted, '') AS new_job_posted,
    COALESCE(talent_insights_shown, '') AS talent_insights_shown,
    COALESCE(phases_reached, '') AS phases_reached,
    COALESCE(drop_reason, '') AS drop_reason,
    COALESCE(call_outcome, '') AS call_outcome,
    COALESCE(city_campaign, '') AS city_campaign,
    lower(COALESCE(call_status, '')) AS call_status_l,
    lower(COALESCE(job_status, '')) AS job_status_l,
    lower(COALESCE(new_job_posted, '')) AS new_job_posted_l,
    lower(COALESCE(talent_insights_shown, '')) AS talent_insights_shown_l,
    COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
    CASE
      WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
        THEN (DATE '1899-12-30' + (campaign_date)::int)::date
      WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(campaign_date, 1, 10)::date
      WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(data->>'call_datetime_ist', 1, 10)::date
      ELSE NULL
    END AS call_date
  FROM public.call_rows
  WHERE program = _program
    AND (_channel = 'all' OR channel = _channel)
),
filtered AS (
  SELECT * FROM base
  WHERE (_state = 'all' OR region_code = _state)
    AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
    AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
    AND (_campaign IS NULL OR campaign_type = _campaign)
    AND (
      _campaign_type = 'all'
      OR campaign_type = _campaign_type
    )
),
summary AS (
  SELECT
    COUNT(*)::int AS total_rows,
    COUNT(*) FILTER (WHERE call_answered)::int AS kkb_answered,
    COUNT(*) FILTER (WHERE call_engaged)::int AS engaged,
    COUNT(*) FILTER (WHERE applied_to_job)::int AS applied,
    COUNT(*) FILTER (WHERE intent_score >= 5)::int AS high_intent,
    COUNT(*) FILTER (WHERE call_status_l LIKE 'answered%' OR call_status_l = 'completed')::int AS dkb_answered,
    COUNT(*) FILTER (WHERE job_status_l IN ('active', 'closed'))::int AS jobs_verified,
    COUNT(*) FILTER (WHERE new_job_posted_l = 'yes')::int AS new_jobs_posted,
    COUNT(*) FILTER (WHERE talent_insights_shown_l = 'yes')::int AS talent_insights
  FROM filtered
),
per_day AS (
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object(
      'day', day, 'date', date_str, 'type', campaign_type, 'language', language,
      'rows', rows, 'answered', answered, 'engaged', engaged, 'converted', converted,
      'new_jobs', new_jobs, 'answered_pct', answered_pct, 'high_intent', high_intent
    ) ORDER BY sort_date NULLS LAST, date_str
  ), '[]'::jsonb) AS items
  FROM (
    SELECT
      MIN(campaign_day) AS day,
      to_char(call_date, 'YYYY-MM-DD') AS date_str,
      call_date AS sort_date,
      MIN(campaign_type) AS campaign_type,
      MIN(language) AS language,
      COUNT(*)::int AS rows,
      CASE WHEN _program = 'providers' THEN COUNT(*) FILTER (WHERE call_status_l LIKE 'answered%' OR call_status_l = 'completed')::int
        ELSE COUNT(*) FILTER (WHERE call_answered)::int END AS answered,
      COUNT(*) FILTER (WHERE call_engaged)::int AS engaged,
      CASE WHEN _program = 'providers' THEN COUNT(*) FILTER (WHERE new_job_posted_l = 'yes')::int
        ELSE COUNT(*) FILTER (WHERE applied_to_job)::int END AS converted,
      COUNT(*) FILTER (WHERE new_job_posted_l = 'yes')::int AS new_jobs,
      CASE WHEN COUNT(*) = 0 THEN 0 ELSE ROUND(
        100.0 * CASE WHEN _program = 'providers' THEN COUNT(*) FILTER (WHERE call_status_l LIKE 'answered%' OR call_status_l = 'completed')
          ELSE COUNT(*) FILTER (WHERE call_answered) END / COUNT(*))::int END AS answered_pct,
      COUNT(*) FILTER (WHERE intent_score >= 5)::int AS high_intent
    FROM filtered
    WHERE call_date IS NOT NULL
    GROUP BY call_date
  ) d
),
drops AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('reason', reason, 'count', count) ORDER BY count DESC, reason), '[]'::jsonb) AS items
  FROM (
    SELECT COALESCE(NULLIF(drop_reason, ''), 'Unknown') AS reason, COUNT(*)::int AS count
    FROM filtered WHERE _program <> 'providers' AND COALESCE(drop_reason, '') <> ''
    GROUP BY 1
  ) x
),
intent_buckets AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('score', b.bucket::text, 'count', COALESCE(c.count, 0)) ORDER BY b.bucket), '[]'::jsonb) AS items
  FROM generate_series(0, 10) AS b(bucket)
  LEFT JOIN (
    SELECT GREATEST(0, LEAST(10, FLOOR(intent_score)::int)) AS bucket, COUNT(*)::int AS count
    FROM filtered GROUP BY 1
  ) c USING (bucket)
),
regions AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('region', region, 'count', count) ORDER BY region), '[]'::jsonb) AS items
  FROM (
    SELECT COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region,
      COUNT(*)::int AS count
    FROM filtered WHERE _program <> 'providers' GROUP BY 1
  ) x
),
phases AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('phase', phase, 'count', count) ORDER BY phase), '[]'::jsonb) AS items
  FROM (
    SELECT 'Phase ' || phases_reached AS phase, COUNT(*)::int AS count
    FROM filtered WHERE _program = 'providers' AND phases_reached ~ '^[1-4]$' GROUP BY phases_reached
  ) x
),
job_statuses AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('status', status, 'count', count) ORDER BY count DESC), '[]'::jsonb) AS items
  FROM (
    SELECT COALESCE(NULLIF(job_status, ''), 'Unknown') AS status, COUNT(*)::int AS count
    FROM filtered WHERE _program = 'providers' GROUP BY 1
  ) x
),
outcomes AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('outcome', outcome, 'count', count) ORDER BY count DESC), '[]'::jsonb) AS items
  FROM (
    SELECT COALESCE(NULLIF(call_outcome, ''), 'Unknown') AS outcome, COUNT(*)::int AS count
    FROM filtered WHERE _program = 'providers' GROUP BY 1
  ) x
),
drop_class AS (
  SELECT
    region_code AS region,
    btrim(phases_reached) AS stage_label,
    btrim(drop_reason) AS dr
  FROM filtered
  WHERE _program = 'seekers'
    AND phases_reached IS NOT NULL AND btrim(phases_reached) <> ''
    AND drop_reason NOT ILIKE '%suicidal%'
    AND drop_reason NOT ILIKE '%distress%'
),
drop_tagged AS (
  -- Purple Dots: drop_reason is already 8 canonical values, so the reason IS
  -- the bucket and the stage comes from the normalised bot phase. The job-era
  -- keyword classifier that used to live here has been removed.
  SELECT region, stage_label AS stage,
         coalesce(nullif(dr,''), 'Not captured') AS reason
  FROM drop_class
),
drop_agg AS (
  SELECT stage, reason,
    COALESCE(jsonb_object_agg(region, cnt) FILTER (WHERE region IS NOT NULL), '{}'::jsonb) AS by_region,
    SUM(cnt)::int AS total
  FROM (
    SELECT stage, reason, region, COUNT(*)::int AS cnt
    FROM drop_tagged
    GROUP BY stage, reason, region
  ) s
  GROUP BY stage, reason
),
drop_analysis AS (
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object('stage', stage, 'reason', reason, 'byRegion', by_region, 'total', total)
    ORDER BY
      CASE stage
        WHEN 'Profile fetch & name confirmation'  THEN 1
        WHEN 'Profile completion & verification'  THEN 2
        WHEN 'Identify disability type'           THEN 3
        WHEN 'Needs & challenges evaluation'      THEN 4
        WHEN 'Options delivery & decision making' THEN 5
        WHEN 'Summary & profile update'           THEN 6
        WHEN 'Match provider'                     THEN 7
        WHEN 'Connect to provider'                THEN 8
        ELSE 9
      END,
      total DESC, reason
  ), '[]'::jsonb) AS items
  FROM drop_agg
)
SELECT jsonb_build_object(
  'kpis', jsonb_build_object(
    'total_rows', summary.total_rows,
    'kkb_answered', summary.kkb_answered, 'engaged', summary.engaged, 'applied', summary.applied,
    'high_intent', summary.high_intent, 'dkb_answered', summary.dkb_answered,
    'jobs_verified', summary.jobs_verified, 'new_jobs_posted', summary.new_jobs_posted,
    'talent_insights', summary.talent_insights
  ),
  'perDay', per_day.items,
  'drops', drops.items,
  'intents', intent_buckets.items,
  'regions', regions.items,
  'phases', phases.items,
  'jobStatus', job_statuses.items,
  'outcomes', outcomes.items,
  'dkbIntents', intent_buckets.items,
  'dropAnalysis', CASE WHEN _program = 'providers' THEN '[]'::jsonb ELSE drop_analysis.items END
)
FROM summary, per_day, drops, intent_buckets, regions, phases, job_statuses, outcomes, drop_analysis;
$_$;

CREATE OR REPLACE FUNCTION public.get_program_aggregates_np(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
WITH base AS (
  SELECT
    campaign_day,
    COALESCE(campaign_date, '') AS campaign_date,
    COALESCE(campaign_type, '') AS campaign_type,
    COALESCE(language, '') AS language,
    COALESCE(intent_score, 0) AS intent_score,
    COALESCE(call_answered, false) AS call_answered,
    COALESCE(call_engaged, false) AS call_engaged,
    COALESCE(applied_to_job, false) AS applied_to_job,
    COALESCE(call_status, '') AS call_status,
    COALESCE(job_status, '') AS job_status,
    COALESCE(new_job_posted, '') AS new_job_posted,
    COALESCE(talent_insights_shown, '') AS talent_insights_shown,
    COALESCE(phases_reached, '') AS phases_reached,
    COALESCE(drop_reason, '') AS drop_reason,
    COALESCE(call_outcome, '') AS call_outcome,
    COALESCE(city_campaign, '') AS city_campaign,
    lower(COALESCE(call_status, '')) AS call_status_l,
    lower(COALESCE(job_status, '')) AS job_status_l,
    lower(COALESCE(new_job_posted, '')) AS new_job_posted_l,
    lower(COALESCE(talent_insights_shown, '')) AS talent_insights_shown_l,
    COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
    CASE
      WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
        THEN (DATE '1899-12-30' + (campaign_date)::int)::date
      WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(campaign_date, 1, 10)::date
      WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
        THEN substring(data->>'call_datetime_ist', 1, 10)::date
      ELSE NULL
    END AS call_date
  FROM public.call_rows_np
  WHERE program = _program
    AND (_channel = 'all' OR channel = _channel)
),
filtered AS (
  SELECT * FROM base
  WHERE (_state = 'all' OR region_code = _state)
    AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
    AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
    AND (_campaign IS NULL OR campaign_type = _campaign)
    AND (
      _campaign_type = 'all'
      OR campaign_type = _campaign_type
    )
),
summary AS (
  SELECT
    COUNT(*)::int AS total_rows,
    COUNT(*) FILTER (WHERE call_answered)::int AS kkb_answered,
    COUNT(*) FILTER (WHERE call_engaged)::int AS engaged,
    COUNT(*) FILTER (WHERE applied_to_job)::int AS applied,
    COUNT(*) FILTER (WHERE intent_score >= 5)::int AS high_intent,
    COUNT(*) FILTER (WHERE call_status_l LIKE 'answered%' OR call_status_l = 'completed')::int AS dkb_answered,
    COUNT(*) FILTER (WHERE job_status_l IN ('active', 'closed'))::int AS jobs_verified,
    COUNT(*) FILTER (WHERE new_job_posted_l = 'yes')::int AS new_jobs_posted,
    COUNT(*) FILTER (WHERE talent_insights_shown_l = 'yes')::int AS talent_insights
  FROM filtered
),
per_day AS (
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object(
      'day', day, 'date', date_str, 'type', campaign_type, 'language', language,
      'rows', rows, 'answered', answered, 'engaged', engaged, 'converted', converted,
      'new_jobs', new_jobs, 'answered_pct', answered_pct, 'high_intent', high_intent
    ) ORDER BY sort_date NULLS LAST, date_str
  ), '[]'::jsonb) AS items
  FROM (
    SELECT
      MIN(campaign_day) AS day,
      to_char(call_date, 'YYYY-MM-DD') AS date_str,
      call_date AS sort_date,
      MIN(campaign_type) AS campaign_type,
      MIN(language) AS language,
      COUNT(*)::int AS rows,
      CASE WHEN _program = 'providers' THEN COUNT(*) FILTER (WHERE call_status_l LIKE 'answered%' OR call_status_l = 'completed')::int
        ELSE COUNT(*) FILTER (WHERE call_answered)::int END AS answered,
      COUNT(*) FILTER (WHERE call_engaged)::int AS engaged,
      CASE WHEN _program = 'providers' THEN COUNT(*) FILTER (WHERE new_job_posted_l = 'yes')::int
        ELSE COUNT(*) FILTER (WHERE applied_to_job)::int END AS converted,
      COUNT(*) FILTER (WHERE new_job_posted_l = 'yes')::int AS new_jobs,
      CASE WHEN COUNT(*) = 0 THEN 0 ELSE ROUND(
        100.0 * CASE WHEN _program = 'providers' THEN COUNT(*) FILTER (WHERE call_status_l LIKE 'answered%' OR call_status_l = 'completed')
          ELSE COUNT(*) FILTER (WHERE call_answered) END / COUNT(*))::int END AS answered_pct,
      COUNT(*) FILTER (WHERE intent_score >= 5)::int AS high_intent
    FROM filtered
    WHERE call_date IS NOT NULL
    GROUP BY call_date
  ) d
),
drops AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('reason', reason, 'count', count) ORDER BY count DESC, reason), '[]'::jsonb) AS items
  FROM (
    SELECT COALESCE(NULLIF(drop_reason, ''), 'Unknown') AS reason, COUNT(*)::int AS count
    FROM filtered WHERE _program <> 'providers' AND COALESCE(drop_reason, '') <> ''
    GROUP BY 1
  ) x
),
intent_buckets AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('score', b.bucket::text, 'count', COALESCE(c.count, 0)) ORDER BY b.bucket), '[]'::jsonb) AS items
  FROM generate_series(0, 10) AS b(bucket)
  LEFT JOIN (
    SELECT GREATEST(0, LEAST(10, FLOOR(intent_score)::int)) AS bucket, COUNT(*)::int AS count
    FROM filtered GROUP BY 1
  ) c USING (bucket)
),
regions AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('region', region, 'count', count) ORDER BY region), '[]'::jsonb) AS items
  FROM (
    SELECT COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region,
      COUNT(*)::int AS count
    FROM filtered WHERE _program <> 'providers' GROUP BY 1
  ) x
),
phases AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('phase', phase, 'count', count) ORDER BY phase), '[]'::jsonb) AS items
  FROM (
    SELECT 'Phase ' || phases_reached AS phase, COUNT(*)::int AS count
    FROM filtered WHERE _program = 'providers' AND phases_reached ~ '^[1-4]$' GROUP BY phases_reached
  ) x
),
job_statuses AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('status', status, 'count', count) ORDER BY count DESC), '[]'::jsonb) AS items
  FROM (
    SELECT COALESCE(NULLIF(job_status, ''), 'Unknown') AS status, COUNT(*)::int AS count
    FROM filtered WHERE _program = 'providers' GROUP BY 1
  ) x
),
outcomes AS (
  SELECT COALESCE(jsonb_agg(jsonb_build_object('outcome', outcome, 'count', count) ORDER BY count DESC), '[]'::jsonb) AS items
  FROM (
    SELECT COALESCE(NULLIF(call_outcome, ''), 'Unknown') AS outcome, COUNT(*)::int AS count
    FROM filtered WHERE _program = 'providers' GROUP BY 1
  ) x
),
drop_class AS (
  SELECT
    region_code AS region,
    btrim(phases_reached) AS stage_label,
    btrim(drop_reason) AS dr
  FROM filtered
  WHERE _program = 'seekers'
    AND phases_reached IS NOT NULL AND btrim(phases_reached) <> ''
    AND drop_reason NOT ILIKE '%suicidal%'
    AND drop_reason NOT ILIKE '%distress%'
),
drop_tagged AS (
  -- Purple Dots: drop_reason is already 8 canonical values, so the reason IS
  -- the bucket and the stage comes from the normalised bot phase. The job-era
  -- keyword classifier that used to live here has been removed.
  SELECT region, stage_label AS stage,
         coalesce(nullif(dr,''), 'Not captured') AS reason
  FROM drop_class
),
drop_agg AS (
  SELECT stage, reason,
    COALESCE(jsonb_object_agg(region, cnt) FILTER (WHERE region IS NOT NULL), '{}'::jsonb) AS by_region,
    SUM(cnt)::int AS total
  FROM (
    SELECT stage, reason, region, COUNT(*)::int AS cnt
    FROM drop_tagged
    GROUP BY stage, reason, region
  ) s
  GROUP BY stage, reason
),
drop_analysis AS (
  SELECT COALESCE(jsonb_agg(
    jsonb_build_object('stage', stage, 'reason', reason, 'byRegion', by_region, 'total', total)
    ORDER BY
      CASE stage
        WHEN 'Profile fetch & name confirmation'  THEN 1
        WHEN 'Profile completion & verification'  THEN 2
        WHEN 'Identify disability type'           THEN 3
        WHEN 'Needs & challenges evaluation'      THEN 4
        WHEN 'Options delivery & decision making' THEN 5
        WHEN 'Summary & profile update'           THEN 6
        WHEN 'Match provider'                     THEN 7
        WHEN 'Connect to provider'                THEN 8
        ELSE 9
      END,
      total DESC, reason
  ), '[]'::jsonb) AS items
  FROM drop_agg
)
SELECT jsonb_build_object(
  'kpis', jsonb_build_object(
    'total_rows', summary.total_rows,
    'kkb_answered', summary.kkb_answered, 'engaged', summary.engaged, 'applied', summary.applied,
    'high_intent', summary.high_intent, 'dkb_answered', summary.dkb_answered,
    'jobs_verified', summary.jobs_verified, 'new_jobs_posted', summary.new_jobs_posted,
    'talent_insights', summary.talent_insights
  ),
  'perDay', per_day.items,
  'drops', drops.items,
  'intents', intent_buckets.items,
  'regions', regions.items,
  'phases', phases.items,
  'jobStatus', job_statuses.items,
  'outcomes', outcomes.items,
  'dkbIntents', intent_buckets.items,
  'dropAnalysis', CASE WHEN _program = 'providers' THEN '[]'::jsonb ELSE drop_analysis.items END
)
FROM summary, per_day, drops, intent_buckets, regions, phases, job_statuses, outcomes, drop_analysis;
$_$;

CREATE OR REPLACE FUNCTION public.get_program_filter_options(_program text) RETURNS json
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'public', 'extensions'
    AS $$
  select json_build_object(
    'cities', coalesce((
      select json_agg(v order by v)
      from (select distinct nullif(btrim(city_campaign), '') as v
            from public.call_rows where program = _program) s
      where v is not null
    ), '[]'::json),
    'campaignTypes', coalesce((
      select json_agg(v order by v)
      from (select distinct nullif(btrim(campaign_type), '') as v
            from public.call_rows where program = _program) s
      where v is not null
    ), '[]'::json),
    'channels', coalesce((
      select json_agg(v order by v)
      from (select distinct nullif(btrim(channel), '') as v
            from public.call_rows where program = _program) s
      where v is not null
    ), '[]'::json)
  );
$$;

CREATE OR REPLACE FUNCTION public.get_program_metric_groups(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
  total_calls int := 0;
  answered_calls int := 0;
  unanswered_calls int := 0;
  productive_calls int := 0;
  avg_dur numeric := 0;
  seekers int := 0;
  answered_seekers int := 0;
  applied_seekers int := 0;
  failed_seekers int := 0;
  did_not_apply int := 0;
  total_applications numeric := 0;
  tried int := 0;
  application_rate numeric := 0;
  applied_pct numeric := 0;
  failed_pct numeric := 0;
  pickup numeric := 0;
  productive_pct numeric := 0;
  total_openings int := 0;
  active_openings int := 0;
  closed_openings int := 0;
  unresolved_openings int := 0;
  new_openings int := 0;
  active_open_pct numeric := 0;
  closed_open_pct numeric := 0;
  unresolved_open_pct numeric := 0;
  companies_called int := 0;
  jobs_active int := 0;
  jobs_closed int := 0;
  companies_unresolved int := 0;
  new_jobs_discussed int := 0;
  jobs_active_pct numeric := 0;
  jobs_closed_pct numeric := 0;
  companies_unresolved_pct numeric := 0;
  result jsonb;
BEGIN
  IF _program = 'seekers' THEN
    WITH base AS (
      SELECT
        phone, call_answered, call_duration_seconds, applied_to_job,
        tried_to_apply, applications_count,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE
          WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
            THEN substring(data->>'call_datetime_ist', 1, 10)::date
          WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
            THEN substring(campaign_date, 1, 10)::date
          ELSE NULL END AS call_date
      FROM public.call_rows WHERE program = 'seekers' AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state = 'all' OR region_code = _state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type = _campaign)
        AND (
          _campaign_type = 'all' OR campaign_type = _campaign_type
        )
    )
    SELECT
      COUNT(*)::int,
      COUNT(*) FILTER (WHERE call_answered)::int,
      COUNT(*) FILTER (WHERE call_answered AND COALESCE(call_duration_seconds,0) > 30)::int,
      COALESCE(AVG(call_duration_seconds) FILTER (WHERE call_answered), 0),
      COUNT(DISTINCT phone) FILTER (WHERE phone IS NOT NULL AND phone <> '')::int,
      COUNT(DISTINCT phone) FILTER (WHERE call_answered AND phone IS NOT NULL AND phone <> '')::int,
      COUNT(DISTINCT phone) FILTER (WHERE applied_to_job AND phone IS NOT NULL AND phone <> '')::int,
      COUNT(DISTINCT phone) FILTER (WHERE COALESCE(tried_to_apply,false) AND NOT COALESCE(applied_to_job,false) AND phone IS NOT NULL AND phone <> '')::int,
      COALESCE(SUM(applications_count), 0)
    INTO total_calls, answered_calls, productive_calls, avg_dur,
         seekers, answered_seekers, applied_seekers, failed_seekers, total_applications
    FROM f;

    unanswered_calls := total_calls - answered_calls;
    pickup := CASE WHEN total_calls = 0 THEN 0 ELSE 100.0 * answered_calls / total_calls END;
    productive_pct := CASE WHEN total_calls = 0 THEN 0 ELSE 100.0 * productive_calls / total_calls END;
    did_not_apply := GREATEST(answered_seekers - applied_seekers, 0);
    tried := applied_seekers + failed_seekers;
    applied_pct := CASE WHEN answered_seekers = 0 THEN 0 ELSE 100.0 * applied_seekers / answered_seekers END;
    failed_pct := CASE WHEN tried = 0 THEN 0 ELSE 100.0 * failed_seekers / tried END;
    application_rate := applied_pct;

    result := jsonb_build_array(
      jsonb_build_object(
        'key','outcome','title','Outcome metrics',
        'subtitle','Per unique seeker (deduped by phone)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','seekers_called','label','Seekers Called','value', to_char(seekers,'FM999,999,999'),'sub','Unique phone numbers','accent','blue'),
          jsonb_build_object('key','applied','label','Connected to a Provider','value', to_char(applied_seekers,'FM999,999,999'),'sub', to_char(applied_pct,'FM999990.0') || '% of seekers who answered','accent','green'),
          jsonb_build_object('key','failed_apply','label','Failed Connection Attempts','value', to_char(failed_seekers,'FM999,999,999'),'sub', to_char(failed_pct,'FM999990.0') || '% of seekers who attempted','accent','amber'),
          jsonb_build_object('key','did_not_apply','label','Not Connected','value', to_char(did_not_apply,'FM999,999,999'),'sub','Answered but no provider connection','accent','red'),
          jsonb_build_object('key','total_applications','label','Total Connections','value', to_char(ROUND(total_applications),'FM999,999,999'),'sub','Across all calls','accent','blue'),
          jsonb_build_object('key','application_rate','label','Connection Rate','value', to_char(application_rate,'FM999990.0') || '%','sub','Seekers connected / seekers who answered','accent','green')
        )
      ),
      jsonb_build_object(
        'key','call','title','Call metrics','subtitle','Per call (raw rows)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','total_calls','label','Total Calls','value', to_char(total_calls,'FM999,999,999'),'sub','All call attempts','accent','blue'),
          jsonb_build_object('key','answered','label','Answered Calls','value', to_char(answered_calls,'FM999,999,999'),'sub', to_char(pickup,'FM999990.0') || '% pickup rate','accent','green'),
          jsonb_build_object('key','unanswered','label','Unanswered Calls','value', to_char(unanswered_calls,'FM999,999,999'),'sub','No pickup','accent','red'),
          jsonb_build_object('key','productive','label','Productive Conversations','value', to_char(productive_pct,'FM999990.0') || '%','sub', to_char(productive_calls,'FM999,999,999') || ' calls — answered + duration > 30s','accent','amber'),
          jsonb_build_object('key','avg_duration','label','Avg Call Duration','value', to_char(ROUND(avg_dur::numeric, 1),'FM999990.0') || ' sec','sub','Answered calls only','accent','blue')
        )
      )
    );
  ELSE
    WITH base AS (
      SELECT
        phone,
        lower(COALESCE(call_status,'')) AS cs,
        call_duration_seconds AS dur,
        lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
        lower(COALESCE(new_job_posted, data->'raw'->>'new_job_posted','')) AS njp,
        lower(COALESCE(data->'raw'->>'new_job_mentioned','')) AS njm,
        COALESCE((regexp_match(COALESCE(data->'raw'->>'num_vacancies_input',''), '\d+'))[1]::int, 0) AS nvi,
        COALESCE((regexp_match(COALESCE(data->'raw'->>'new_job_vacancies',''), '\d+'))[1]::int, 0) AS njv,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE
          WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
            THEN substring(data->>'call_datetime_ist', 1, 10)::date
          WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
            THEN substring(campaign_date, 1, 10)::date
          ELSE NULL END AS call_date
      FROM public.call_rows WHERE program = 'providers' AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state = 'all' OR region_code = _state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type = _campaign)
    )
    SELECT
      COUNT(*)::int,
      COUNT(*) FILTER (WHERE cs LIKE 'answered%' OR cs = 'completed')::int,
      COUNT(*) FILTER (WHERE (cs LIKE 'answered%' OR cs = 'completed') AND COALESCE(dur,0) > 30)::int,
      COALESCE(AVG(dur) FILTER (WHERE cs LIKE 'answered%' OR cs = 'completed'), 0),
      COALESCE(SUM(nvi), 0)::int,
      COALESCE(SUM(nvi) FILTER (WHERE js = 'active'), 0)::int,
      COALESCE(SUM(nvi) FILTER (WHERE js = 'closed'), 0)::int,
      COALESCE(SUM(njv) FILTER (WHERE njp = 'yes'), 0)::int,
      COUNT(DISTINCT phone) FILTER (WHERE phone IS NOT NULL AND phone <> '')::int,
      COUNT(*) FILTER (WHERE js = 'active')::int,
      COUNT(*) FILTER (WHERE js = 'closed')::int,
      COUNT(DISTINCT phone) FILTER (WHERE njm = 'yes' AND phone IS NOT NULL AND phone <> '')::int
    INTO total_calls, answered_calls, productive_calls, avg_dur,
         total_openings, active_openings, closed_openings, new_openings,
         companies_called, jobs_active, jobs_closed, new_jobs_discussed
    FROM f;

    unanswered_calls := total_calls - answered_calls;
    pickup := CASE WHEN total_calls = 0 THEN 0 ELSE 100.0 * answered_calls / total_calls END;
    productive_pct := CASE WHEN answered_calls = 0 THEN 0 ELSE 100.0 * productive_calls / answered_calls END;
    unresolved_openings := GREATEST(total_openings - active_openings - closed_openings, 0);
    active_open_pct := CASE WHEN total_openings = 0 THEN 0 ELSE 100.0 * active_openings / total_openings END;
    closed_open_pct := CASE WHEN total_openings = 0 THEN 0 ELSE 100.0 * closed_openings / total_openings END;
    unresolved_open_pct := CASE WHEN total_openings = 0 THEN 0 ELSE 100.0 * unresolved_openings / total_openings END;
    companies_unresolved := GREATEST(companies_called - jobs_active - jobs_closed, 0);
    jobs_active_pct := CASE WHEN companies_called = 0 THEN 0 ELSE 100.0 * jobs_active / companies_called END;
    jobs_closed_pct := CASE WHEN companies_called = 0 THEN 0 ELSE 100.0 * jobs_closed / companies_called END;
    companies_unresolved_pct := CASE WHEN companies_called = 0 THEN 0 ELSE 100.0 * companies_unresolved / companies_called END;

    result := jsonb_build_array(
      jsonb_build_object(
        'key','openings','title','Outcome metrics — Openings',
        'subtitle','Vacancy-weighted (sum of num_vacancies_input)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','total_openings','label','Total Openings (Before Campaign)','value', to_char(total_openings,'FM999,999,999'),'sub','Vacancies across all contacted companies','accent','blue'),
          jsonb_build_object('key','active_openings','label','Active Openings','value', to_char(active_openings,'FM999,999,999'),'sub', to_char(active_open_pct,'FM999990.0') || '% of total openings','accent','green'),
          jsonb_build_object('key','closed_openings','label','Closed Openings','value', to_char(closed_openings,'FM999,999,999'),'sub', to_char(closed_open_pct,'FM999990.0') || '% — positions filled','accent','red'),
          jsonb_build_object('key','unresolved_openings','label','Unresolved Openings','value', to_char(unresolved_openings,'FM999,999,999'),'sub', to_char(unresolved_open_pct,'FM999990.0') || '% — not confirmed','accent','amber'),
          jsonb_build_object('key','new_openings','label','New Openings Captured','value', to_char(new_openings,'FM999,999,999'),'sub','From new jobs posted this campaign','accent','blue')
        )
      ),
      jsonb_build_object(
        'key','companies','title','Outcome metrics — Companies',
        'subtitle','Per unique company (deduped by contact phone)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','companies_called','label','Companies Called','value', to_char(companies_called,'FM999,999,999'),'sub','Unique phone numbers','accent','blue'),
          jsonb_build_object('key','jobs_active','label','Jobs Confirmed Active','value', to_char(jobs_active,'FM999,999,999'),'sub', to_char(jobs_active_pct,'FM999990.0') || '% of companies called','accent','green'),
          jsonb_build_object('key','jobs_closed','label','Jobs Confirmed Closed','value', to_char(jobs_closed,'FM999,999,999'),'sub', to_char(jobs_closed_pct,'FM999990.0') || '% — no longer hiring','accent','red'),
          jsonb_build_object('key','companies_unresolved','label','Unresolved','value', to_char(companies_unresolved,'FM999,999,999'),'sub', to_char(companies_unresolved_pct,'FM999990.0') || '% — no confirmation','accent','amber'),
          jsonb_build_object('key','new_jobs_discussed','label','New Jobs Discussed','value', to_char(new_jobs_discussed,'FM999,999,999'),'sub','Companies that mentioned a new role','accent','blue')
        )
      ),
      jsonb_build_object(
        'key','call','title','Call metrics','subtitle','Per call (raw rows)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','total_calls','label','Total Calls','value', to_char(total_calls,'FM999,999,999'),'sub','All call attempts','accent','blue'),
          jsonb_build_object('key','answered','label','Answered Calls','value', to_char(answered_calls,'FM999,999,999'),'sub', to_char(pickup,'FM999990.0') || '% pickup rate','accent','green'),
          jsonb_build_object('key','unanswered','label','Unanswered Calls','value', to_char(unanswered_calls,'FM999,999,999'),'sub','No pickup','accent','red'),
          jsonb_build_object('key','productive','label','Productive Conversations','value', to_char(productive_pct,'FM999990.0') || '%','sub', to_char(productive_calls,'FM999,999,999') || ' calls — answered + over 30 seconds','accent','amber'),
          jsonb_build_object('key','avg_duration','label','Avg Call Duration','value', to_char(ROUND(avg_dur::numeric, 1),'FM999990.0') || ' sec','sub','Answered calls only','accent','blue')
        )
      )
    );
  END IF;

  RETURN result;
END;
$$;

CREATE OR REPLACE FUNCTION public.get_program_metric_groups_np(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $$
DECLARE
  total_calls int := 0;
  answered_calls int := 0;
  unanswered_calls int := 0;
  productive_calls int := 0;
  avg_dur numeric := 0;
  seekers int := 0;
  answered_seekers int := 0;
  applied_seekers int := 0;
  failed_seekers int := 0;
  did_not_apply int := 0;
  total_applications numeric := 0;
  tried int := 0;
  application_rate numeric := 0;
  applied_pct numeric := 0;
  failed_pct numeric := 0;
  pickup numeric := 0;
  productive_pct numeric := 0;
  total_openings int := 0;
  active_openings int := 0;
  closed_openings int := 0;
  unresolved_openings int := 0;
  new_openings int := 0;
  active_open_pct numeric := 0;
  closed_open_pct numeric := 0;
  unresolved_open_pct numeric := 0;
  companies_called int := 0;
  jobs_active int := 0;
  jobs_closed int := 0;
  companies_unresolved int := 0;
  new_jobs_discussed int := 0;
  jobs_active_pct numeric := 0;
  jobs_closed_pct numeric := 0;
  companies_unresolved_pct numeric := 0;
  result jsonb;
BEGIN
  IF _program = 'seekers' THEN
    WITH base AS (
      SELECT
        phone, call_answered, call_duration_seconds, applied_to_job,
        tried_to_apply, applications_count,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE
          WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
            THEN substring(data->>'call_datetime_ist', 1, 10)::date
          WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
            THEN substring(campaign_date, 1, 10)::date
          ELSE NULL END AS call_date
      FROM public.call_rows_np WHERE program = 'seekers' AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state = 'all' OR region_code = _state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type = _campaign)
        AND (
          _campaign_type = 'all' OR campaign_type = _campaign_type
        )
    )
    SELECT
      COUNT(*)::int,
      COUNT(*) FILTER (WHERE call_answered)::int,
      COUNT(*) FILTER (WHERE call_answered AND COALESCE(call_duration_seconds,0) > 30)::int,
      COALESCE(AVG(call_duration_seconds) FILTER (WHERE call_answered), 0),
      COUNT(DISTINCT phone) FILTER (WHERE phone IS NOT NULL AND phone <> '')::int,
      COUNT(DISTINCT phone) FILTER (WHERE call_answered AND phone IS NOT NULL AND phone <> '')::int,
      COUNT(DISTINCT phone) FILTER (WHERE applied_to_job AND phone IS NOT NULL AND phone <> '')::int,
      COUNT(DISTINCT phone) FILTER (WHERE COALESCE(tried_to_apply,false) AND NOT COALESCE(applied_to_job,false) AND phone IS NOT NULL AND phone <> '')::int,
      COALESCE(SUM(applications_count), 0)
    INTO total_calls, answered_calls, productive_calls, avg_dur,
         seekers, answered_seekers, applied_seekers, failed_seekers, total_applications
    FROM f;

    unanswered_calls := total_calls - answered_calls;
    pickup := CASE WHEN total_calls = 0 THEN 0 ELSE 100.0 * answered_calls / total_calls END;
    productive_pct := CASE WHEN total_calls = 0 THEN 0 ELSE 100.0 * productive_calls / total_calls END;
    did_not_apply := GREATEST(answered_seekers - applied_seekers, 0);
    tried := applied_seekers + failed_seekers;
    applied_pct := CASE WHEN answered_seekers = 0 THEN 0 ELSE 100.0 * applied_seekers / answered_seekers END;
    failed_pct := CASE WHEN tried = 0 THEN 0 ELSE 100.0 * failed_seekers / tried END;
    application_rate := applied_pct;

    result := jsonb_build_array(
      jsonb_build_object(
        'key','outcome','title','Outcome metrics',
        'subtitle','Per unique seeker (deduped by phone)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','seekers_called','label','Seekers Called','value', to_char(seekers,'FM999,999,999'),'sub','Unique phone numbers','accent','blue'),
          jsonb_build_object('key','applied','label','Connected to a Provider','value', to_char(applied_seekers,'FM999,999,999'),'sub', to_char(applied_pct,'FM999990.0') || '% of seekers who answered','accent','green'),
          jsonb_build_object('key','failed_apply','label','Failed Connection Attempts','value', to_char(failed_seekers,'FM999,999,999'),'sub', to_char(failed_pct,'FM999990.0') || '% of seekers who attempted','accent','amber'),
          jsonb_build_object('key','did_not_apply','label','Not Connected','value', to_char(did_not_apply,'FM999,999,999'),'sub','Answered but no provider connection','accent','red'),
          jsonb_build_object('key','total_applications','label','Total Connections','value', to_char(ROUND(total_applications),'FM999,999,999'),'sub','Across all calls','accent','blue'),
          jsonb_build_object('key','application_rate','label','Connection Rate','value', to_char(application_rate,'FM999990.0') || '%','sub','Seekers connected / seekers who answered','accent','green')
        )
      ),
      jsonb_build_object(
        'key','call','title','Call metrics','subtitle','Per call (raw rows)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','total_calls','label','Total Calls','value', to_char(total_calls,'FM999,999,999'),'sub','All call attempts','accent','blue'),
          jsonb_build_object('key','answered','label','Answered Calls','value', to_char(answered_calls,'FM999,999,999'),'sub', to_char(pickup,'FM999990.0') || '% pickup rate','accent','green'),
          jsonb_build_object('key','unanswered','label','Unanswered Calls','value', to_char(unanswered_calls,'FM999,999,999'),'sub','No pickup','accent','red'),
          jsonb_build_object('key','productive','label','Productive Conversations','value', to_char(productive_pct,'FM999990.0') || '%','sub', to_char(productive_calls,'FM999,999,999') || ' calls — answered + duration > 30s','accent','amber'),
          jsonb_build_object('key','avg_duration','label','Avg Call Duration','value', to_char(ROUND(avg_dur::numeric, 1),'FM999990.0') || ' sec','sub','Answered calls only','accent','blue')
        )
      )
    );
  ELSE
    WITH base AS (
      SELECT
        phone,
        lower(COALESCE(call_status,'')) AS cs,
        call_duration_seconds AS dur,
        lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
        lower(COALESCE(new_job_posted, data->'raw'->>'new_job_posted','')) AS njp,
        lower(COALESCE(data->'raw'->>'new_job_mentioned','')) AS njm,
        COALESCE((regexp_match(COALESCE(data->'raw'->>'num_vacancies_input',''), '\d+'))[1]::int, 0) AS nvi,
        COALESCE((regexp_match(COALESCE(data->'raw'->>'new_job_vacancies',''), '\d+'))[1]::int, 0) AS njv,
        COALESCE(campaign_type,'') AS campaign_type,
        COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
        CASE
          WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
            THEN substring(data->>'call_datetime_ist', 1, 10)::date
          WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
            THEN substring(campaign_date, 1, 10)::date
          ELSE NULL END AS call_date
      FROM public.call_rows_np WHERE program = 'providers' AND (_channel = 'all' OR channel = _channel)
    ),
    f AS (
      SELECT * FROM base
      WHERE (_state = 'all' OR region_code = _state)
        AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
        AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
        AND (_campaign IS NULL OR campaign_type = _campaign)
    )
    SELECT
      COUNT(*)::int,
      COUNT(*) FILTER (WHERE cs LIKE 'answered%' OR cs = 'completed')::int,
      COUNT(*) FILTER (WHERE (cs LIKE 'answered%' OR cs = 'completed') AND COALESCE(dur,0) > 30)::int,
      COALESCE(AVG(dur) FILTER (WHERE cs LIKE 'answered%' OR cs = 'completed'), 0),
      COALESCE(SUM(nvi), 0)::int,
      COALESCE(SUM(nvi) FILTER (WHERE js = 'active'), 0)::int,
      COALESCE(SUM(nvi) FILTER (WHERE js = 'closed'), 0)::int,
      COALESCE(SUM(njv) FILTER (WHERE njp = 'yes'), 0)::int,
      COUNT(DISTINCT phone) FILTER (WHERE phone IS NOT NULL AND phone <> '')::int,
      COUNT(*) FILTER (WHERE js = 'active')::int,
      COUNT(*) FILTER (WHERE js = 'closed')::int,
      COUNT(DISTINCT phone) FILTER (WHERE njm = 'yes' AND phone IS NOT NULL AND phone <> '')::int
    INTO total_calls, answered_calls, productive_calls, avg_dur,
         total_openings, active_openings, closed_openings, new_openings,
         companies_called, jobs_active, jobs_closed, new_jobs_discussed
    FROM f;

    unanswered_calls := total_calls - answered_calls;
    pickup := CASE WHEN total_calls = 0 THEN 0 ELSE 100.0 * answered_calls / total_calls END;
    productive_pct := CASE WHEN answered_calls = 0 THEN 0 ELSE 100.0 * productive_calls / answered_calls END;
    unresolved_openings := GREATEST(total_openings - active_openings - closed_openings, 0);
    active_open_pct := CASE WHEN total_openings = 0 THEN 0 ELSE 100.0 * active_openings / total_openings END;
    closed_open_pct := CASE WHEN total_openings = 0 THEN 0 ELSE 100.0 * closed_openings / total_openings END;
    unresolved_open_pct := CASE WHEN total_openings = 0 THEN 0 ELSE 100.0 * unresolved_openings / total_openings END;
    companies_unresolved := GREATEST(companies_called - jobs_active - jobs_closed, 0);
    jobs_active_pct := CASE WHEN companies_called = 0 THEN 0 ELSE 100.0 * jobs_active / companies_called END;
    jobs_closed_pct := CASE WHEN companies_called = 0 THEN 0 ELSE 100.0 * jobs_closed / companies_called END;
    companies_unresolved_pct := CASE WHEN companies_called = 0 THEN 0 ELSE 100.0 * companies_unresolved / companies_called END;

    result := jsonb_build_array(
      jsonb_build_object(
        'key','openings','title','Outcome metrics — Openings',
        'subtitle','Vacancy-weighted (sum of num_vacancies_input)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','total_openings','label','Total Openings (Before Campaign)','value', to_char(total_openings,'FM999,999,999'),'sub','Vacancies across all contacted companies','accent','blue'),
          jsonb_build_object('key','active_openings','label','Active Openings','value', to_char(active_openings,'FM999,999,999'),'sub', to_char(active_open_pct,'FM999990.0') || '% of total openings','accent','green'),
          jsonb_build_object('key','closed_openings','label','Closed Openings','value', to_char(closed_openings,'FM999,999,999'),'sub', to_char(closed_open_pct,'FM999990.0') || '% — positions filled','accent','red'),
          jsonb_build_object('key','unresolved_openings','label','Unresolved Openings','value', to_char(unresolved_openings,'FM999,999,999'),'sub', to_char(unresolved_open_pct,'FM999990.0') || '% — not confirmed','accent','amber'),
          jsonb_build_object('key','new_openings','label','New Openings Captured','value', to_char(new_openings,'FM999,999,999'),'sub','From new jobs posted this campaign','accent','blue')
        )
      ),
      jsonb_build_object(
        'key','companies','title','Outcome metrics — Companies',
        'subtitle','Per unique company (deduped by contact phone)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','companies_called','label','Companies Called','value', to_char(companies_called,'FM999,999,999'),'sub','Unique phone numbers','accent','blue'),
          jsonb_build_object('key','jobs_active','label','Jobs Confirmed Active','value', to_char(jobs_active,'FM999,999,999'),'sub', to_char(jobs_active_pct,'FM999990.0') || '% of companies called','accent','green'),
          jsonb_build_object('key','jobs_closed','label','Jobs Confirmed Closed','value', to_char(jobs_closed,'FM999,999,999'),'sub', to_char(jobs_closed_pct,'FM999990.0') || '% — no longer hiring','accent','red'),
          jsonb_build_object('key','companies_unresolved','label','Unresolved','value', to_char(companies_unresolved,'FM999,999,999'),'sub', to_char(companies_unresolved_pct,'FM999990.0') || '% — no confirmation','accent','amber'),
          jsonb_build_object('key','new_jobs_discussed','label','New Jobs Discussed','value', to_char(new_jobs_discussed,'FM999,999,999'),'sub','Companies that mentioned a new role','accent','blue')
        )
      ),
      jsonb_build_object(
        'key','call','title','Call metrics','subtitle','Per call (raw rows)',
        'cards', jsonb_build_array(
          jsonb_build_object('key','total_calls','label','Total Calls','value', to_char(total_calls,'FM999,999,999'),'sub','All call attempts','accent','blue'),
          jsonb_build_object('key','answered','label','Answered Calls','value', to_char(answered_calls,'FM999,999,999'),'sub', to_char(pickup,'FM999990.0') || '% pickup rate','accent','green'),
          jsonb_build_object('key','unanswered','label','Unanswered Calls','value', to_char(unanswered_calls,'FM999,999,999'),'sub','No pickup','accent','red'),
          jsonb_build_object('key','productive','label','Productive Conversations','value', to_char(productive_pct,'FM999990.0') || '%','sub', to_char(productive_calls,'FM999,999,999') || ' calls — answered + over 30 seconds','accent','amber'),
          jsonb_build_object('key','avg_duration','label','Avg Call Duration','value', to_char(ROUND(avg_dur::numeric, 1),'FM999990.0') || ' sec','sub','Answered calls only','accent','blue')
        )
      )
    );
  END IF;

  RETURN result;
END;
$$;

CREATE OR REPLACE FUNCTION public.get_program_metrics_raw(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE
  total_calls int := 0;
  answered_calls int := 0;
  productive_calls int := 0;
  avg_dur numeric := 0;
  result jsonb;
BEGIN
  IF _program = 'seekers' THEN
    DECLARE
      seekers int; answered_seekers int; applied_seekers int; failed_seekers int;
      total_applications numeric; tried int;
      engaged_calls int; jobs_shown_calls int; high_intent_calls int;
      apps_submitted int; apps_blocked int; apps_total int;
      engaged_seekers int; jobs_shown_seekers int; high_intent_seekers int; applications_seekers int;
      profile_calls int; needs_calls int; found_calls int; connected_calls int;
      profile_seekers int; needs_seekers int; found_seekers int; connected_seekers int;
    BEGIN
      WITH base AS (
        SELECT
          phone, call_answered, call_engaged, call_duration_seconds, intent_score,
          applied_to_job, tried_to_apply, applications_count,
          -- Survival rank: which bot phase the call was abandoned at. Blank means
          -- the journey completed. An UNRECOGNISED label maps to 1 rather than 9,
          -- so a new phase the normaliser does not know cannot silently inflate
          -- the completion count -- it shows as an early drop instead, and the
          -- call-record QC has a check that flags unrecognised labels outright.
          CASE
            WHEN COALESCE(btrim(phases_reached), '') = ''          THEN 9
            WHEN phases_reached = 'Profile fetch & name confirmation'  THEN 1
            WHEN phases_reached = 'Profile completion & verification'  THEN 2
            WHEN phases_reached = 'Identify disability type'           THEN 3
            WHEN phases_reached = 'Needs & challenges evaluation'      THEN 4
            WHEN phases_reached = 'Options delivery & decision making' THEN 5
            WHEN phases_reached = 'Summary & profile update'           THEN 6
            WHEN phases_reached = 'Match provider'                     THEN 7
            WHEN phases_reached = 'Connect to provider'                THEN 8
            ELSE 1
          END AS stage_rank,
          COALESCE(campaign_type,'') AS campaign_type,
          ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag, (lower(COALESCE(call_outcome,'')) <> 'pending' AND lower(COALESCE(call_outcome,'')) NOT LIKE 'not dialled%') AS dialled_flag,
          (COALESCE(applied_to_job,false)
            OR (jsonb_typeof(data->'jobs_applied') = 'array' AND jsonb_array_length(data->'jobs_applied') > 0)) AS submitted_flag,
          (jsonb_typeof(data->'jobs_failed_to_apply') = 'array' AND jsonb_array_length(data->'jobs_failed_to_apply') > 0) AS blocked_flag,
          COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'matching_providers_found',''), '[^0-9]', '', 'g'), '')::int, 0) AS matching_providers_found_i,
          COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'providers_connected',''), '[^0-9]', '', 'g'), '')::int, 0) AS providers_connected_i,
          -- Computed HERE, not in cum: `data` is a column of call_rows and is in
          -- scope only inside base. cum selects from f, which carries base's
          -- OUTPUT columns -- `data` is not among them.
          (lower(COALESCE(data->'raw'->>'connect_provider_api_successful',''))
             IN ('true','t','yes','1')) AS connect_api_ok,
          COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
          CASE
            WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
              THEN (DATE '1899-12-30' + (campaign_date)::int)::date
            WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
              THEN substring(campaign_date, 1, 10)::date
            WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
              THEN substring(data->>'call_datetime_ist', 1, 10)::date
            ELSE NULL END AS call_date
        FROM public.call_rows WHERE program = 'seekers' AND (_channel = 'all' OR channel = _channel)
      ),
      f AS (
        SELECT * FROM base
        WHERE (_state = 'all' OR region_code = _state)
          AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
          AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
          AND (_campaign IS NULL OR campaign_type = _campaign)
          AND (
            _campaign_type = 'all' OR campaign_type = _campaign_type
          )
      ),
      cum AS (
        SELECT *,
          -- Funnel stages are SURVIVAL through the bot's own phases, scoped to
          -- answered calls. One source, so the ladder is monotonic by construction
          -- and reconciles exactly against the row count.
          --
          -- Previously these chained booleans with AND, which had two failures:
          -- profile_completeness and needs_mentioned do not exist upstream, so
          -- every stage behind them read 0 -- including providers found and
          -- connected, which DO have data. Real connections reported as zero.
          --
          -- "Reached stage N" is stage_rank >= N: a call abandoned at phase N
          -- did not reach N+1. stage_rank 9 means the journey completed.
          call_answered AS s_picked,
          (call_answered AND stage_rank >= 2) AS s_engaged,
          (call_answered AND stage_rank >= 3) AS s_profile,
          (call_answered AND stage_rank >= 5) AS s_needs,
          -- OUTCOME stages count confirmed outcomes, not phase-reach. A connection
          -- the API failed to create is not a connection. Survival said 12 reached
          -- the connect phase but only 3 have providers actually recorded, so
          -- phase-reach overstates the outcome by 4x.
          (call_answered AND COALESCE(matching_providers_found_i,0) > 0) AS s_found,
          (call_answered AND connect_api_ok) AS s_connected,
          -- Conversation stages (engaged / profile / needs) stay on survival: they
          -- measure how far the conversation got. Outcome stages use the API result.
          -- These are different dimensions and should not share a source.
          (call_answered AND call_engaged AND jobs_shown_flag) AS s_jobs,
          (call_answered AND COALESCE(intent_score,0) >= 5) AS s_intent,
          (call_answered AND (submitted_flag OR blocked_flag)) AS s_apps
        FROM f
      )
      SELECT
        COUNT(*) FILTER (WHERE dialled_flag)::int,
        COUNT(*) FILTER (WHERE call_answered)::int,
        COUNT(*) FILTER (WHERE call_answered AND COALESCE(call_duration_seconds,0) > 30)::int,
        COALESCE(AVG(call_duration_seconds) FILTER (WHERE call_answered), 0),
        COUNT(*) FILTER (WHERE s_engaged)::int,
        COUNT(*) FILTER (WHERE s_jobs)::int,
        COUNT(*) FILTER (WHERE s_intent)::int,
        COUNT(*) FILTER (WHERE s_apps AND submitted_flag)::int,
        COUNT(*) FILTER (WHERE s_apps AND blocked_flag AND NOT submitted_flag)::int,
        COUNT(*) FILTER (WHERE s_apps)::int,
        COUNT(DISTINCT phone) FILTER (WHERE phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE call_answered AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE applied_to_job AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE COALESCE(tried_to_apply,false) AND NOT COALESCE(applied_to_job,false) AND phone IS NOT NULL AND phone <> '')::int,
        COALESCE(SUM(applications_count), 0),
        COUNT(DISTINCT phone) FILTER (WHERE s_engaged AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_jobs    AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_intent  AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_apps    AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(*) FILTER (WHERE s_profile)::int,
        COUNT(*) FILTER (WHERE s_needs)::int,
        COUNT(*) FILTER (WHERE s_found)::int,
        COUNT(*) FILTER (WHERE s_connected)::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_profile   AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_needs     AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_found     AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_connected AND phone IS NOT NULL AND phone <> '')::int
      INTO total_calls, answered_calls, productive_calls, avg_dur,
           engaged_calls, jobs_shown_calls, high_intent_calls,
           apps_submitted, apps_blocked, apps_total,
           seekers, answered_seekers, applied_seekers, failed_seekers, total_applications,
           engaged_seekers, jobs_shown_seekers, high_intent_seekers, applications_seekers,
           profile_calls, needs_calls, found_calls, connected_calls,
           profile_seekers, needs_seekers, found_seekers, connected_seekers
      FROM cum;

      tried := applied_seekers + failed_seekers;
      result := jsonb_build_object(
        'program','seekers','totalCalls', total_calls,'answeredCalls', answered_calls,
        'unansweredCalls', GREATEST(total_calls - answered_calls, 0),
        'productiveCalls', productive_calls,'avgDuration', ROUND(avg_dur::numeric, 1),
        'engagedCalls', engaged_calls,'jobsShownCalls', jobs_shown_calls,
        'highIntentCalls', high_intent_calls,
        'applicationsSubmitted', apps_submitted,'applicationsBlocked', apps_blocked,
        'applicationsTotal', apps_total,'hasInterviewData', false,'interviewCount', 0,
        'seekers', seekers,'answeredSeekers', answered_seekers,'triedSeekers', tried,
        'appliedSeekers', applied_seekers,'failedSeekers', failed_seekers,
        'didNotApply', GREATEST(answered_seekers - applied_seekers, 0),
        'totalApplications', ROUND(total_applications)::int,
        'engagedSeekers', engaged_seekers,
        'jobsShownSeekers', jobs_shown_seekers,
        'highIntentSeekers', high_intent_seekers,
        'applicationsSeekers', applications_seekers,
        'profileCapturedCalls', profile_calls,'needsCapturedCalls', needs_calls,
        'providersFoundCalls', found_calls,'providersConnectedCalls', connected_calls,
        'profileCapturedSeekers', profile_seekers,'needsCapturedSeekers', needs_seekers,
        'providersFoundSeekers', found_seekers,'providersConnectedSeekers', connected_seekers
      );
    END;
    RETURN result;
  ELSIF _program = 'providers' THEN
    DECLARE
      total_openings int; active_openings int; closed_openings int; new_openings int;
      companies_called int; jobs_active int; jobs_closed int; new_jobs_discussed int;
      active_providers int; new_jobs_posted int;
      funnel jsonb;
      p_called int; p_picked int; p_engaged int;
      o_called int; o_picked int; o_engaged int;
      c_active int; c_new_jobs int;
    BEGIN
      WITH base AS (
        SELECT
          phone,
          lower(COALESCE(call_status,'')) AS cs,
          call_duration_seconds AS dur,
          lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
          lower(COALESCE(new_job_posted, data->'raw'->>'new_job_posted','')) AS njp,
          lower(COALESCE(data->'raw'->>'new_job_mentioned','')) AS njm,
          COALESCE((regexp_match(COALESCE(data->'raw'->>'num_vacancies_input',''), '\d+'))[1]::int, 0) AS nvi,
          COALESCE((
            SELECT SUM((m[1])::int)
            FROM regexp_matches(COALESCE(data->'raw'->>'new_job_vacancies',''), '\d+', 'g') AS m
            WHERE (m[1])::int <= 500
          ), 0)::int AS njv,
          COALESCE(campaign_type,'') AS campaign_type,
          COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
          CASE
            WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
              THEN (DATE '1899-12-30' + (campaign_date)::int)::date
            WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
              THEN substring(campaign_date, 1, 10)::date
            WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
              THEN substring(data->>'call_datetime_ist', 1, 10)::date
            ELSE NULL END AS call_date
        FROM public.call_rows WHERE program = 'providers' AND (_channel = 'all' OR channel = _channel)
      ),
      f AS (
        SELECT * FROM base
        WHERE (_state = 'all' OR region_code = _state)
          AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
          AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
          AND (_campaign IS NULL OR campaign_type = _campaign)
      ),
      provider AS (
        SELECT
          phone,
          bool_or(cs LIKE 'answered%' OR cs = 'completed') AS picked,
          bool_or((cs LIKE 'answered%' OR cs = 'completed') AND COALESCE(dur,0) > 30) AS engaged,
          SUM(nvi)::int AS openings
        FROM f
        WHERE phone IS NOT NULL AND phone <> ''
        GROUP BY phone
      )
      SELECT
        COUNT(*)::int,
        COUNT(*) FILTER (WHERE cs LIKE 'answered%' OR cs = 'completed')::int,
        COUNT(*) FILTER (WHERE (cs LIKE 'answered%' OR cs = 'completed') AND COALESCE(dur,0) > 30)::int,
        COALESCE(AVG(dur) FILTER (WHERE cs LIKE 'answered%' OR cs = 'completed'), 0),
        COALESCE(SUM(nvi), 0)::int,
        COALESCE(SUM(nvi) FILTER (WHERE js = 'active'), 0)::int,
        COALESCE(SUM(nvi) FILTER (WHERE js = 'closed'), 0)::int,
        COALESCE(SUM(njv) FILTER (WHERE njp = 'yes'), 0)::int,
        COUNT(DISTINCT phone) FILTER (WHERE phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE js = 'active' AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(*) FILTER (WHERE js = 'closed')::int,
        COUNT(DISTINCT phone) FILTER (WHERE njm = 'yes' AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE njp = 'yes' AND phone IS NOT NULL AND phone <> '')::int,
        (SELECT COUNT(*)::int FROM provider),
        (SELECT COUNT(*)::int FROM provider WHERE picked),
        (SELECT COUNT(*)::int FROM provider WHERE engaged),
        (SELECT COALESCE(SUM(openings),0)::int FROM provider),
        (SELECT COALESCE(SUM(openings),0)::int FROM provider WHERE picked),
        (SELECT COALESCE(SUM(openings),0)::int FROM provider WHERE engaged),
        COUNT(*) FILTER (WHERE js = 'active')::int,
        COUNT(*) FILTER (WHERE njp = 'yes')::int
      INTO total_calls, answered_calls, productive_calls, avg_dur,
           total_openings, active_openings, closed_openings, new_openings,
           companies_called, active_providers, jobs_closed, new_jobs_discussed,
           new_jobs_posted,
           p_called, p_picked, p_engaged,
           o_called, o_picked, o_engaged,
           c_active, c_new_jobs
      FROM f;

      jobs_active := active_providers;

      funnel := jsonb_build_array(
        jsonb_build_object('key','called','label','Called','providers', p_called, 'openings', o_called, 'calls', total_calls),
        jsonb_build_object('key','picked','label','Picked up','providers', p_picked, 'openings', o_picked, 'calls', answered_calls),
        jsonb_build_object('key','engaged','label','Engaged (30s+)','providers', p_engaged, 'openings', o_engaged, 'calls', productive_calls),
        jsonb_build_object('key','active','label','Actively hiring','providers', active_providers, 'openings', active_openings, 'calls', c_active),
        jsonb_build_object('key','new_jobs','label','New jobs posted','providers', new_jobs_posted, 'openings', new_openings, 'calls', c_new_jobs)
      );

      result := jsonb_build_object(
        'program','providers','totalCalls', total_calls,'answeredCalls', answered_calls,
        'unansweredCalls', GREATEST(total_calls - answered_calls, 0),
        'productiveCalls', productive_calls,'avgDuration', ROUND(avg_dur::numeric, 1),
        'totalOpenings', total_openings,'activeOpenings', active_openings,
        'closedOpenings', closed_openings,
        'unresolvedOpenings', GREATEST(total_openings - active_openings - closed_openings, 0),
        'newOpenings', new_openings,'companiesCalled', companies_called,
        'jobsActive', jobs_active,'jobsClosed', jobs_closed,
        'companiesUnresolved', GREATEST(companies_called - jobs_active - jobs_closed, 0),
        'newJobsDiscussed', new_jobs_discussed,
        'newJobsPosted', new_jobs_posted,
        'providerFunnel', funnel
      );
    END;
    RETURN result;
  END IF;
  RETURN '{}'::jsonb;
END;
$_$;

CREATE OR REPLACE FUNCTION public.get_program_metrics_raw_np(_program text, _state text DEFAULT 'all'::text, _date_from date DEFAULT NULL::date, _date_to date DEFAULT NULL::date, _campaign_type text DEFAULT 'all'::text, _campaign text DEFAULT NULL::text, _channel text DEFAULT 'all'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'public'
    AS $_$
DECLARE
  total_calls int := 0;
  answered_calls int := 0;
  productive_calls int := 0;
  avg_dur numeric := 0;
  result jsonb;
BEGIN
  IF _program = 'seekers' THEN
    DECLARE
      seekers int; answered_seekers int; applied_seekers int; failed_seekers int;
      total_applications numeric; tried int;
      engaged_calls int; jobs_shown_calls int; high_intent_calls int;
      apps_submitted int; apps_blocked int; apps_total int;
      engaged_seekers int; jobs_shown_seekers int; high_intent_seekers int; applications_seekers int;
      profile_calls int; needs_calls int; found_calls int; connected_calls int;
      profile_seekers int; needs_seekers int; found_seekers int; connected_seekers int;
    BEGIN
      WITH base AS (
        SELECT
          phone, call_answered, call_engaged, call_duration_seconds, intent_score,
          applied_to_job, tried_to_apply, applications_count,
          -- Survival rank: which bot phase the call was abandoned at. Blank means
          -- the journey completed. An UNRECOGNISED label maps to 1 rather than 9,
          -- so a new phase the normaliser does not know cannot silently inflate
          -- the completion count -- it shows as an early drop instead, and the
          -- call-record QC has a check that flags unrecognised labels outright.
          CASE
            WHEN COALESCE(btrim(phases_reached), '') = ''          THEN 9
            WHEN phases_reached = 'Profile fetch & name confirmation'  THEN 1
            WHEN phases_reached = 'Profile completion & verification'  THEN 2
            WHEN phases_reached = 'Identify disability type'           THEN 3
            WHEN phases_reached = 'Needs & challenges evaluation'      THEN 4
            WHEN phases_reached = 'Options delivery & decision making' THEN 5
            WHEN phases_reached = 'Summary & profile update'           THEN 6
            WHEN phases_reached = 'Match provider'                     THEN 7
            WHEN phases_reached = 'Connect to provider'                THEN 8
            ELSE 1
          END AS stage_rank,
          COALESCE(campaign_type,'') AS campaign_type,
          ((data->>'jobs_shown') IS NOT NULL AND lower(data->>'jobs_shown') IN ('true','yes','y','1')) AS jobs_shown_flag, (lower(COALESCE(call_outcome,'')) <> 'pending' AND lower(COALESCE(call_outcome,'')) NOT LIKE 'not dialled%') AS dialled_flag,
          (COALESCE(applied_to_job,false)
            OR (jsonb_typeof(data->'jobs_applied') = 'array' AND jsonb_array_length(data->'jobs_applied') > 0)) AS submitted_flag,
          (jsonb_typeof(data->'jobs_failed_to_apply') = 'array' AND jsonb_array_length(data->'jobs_failed_to_apply') > 0) AS blocked_flag,
          COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'matching_providers_found',''), '[^0-9]', '', 'g'), '')::int, 0) AS matching_providers_found_i,
          COALESCE(NULLIF(regexp_replace(COALESCE(data->'raw'->>'providers_connected',''), '[^0-9]', '', 'g'), '')::int, 0) AS providers_connected_i,
          -- Computed HERE, not in cum: `data` is a column of call_rows and is in
          -- scope only inside base. cum selects from f, which carries base's
          -- OUTPUT columns -- `data` is not among them.
          (lower(COALESCE(data->'raw'->>'connect_provider_api_successful',''))
             IN ('true','t','yes','1')) AS connect_api_ok,
          COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
          CASE
            WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
              THEN (DATE '1899-12-30' + (campaign_date)::int)::date
            WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
              THEN substring(campaign_date, 1, 10)::date
            WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
              THEN substring(data->>'call_datetime_ist', 1, 10)::date
            ELSE NULL END AS call_date
        FROM public.call_rows_np WHERE program = 'seekers' AND (_channel = 'all' OR channel = _channel)
      ),
      f AS (
        SELECT * FROM base
        WHERE (_state = 'all' OR region_code = _state)
          AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
          AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
          AND (_campaign IS NULL OR campaign_type = _campaign)
          AND (
            _campaign_type = 'all' OR campaign_type = _campaign_type
          )
      ),
      cum AS (
        SELECT *,
          -- Funnel stages are SURVIVAL through the bot's own phases, scoped to
          -- answered calls. One source, so the ladder is monotonic by construction
          -- and reconciles exactly against the row count.
          --
          -- Previously these chained booleans with AND, which had two failures:
          -- profile_completeness and needs_mentioned do not exist upstream, so
          -- every stage behind them read 0 -- including providers found and
          -- connected, which DO have data. Real connections reported as zero.
          --
          -- "Reached stage N" is stage_rank >= N: a call abandoned at phase N
          -- did not reach N+1. stage_rank 9 means the journey completed.
          call_answered AS s_picked,
          (call_answered AND stage_rank >= 2) AS s_engaged,
          (call_answered AND stage_rank >= 3) AS s_profile,
          (call_answered AND stage_rank >= 5) AS s_needs,
          -- OUTCOME stages count confirmed outcomes, not phase-reach. A connection
          -- the API failed to create is not a connection. Survival said 12 reached
          -- the connect phase but only 3 have providers actually recorded, so
          -- phase-reach overstates the outcome by 4x.
          (call_answered AND COALESCE(matching_providers_found_i,0) > 0) AS s_found,
          (call_answered AND connect_api_ok) AS s_connected,
          -- Conversation stages (engaged / profile / needs) stay on survival: they
          -- measure how far the conversation got. Outcome stages use the API result.
          -- These are different dimensions and should not share a source.
          (call_answered AND call_engaged AND jobs_shown_flag) AS s_jobs,
          (call_answered AND COALESCE(intent_score,0) >= 5) AS s_intent,
          (call_answered AND (submitted_flag OR blocked_flag)) AS s_apps
        FROM f
      )
      SELECT
        COUNT(*) FILTER (WHERE dialled_flag)::int,
        COUNT(*) FILTER (WHERE call_answered)::int,
        COUNT(*) FILTER (WHERE call_answered AND COALESCE(call_duration_seconds,0) > 30)::int,
        COALESCE(AVG(call_duration_seconds) FILTER (WHERE call_answered), 0),
        COUNT(*) FILTER (WHERE s_engaged)::int,
        COUNT(*) FILTER (WHERE s_jobs)::int,
        COUNT(*) FILTER (WHERE s_intent)::int,
        COUNT(*) FILTER (WHERE s_apps AND submitted_flag)::int,
        COUNT(*) FILTER (WHERE s_apps AND blocked_flag AND NOT submitted_flag)::int,
        COUNT(*) FILTER (WHERE s_apps)::int,
        COUNT(DISTINCT phone) FILTER (WHERE phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE call_answered AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE applied_to_job AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE COALESCE(tried_to_apply,false) AND NOT COALESCE(applied_to_job,false) AND phone IS NOT NULL AND phone <> '')::int,
        COALESCE(SUM(applications_count), 0),
        COUNT(DISTINCT phone) FILTER (WHERE s_engaged AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_jobs    AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_intent  AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_apps    AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(*) FILTER (WHERE s_profile)::int,
        COUNT(*) FILTER (WHERE s_needs)::int,
        COUNT(*) FILTER (WHERE s_found)::int,
        COUNT(*) FILTER (WHERE s_connected)::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_profile   AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_needs     AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_found     AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE s_connected AND phone IS NOT NULL AND phone <> '')::int
      INTO total_calls, answered_calls, productive_calls, avg_dur,
           engaged_calls, jobs_shown_calls, high_intent_calls,
           apps_submitted, apps_blocked, apps_total,
           seekers, answered_seekers, applied_seekers, failed_seekers, total_applications,
           engaged_seekers, jobs_shown_seekers, high_intent_seekers, applications_seekers,
           profile_calls, needs_calls, found_calls, connected_calls,
           profile_seekers, needs_seekers, found_seekers, connected_seekers
      FROM cum;

      tried := applied_seekers + failed_seekers;
      result := jsonb_build_object(
        'program','seekers','totalCalls', total_calls,'answeredCalls', answered_calls,
        'unansweredCalls', GREATEST(total_calls - answered_calls, 0),
        'productiveCalls', productive_calls,'avgDuration', ROUND(avg_dur::numeric, 1),
        'engagedCalls', engaged_calls,'jobsShownCalls', jobs_shown_calls,
        'highIntentCalls', high_intent_calls,
        'applicationsSubmitted', apps_submitted,'applicationsBlocked', apps_blocked,
        'applicationsTotal', apps_total,'hasInterviewData', false,'interviewCount', 0,
        'seekers', seekers,'answeredSeekers', answered_seekers,'triedSeekers', tried,
        'appliedSeekers', applied_seekers,'failedSeekers', failed_seekers,
        'didNotApply', GREATEST(answered_seekers - applied_seekers, 0),
        'totalApplications', ROUND(total_applications)::int,
        'engagedSeekers', engaged_seekers,
        'jobsShownSeekers', jobs_shown_seekers,
        'highIntentSeekers', high_intent_seekers,
        'applicationsSeekers', applications_seekers,
        'profileCapturedCalls', profile_calls,'needsCapturedCalls', needs_calls,
        'providersFoundCalls', found_calls,'providersConnectedCalls', connected_calls,
        'profileCapturedSeekers', profile_seekers,'needsCapturedSeekers', needs_seekers,
        'providersFoundSeekers', found_seekers,'providersConnectedSeekers', connected_seekers
      );
    END;
    RETURN result;
  ELSIF _program = 'providers' THEN
    DECLARE
      total_openings int; active_openings int; closed_openings int; new_openings int;
      companies_called int; jobs_active int; jobs_closed int; new_jobs_discussed int;
      active_providers int; new_jobs_posted int;
      funnel jsonb;
      p_called int; p_picked int; p_engaged int;
      o_called int; o_picked int; o_engaged int;
      c_active int; c_new_jobs int;
    BEGIN
      WITH base AS (
        SELECT
          phone,
          lower(COALESCE(call_status,'')) AS cs,
          call_duration_seconds AS dur,
          lower(COALESCE(job_status, data->'raw'->>'job_status','')) AS js,
          lower(COALESCE(new_job_posted, data->'raw'->>'new_job_posted','')) AS njp,
          lower(COALESCE(data->'raw'->>'new_job_mentioned','')) AS njm,
          COALESCE((regexp_match(COALESCE(data->'raw'->>'num_vacancies_input',''), '\d+'))[1]::int, 0) AS nvi,
          COALESCE((
            SELECT SUM((m[1])::int)
            FROM regexp_matches(COALESCE(data->'raw'->>'new_job_vacancies',''), '\d+', 'g') AS m
            WHERE (m[1])::int <= 500
          ), 0)::int AS njv,
          COALESCE(campaign_type,'') AS campaign_type,
          COALESCE(NULLIF(btrim(city_campaign), ''), 'Unknown') AS region_code,
          CASE
            WHEN COALESCE(campaign_date,'') ~ '^[0-9]{4,6}$'
              THEN (DATE '1899-12-30' + (campaign_date)::int)::date
            WHEN COALESCE(campaign_date,'') ~ '^\d{4}-\d{2}-\d{2}'
              THEN substring(campaign_date, 1, 10)::date
            WHEN COALESCE(data->>'call_datetime_ist','') ~ '^\d{4}-\d{2}-\d{2}'
              THEN substring(data->>'call_datetime_ist', 1, 10)::date
            ELSE NULL END AS call_date
        FROM public.call_rows_np WHERE program = 'providers' AND (_channel = 'all' OR channel = _channel)
      ),
      f AS (
        SELECT * FROM base
        WHERE (_state = 'all' OR region_code = _state)
          AND (_date_from IS NULL OR (call_date IS NOT NULL AND call_date >= _date_from))
          AND (_date_to   IS NULL OR (call_date IS NOT NULL AND call_date <= _date_to))
          AND (_campaign IS NULL OR campaign_type = _campaign)
      ),
      provider AS (
        SELECT
          phone,
          bool_or(cs LIKE 'answered%' OR cs = 'completed') AS picked,
          bool_or((cs LIKE 'answered%' OR cs = 'completed') AND COALESCE(dur,0) > 30) AS engaged,
          SUM(nvi)::int AS openings
        FROM f
        WHERE phone IS NOT NULL AND phone <> ''
        GROUP BY phone
      )
      SELECT
        COUNT(*)::int,
        COUNT(*) FILTER (WHERE cs LIKE 'answered%' OR cs = 'completed')::int,
        COUNT(*) FILTER (WHERE (cs LIKE 'answered%' OR cs = 'completed') AND COALESCE(dur,0) > 30)::int,
        COALESCE(AVG(dur) FILTER (WHERE cs LIKE 'answered%' OR cs = 'completed'), 0),
        COALESCE(SUM(nvi), 0)::int,
        COALESCE(SUM(nvi) FILTER (WHERE js = 'active'), 0)::int,
        COALESCE(SUM(nvi) FILTER (WHERE js = 'closed'), 0)::int,
        COALESCE(SUM(njv) FILTER (WHERE njp = 'yes'), 0)::int,
        COUNT(DISTINCT phone) FILTER (WHERE phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE js = 'active' AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(*) FILTER (WHERE js = 'closed')::int,
        COUNT(DISTINCT phone) FILTER (WHERE njm = 'yes' AND phone IS NOT NULL AND phone <> '')::int,
        COUNT(DISTINCT phone) FILTER (WHERE njp = 'yes' AND phone IS NOT NULL AND phone <> '')::int,
        (SELECT COUNT(*)::int FROM provider),
        (SELECT COUNT(*)::int FROM provider WHERE picked),
        (SELECT COUNT(*)::int FROM provider WHERE engaged),
        (SELECT COALESCE(SUM(openings),0)::int FROM provider),
        (SELECT COALESCE(SUM(openings),0)::int FROM provider WHERE picked),
        (SELECT COALESCE(SUM(openings),0)::int FROM provider WHERE engaged),
        COUNT(*) FILTER (WHERE js = 'active')::int,
        COUNT(*) FILTER (WHERE njp = 'yes')::int
      INTO total_calls, answered_calls, productive_calls, avg_dur,
           total_openings, active_openings, closed_openings, new_openings,
           companies_called, active_providers, jobs_closed, new_jobs_discussed,
           new_jobs_posted,
           p_called, p_picked, p_engaged,
           o_called, o_picked, o_engaged,
           c_active, c_new_jobs
      FROM f;

      jobs_active := active_providers;

      funnel := jsonb_build_array(
        jsonb_build_object('key','called','label','Called','providers', p_called, 'openings', o_called, 'calls', total_calls),
        jsonb_build_object('key','picked','label','Picked up','providers', p_picked, 'openings', o_picked, 'calls', answered_calls),
        jsonb_build_object('key','engaged','label','Engaged (30s+)','providers', p_engaged, 'openings', o_engaged, 'calls', productive_calls),
        jsonb_build_object('key','active','label','Actively hiring','providers', active_providers, 'openings', active_openings, 'calls', c_active),
        jsonb_build_object('key','new_jobs','label','New jobs posted','providers', new_jobs_posted, 'openings', new_openings, 'calls', c_new_jobs)
      );

      result := jsonb_build_object(
        'program','providers','totalCalls', total_calls,'answeredCalls', answered_calls,
        'unansweredCalls', GREATEST(total_calls - answered_calls, 0),
        'productiveCalls', productive_calls,'avgDuration', ROUND(avg_dur::numeric, 1),
        'totalOpenings', total_openings,'activeOpenings', active_openings,
        'closedOpenings', closed_openings,
        'unresolvedOpenings', GREATEST(total_openings - active_openings - closed_openings, 0),
        'newOpenings', new_openings,'companiesCalled', companies_called,
        'jobsActive', jobs_active,'jobsClosed', jobs_closed,
        'companiesUnresolved', GREATEST(companies_called - jobs_active - jobs_closed, 0),
        'newJobsDiscussed', new_jobs_discussed,
        'newJobsPosted', new_jobs_posted,
        'providerFunnel', funnel
      );
    END;
    RETURN result;
  END IF;
  RETURN '{}'::jsonb;
END;
$_$;

CREATE OR REPLACE FUNCTION public.pd_norm_stage(s text) RETURNS text
    LANGUAGE sql IMMUTABLE
    AS $$
  select case
    when s is null or btrim(s) = '' then null
    else (
      select case
        when n like '%connect to provider%'      then 'Connect to provider'
        when n like '%match provider%'            then 'Match provider'
        when n like '%summary & profile update%'  then 'Summary & profile update'
        when n like '%options delivery%'          then 'Options delivery & decision making'
        when n like '%needs & challenges%'        then 'Needs & challenges evaluation'
        when n like '%identify the disability%'   then 'Identify disability type'
        when n like '%profile completion%'        then 'Profile completion & verification'
        when n like '%silent profile fetch%'      then 'Profile fetch & name confirmation'
        else btrim(s)
      end
      from (select lower(regexp_replace(btrim(s), '^phase\s*[0-9]+\s*:\s*', '', 'i')) as n) t
    )
  end;
$$;

CREATE OR REPLACE FUNCTION public.touch_export_target_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public'
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.touch_launched_batch_inputs_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public'
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.upsert_app_user(_email text, _name text, _role text, _district text, _program text, _node_type text, _node_name text, _password text DEFAULT NULL::text, _active boolean DEFAULT true) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'public', 'extensions'
    AS $$
declare _id uuid;
begin
  insert into public.app_users (email, name, role, district, program, node_type, node_name, active, password_hash, updated_at)
  values (lower(btrim(_email)), nullif(_name,''), _role, nullif(_district,''), nullif(_program,''),
          nullif(_node_type,''), nullif(_node_name,''), _active,
          case when _password is null or _password = '' then null else crypt(_password, gen_salt('bf')) end, now())
  on conflict (email) do update set
    name = nullif(_name,''), role = _role, district = nullif(_district,''), program = nullif(_program,''),
    node_type = nullif(_node_type,''), node_name = nullif(_node_name,''), active = _active,
    password_hash = case when _password is null or _password = '' then public.app_users.password_hash else crypt(_password, gen_salt('bf')) end,
    updated_at = now()
  returning id into _id;
  return _id;
end $$;


-- views

CREATE OR REPLACE VIEW public.call_rows AS
 SELECT (md5(COALESCE(NULLIF(call_id, ''::text), NULLIF(call_uuid, ''::text), (call_datetime_ist)::text)))::uuid AS id,
        CASE
            WHEN (lower(COALESCE(persona, ''::text)) = ANY (ARRAY['provider'::text, 'providers'::text])) THEN 'providers'::text
            ELSE 'seekers'::text
        END AS program,
    NULL::uuid AS connection_id,
    COALESCE(NULLIF(call_id, ''::text), NULLIF(call_uuid, ''::text), ''::text) AS call_id,
    COALESCE((call_date_ist)::text, ''::text) AS campaign_day,
    call_value_score AS intent_score,
    jsonb_build_object('call_id', call_id, 'phone', COALESCE(NULLIF(contact_id, ''::text), call_id), 'campaign_day', (call_date_ist)::text, 'campaign_date', (call_date_ist)::text, 'campaign_type', campaign_name, 'city_campaign', NULL::text, 'language', NULL::text, 'call_language', NULL::text, 'call_datetime_ist', call_datetime_ist, 'call_duration_seconds', call_duration_seconds, 'call_answered', call_answered, 'call_engaged', call_engaged, 'call_outcome', call_status, 'drop_reason', NULLIF(btrim(drop_reason), ''::text), 'counselled', solution_enablers_discussed, 'applied_to_job', (COALESCE(providers_connected, 0) > 0), 'applications_count', providers_connected, 'tried_to_apply', connect_provider_api_triggered, 'jobs_recommended', matching_providers_found, 'jobs_shown', matching_providers_found, 'jobs_applied', providers_connected, 'jobs_failed_to_apply', NULL::text, 'interview_scheduled', NULL::text, 'Intent Score', call_value_score, 'Intent Score Reasoning', NULL::text, 'primary_topic', NULL::text, 'seeker_name', NULL::text, 'user_intent', NULL::text, 'final_summary', NULL::text, 'call_transcript', NULL::text, 'call_recording_url', NULL::text, 'raw', to_jsonb(c.*)) AS data,
    COALESCE(loaded_at, now()) AS synced_at,
    call_answered,
    call_engaged,
    (COALESCE(providers_connected, 0) > 0) AS applied_to_job,
    call_status,
    NULL::text AS job_status,
    NULL::text AS new_job_posted,
    NULL::text AS talent_insights_shown,
    public.pd_norm_stage(abandoned_at_stage) AS phases_reached,
    NULLIF(btrim(drop_reason), ''::text) AS drop_reason,
    call_status AS call_outcome,
    NULL::text AS city_campaign,
    (call_date_ist)::text AS campaign_date,
    COALESCE(NULLIF(campaign_name, ''::text), 'Unattributed'::text) AS campaign_type,
    NULL::text AS language,
    COALESCE(NULLIF(contact_id, ''::text), call_id) AS phone,
    call_duration_seconds,
    (providers_connected)::numeric AS applications_count,
    connect_provider_api_triggered AS tried_to_apply,
    COALESCE(NULLIF(channel, ''::text), 'outbound'::text) AS channel,
    md5((to_jsonb(c.*))::text) AS row_hash
   FROM public.purple_dots_calls c
  WHERE (COALESCE(test_flag, false) = false);

CREATE OR REPLACE VIEW public.call_rows_np AS
 SELECT id,
    program,
    connection_id,
    call_id,
    campaign_day,
    intent_score,
    data,
    synced_at,
    call_answered,
    call_engaged,
    applied_to_job,
    call_status,
    job_status,
    new_job_posted,
    talent_insights_shown,
    phases_reached,
    drop_reason,
    call_outcome,
    city_campaign,
    campaign_date,
    campaign_type,
    language,
    phone,
    call_duration_seconds,
    applications_count,
    tried_to_apply,
    channel,
    row_hash
   FROM public.call_rows
  WHERE false;

CREATE OR REPLACE VIEW public.kkb_grid AS
 SELECT campaign_day,
    campaign_date,
    campaign_type,
    language,
    call_id,
    phone,
    call_duration_seconds,
    (data ->> 'call_datetime_ist'::text) AS call_datetime_ist,
    call_outcome,
        CASE
            WHEN call_answered THEN 'Yes'::text
            ELSE 'No'::text
        END AS call_answered,
        CASE
            WHEN call_engaged THEN 'Yes'::text
            ELSE 'No'::text
        END AS call_engaged,
        CASE
            WHEN applied_to_job THEN 'Yes'::text
            ELSE 'No'::text
        END AS applied_to_job,
    applications_count,
        CASE
            WHEN (lower(COALESCE((data ->> 'jobs_shown'::text), ''::text)) = ANY (ARRAY['true'::text, 'yes'::text, '1'::text])) THEN 'Yes'::text
            ELSE 'No'::text
        END AS jobs_shown,
    (data ->> 'primary_topic'::text) AS primary_topic,
    COALESCE((data ->> 'call_language'::text), language) AS call_language,
    (data ->> 'call_recording_url'::text) AS call_recording_url,
    (data ->> 'final_summary'::text) AS final_summary,
    (data ->> 'call_transcript'::text) AS call_transcript,
        CASE
            WHEN tried_to_apply THEN 'Yes'::text
            ELSE 'No'::text
        END AS tried_to_apply,
    drop_reason,
    city_campaign,
    (data ->> 'seeker_name'::text) AS seeker_name,
    (data ->> 'user_intent'::text) AS user_intent,
    (data ->> 'jobs_recommended'::text) AS jobs_recommended,
    (data ->> 'jobs_applied'::text) AS jobs_applied,
    (data ->> 'jobs_failed_to_apply'::text) AS jobs_failed_to_apply,
    intent_score,
    (data ->> 'Intent Score Reasoning'::text) AS intent_score_reasoning
   FROM public.call_rows
  WHERE (program = 'seekers'::text);


-- grants: service_role, as in create_purple_dots.sql

grant select, insert, update, delete on public.app_users, public.campaign_requests, public.launched_batch_inputs, public.launched_batches, public.north_star_config, public.program_agents, public.program_export_targets, public.program_sync_state, public.reviewers, public.sheet_connections, public.transcript_reviews to service_role;

grant select on public.call_rows, public.call_rows_np, public.kkb_grid to service_role;
