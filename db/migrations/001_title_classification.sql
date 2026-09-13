-- Cache of title -> persona/seniority so each distinct title is classified once
-- (rules or LLM fallback) and the LLM is never asked twice for the same string.
set search_path = app, public;

create table if not exists title_classification (
  title         text primary key,
  persona       text not null,
  seniority     text not null,
  method        text not null check (method in ('rule', 'llm')),
  model         text,                            -- null for rule matches
  rules_version text not null,
  classified_at timestamptz not null default now()
);
