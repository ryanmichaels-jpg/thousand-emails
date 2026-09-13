-- Per-rep voice: versioned profiles applied between the play skill and batch custom instructions,
-- and the example emails they are built from. Drafts record which voice version shaped them.
set search_path = app, public;

create table if not exists voice_profile (
  id          text primary key,
  rep_id      text not null references users(id),
  version     integer not null,
  profile_md  text not null,
  dos         jsonb not null default '[]',
  donts       jsonb not null default '[]',
  approved_at timestamptz,                        -- null until the rep saves/approves; every save is a new version
  created_at  timestamptz not null default now(),
  unique (rep_id, version)
);

create table if not exists voice_example (
  id         text primary key,
  rep_id     text not null references users(id),
  source     text not null check (source in ('sent', 'pasted', 'written')),
  body       text not null,
  replied    boolean,                             -- did this sent email get a reply (weighting signal); null for pasted/written
  included   boolean not null default true,
  weight     numeric not null default 1,
  created_at timestamptz not null default now()
);

alter table draft add column if not exists voice_version integer;
