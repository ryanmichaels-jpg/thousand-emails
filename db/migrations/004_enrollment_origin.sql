-- Origin is recorded automatically at enrollment (there is no source field in the enroll modal):
-- which surface the enrollment came from.
set search_path = app, public;

alter table enrollment add column if not exists origin text not null default 'patch'
  check (origin in ('patch', 'accounts', 'signals', 'feed', 'home'));
