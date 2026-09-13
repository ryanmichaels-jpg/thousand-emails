-- thousand-emails: application schema. raw_* schemas come from seed/load.py (or the real sync);
-- models.* comes from dbt. This file is the current full state; changes also go in db/migrations/.
-- create extension if not exists vector;  -- enable when pgvector is installed for this Postgres
create schema if not exists app;
set search_path = app, public;

-- ---------------------------------------------------------------- people using the system
create table if not exists users (
  id            text primary key,              -- 'sdr_1', 'manager_1' in fixtures; OIDC subject in prod
  email         text unique not null,
  name          text,
  role          text not null check (role in ('rep','manager','admin')),
  password_hash text,                          -- local auth only; null under OIDC
  active        boolean not null default true,
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------- facts with provenance
create table if not exists account_fact (
  id         bigserial primary key,
  account_id text not null,
  field      text not null,                    -- e.g. 'headcount', 'open_roles_covered', 'competitor_mentioned'
  value      text not null,
  evidence   text,                             -- verbatim excerpt where one exists (job post, call quote)
  source     text not null,                    -- 'salesforce','enrichment','job_post','gong','bigquery','linkedin'
  as_of      date not null,
  created_at timestamptz not null default now()
);
create index if not exists account_fact_lookup on account_fact (account_id, field, as_of desc);

create table if not exists contact_fact (
  id         bigserial primary key,
  contact_id text not null,
  field      text not null,                    -- 'persona','seniority','li_activity_score','past_customer', ...
  value      text not null,
  evidence   text,
  source     text not null,
  as_of      date not null,
  created_at timestamptz not null default now()
);
create index if not exists contact_fact_lookup on contact_fact (contact_id, field, as_of desc);

-- ---------------------------------------------------------------- rules of engagement, computed nightly
create table if not exists exclusion (
  contact_id  text not null,
  account_id  text,
  reason      text not null,                   -- 'customer','open_opp','ae_owned','touched_60d','bounced','unsubscribed','dnc','opted_out','no_email','not_at_company','cooldown','competitor_partner'
  source      text not null,
  action      text not null default 'exclude', -- 'exclude' | 'shadow' (logged, not enforced)
  computed_at timestamptz not null default now(),
  primary key (contact_id, reason)
);

create table if not exists contact_touch_state (
  contact_id        text primary key,
  last_sequence_end date,
  last_rep          text,
  last_outcome      text,                      -- 'no_response','negative','positive','bounced','unsubscribed'
  eligible_after    date,
  updated_at        timestamptz not null default now()
);

-- ---------------------------------------------------------------- scoring
create table if not exists title_classification (
  title         text primary key,
  persona       text not null,
  seniority     text not null,
  method        text not null check (method in ('rule', 'llm')),
  model         text,                            -- null for rule matches
  rules_version text not null,
  classified_at timestamptz not null default now()
);

create table if not exists score_history (
  id              bigserial primary key,
  entity_type     text not null check (entity_type in ('account','contact')),
  entity_id       text not null,
  as_of           date not null,
  fit             numeric,
  pulse           numeric,
  contact_score   numeric,
  li_score        numeric,
  rank_score      numeric,
  rules_version   text not null,
  computed_at     timestamptz not null default now()
);
create index if not exists score_history_lookup on score_history (entity_type, entity_id, as_of desc);

create table if not exists persona_size_lift (
  rules_version text not null,
  size_band     text not null,
  persona       text not null,
  touched       integer not null,
  meetings      integer not null,
  raw_rate      numeric,
  smoothed_lift numeric not null,
  trusted       boolean not null,              -- false when the cell is under the minimum count
  computed_at   timestamptz not null default now(),
  primary key (rules_version, size_band, persona)
);

-- ---------------------------------------------------------------- patches, tiers, enrollment
create table if not exists patch (
  id           text primary key,
  rep_id       text not null references users(id),
  period_start date not null,
  period_end   date not null,
  cut_at       timestamptz not null default now(),
  rules_version text not null
);

create table if not exists patch_member (
  patch_id          text not null references patch(id),
  contact_id        text not null,
  account_id        text not null,
  rank              integer not null,
  rank_score        numeric not null,
  recommended_tier  text not null check (recommended_tier in ('A','B','C','call_only','reenrich')),
  recommended_play  text,
  flags             jsonb not null default '{}'::jsonb,   -- {email_status, phone_type, li_score, past_customer, ...}
  enroll_day        date,                                  -- which daily slice of 200
  primary key (patch_id, contact_id)
);

create table if not exists tier_decision (
  id          bigserial primary key,
  patch_id    text not null,
  contact_id  text not null,
  user_id     text not null references users(id),
  decision    text not null check (decision in ('accept','override','skip')),
  tier        text,
  reason      text,
  decided_at  timestamptz not null default now()
);

create table if not exists enrollment (
  id                   text primary key,
  patch_id             text not null,
  contact_id           text not null,
  sequence_id          text not null,           -- from contracts/sequences.yaml, mirrored in Outreach
  play                 text not null,
  mode                 smallint not null check (mode between 1 and 4),
  outreach_prospect_id text,
  outreach_state_id    text,
  enrolled_by          text not null references users(id),
  enrolled_at          timestamptz not null default now(),
  exclusion_checked_at timestamptz not null,
  unique (contact_id, sequence_id, patch_id)
);

-- ---------------------------------------------------------------- the decision record
create table if not exists draft (
  id                text primary key,
  enrollment_id     text references enrollment(id),
  contact_id        text not null,
  account_id        text not null,
  rep_id            text not null,
  play              text not null,
  step              smallint not null,            -- 1..5
  thread            text not null check (thread in ('A','B')),
  mode              smallint not null,
  source            text not null,                -- prospect source: 'patch','inbound','reactivation'
  skill_version     text not null,
  prompt_hash       text not null,
  model             text not null,
  inputs_read       jsonb not null,               -- [{table, id, field, as_of}] every fact the draft saw
  fitness_result    jsonb not null,               -- {ok, missing:[], stale:[]}
  exclusion_result  jsonb not null,               -- {ok, reasons:[]}
  subject           text,
  body              text not null,
  status            text not null default 'pending' check (status in ('pending','released','edited','dismissed','sent','blocked')),
  created_at        timestamptz not null default now()
);
create index if not exists draft_queue on draft (rep_id, status, play, step);

create table if not exists human_action (
  id             bigserial primary key,
  draft_id       text not null references draft(id),
  user_id        text not null references users(id),
  action         text not null check (action in ('release','edit','dismiss')),
  edited_body    text,
  edit_distance  integer,
  reason         text,                            -- dismiss: wrong_fact | wrong_tone | wrong_timing | in_conversation | other
  acted_at       timestamptz not null default now()
);

create table if not exists send (
  id          text primary key,
  draft_id    text not null references draft(id),
  path        text not null check (path in ('outreach','pool','stub')),
  mailbox     text,
  message_id  text,
  sent_at     timestamptz not null,
  outreach_mailing_id text
);

create table if not exists outcome_event (
  id          bigserial primary key,
  event       text not null check (event in ('open','click','reply','positive_reply','negative_reply','ooo','referral','meeting','opportunity','unsubscribe','complaint','bounce')),
  draft_id    text references draft(id),
  send_id     text references send(id),
  contact_id  text,
  account_id  text,
  observed_at timestamptz not null,
  source      text not null,                      -- 'outreach','pool','calendar','salesforce','stub'
  detail      jsonb not null default '{}'::jsonb
);
create index if not exists outcome_by_draft on outcome_event (draft_id);

-- ---------------------------------------------------------------- calls
create table if not exists call (
  id            text primary key,                 -- gong call id
  call_type     text,
  started_at    timestamptz not null,
  duration_min  integer,
  rep_id        text,
  contact_id    text,
  account_id    text,
  resolved_by   text,                             -- 'crm_id','email','name_domain','unresolved'
  has_transcript boolean not null default false
);

create table if not exists call_tag (
  id             bigserial primary key,
  call_id        text not null references call(id),
  family         text not null,                   -- benchmarking|planning|repricing|communicating|objection|competitor|persona|outcome|next_step
  value          text not null,
  evidence_quote text not null,
  speaker        text,
  turn           integer,
  ts_start_s     integer,
  confidence     numeric,
  tagger_version text not null,                   -- taxonomy version + prompt hash + model
  check_result   jsonb not null,                  -- {quote_verbatim, in_vocab, schema_ok}
  status         text not null default 'ok' check (status in ('ok','quarantined','human_fixed')),
  created_at     timestamptz not null default now()
);
create index if not exists call_tag_lookup on call_tag (call_id, family);

create table if not exists transcript_chunk (
  id        bigserial primary key,
  call_id   text not null references call(id),
  turn_from integer not null,
  turn_to   integer not null,
  text      text not null,
  embedding real[]  -- switch to vector(1024) once pgvector is installed
);

-- ---------------------------------------------------------------- customers and alumni
create table if not exists customer_timeline (
  account_id   text not null,
  period_start date not null,
  period_end   date,
  status       text not null,                     -- 'active','churned'
  churn_reason text,
  source       text not null,                     -- 'salesforce_contract','bigquery_activity'
  primary key (account_id, period_start, source)
);

create table if not exists past_customer_link (
  contact_id        text not null,
  former_account_id text not null,
  overlap_start     date,
  overlap_end       date,
  evidence_level    text not null check (evidence_level in ('used_product','on_deal','employed_during')),
  confidence        numeric not null,
  computed_at       timestamptz not null default now(),
  primary key (contact_id, former_account_id, evidence_level)
);

-- ---------------------------------------------------------------- sync and agent logs
create table if not exists sync_state (
  source     text not null,
  object     text not null,
  watermark  timestamptz,
  last_run   timestamptz,
  last_ok    boolean,
  primary key (source, object)
);

create table if not exists sync_health (
  id           bigserial primary key,
  run_at       timestamptz not null default now(),
  source       text not null,
  object       text not null,
  rows_local   bigint,
  rows_remote  bigint,
  lag_minutes  integer,
  null_rates   jsonb,
  schema_drift jsonb,
  ok           boolean not null
);

create table if not exists agent_action (
  id          bigserial primary key,
  user_id     text not null references users(id),
  session_id  text not null,
  tool        text not null,
  args        jsonb not null,
  result_ok   boolean not null,
  result_ref  text,                               -- draft id, enrollment id, etc.
  acted_at    timestamptz not null default now()
);

-- seed users for the demo (password: 'demo', hashed by api/auth on first run if null)
insert into users (id, email, name, role) values
  ('sdr_1', 'sdr1@example.com', 'Rep One', 'rep'),
  ('sdr_2', 'sdr2@example.com', 'Rep Two', 'rep'),
  ('manager_1', 'manager@example.com', 'SDR Manager', 'manager'),
  ('admin_1', 'admin@example.com', 'RevOps Admin', 'admin')
on conflict (id) do nothing;
