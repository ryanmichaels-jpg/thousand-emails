# thousand-emails

A working MVP of an outbound system where one SDR releases 1,000 tailored emails a day, built so it can be
demoed end-to-end on synthetic data and plugged into real sources (Salesforce, Outreach, Gong, BigQuery,
an enrichment vendor) by swapping adapters. Shape is modeled on what Ramp has described publicly about
their internal system; nothing here describes Ramp's internals.

This file is the project's memory. Read it before doing anything. Keep it current when a decision changes.

## The one-paragraph design

Salesforce is the system of record; Outreach keeps sequence definitions and call/LinkedIn tasks; everything
else lives in one Postgres (schemas `raw_*` per source, `models` via dbt, `app` for the product). A sync
copies sources into `raw_*`. dbt builds `models`. The app cuts a **patch** of ~2,000 contacts per rep every
two weeks, enrolls 200/day in **tiered sequences** (A: email+call+LI, B: email+call, C: email only), drafts
emails per **play** from a **facts table with provenance**, records every draft in a **decision record**,
shows mode 1–2 first touches in a **Queue** for release, sends through Outreach custom fields (path A) or a
mailbox pool (path B), and joins every send to its **outcome**. Gong transcripts are **tagged** against a
closed taxonomy, mechanically checked, and become facts. A **Claude wrapper** (Agent SDK, org API key) is a
second client on the same MCP tools.

## Non-negotiable rules

1. **Adapter rule.** Nothing imports a vendor client directly. Every external system is an interface in
   `api/adapters/base.py` with a `Fixture*` implementation (reads `seed/fixtures/`) and a `Real*` one.
   `config/sources.yaml` picks which. If you need a vendor call, add it to the interface first.
2. **Decision record.** Every draft writes a row to `app.draft` with inputs_read, skill_version, prompt_hash,
   model, fitness and exclusion results. Every human action writes `app.human_action`. Every tool call from
   the wrapper writes `app.agent_action`. No exceptions, no "later".
3. **Guardrails live in tools, not prompts.** Exclusions and rules of engagement are enforced in the
   enrollment path and in every MCP tool. A prompt can never override them.
4. **Contracts are files.** `contracts/exclusions.yaml`, `taxonomy.yaml`, `persona_rules.yaml`,
   `sequences.yaml`, `guardrails/email.yaml`. Code reads them; humans edit them; every change is a commit.
5. **Provenance on every fact.** `account_fact` / `contact_fact` rows carry `source` and `as_of`. Drafts cite
   only facts fresher than the play's freshness rule.
6. **No real PII in this repo, ever.** Fixtures are synthetic. `.env` is gitignored. Real credentials never
   appear in code, tests, or fixtures.
7. **Reproducible.** `make seed` regenerates the world from `seed/seed.yaml`. `seed/fixtures/` is not
   committed. `tests/test_seed.py` pins the reference counts.
8. **Versioned scoring.** Every scoring run writes `app.score_history`. Rules files carry a `version`.

## Locked decisions (from planning)

- Cadence: 10 business days. Emails on days 1,3,5 (thread A) and 8,10 (thread B). Calls on days 2,4,6,7,9.
  LinkedIn connect day 2–3, message on accept, follow-up ~day 9. See `contracts/sequences.yaml`.
- Volume: 1,000 emails/day/rep = 200 new enrollments/day = 2,000-contact patch per rep per two weeks.
- Tiers per patch of 2,000: ~200 tier A (LinkedIn-bound, ~20 connects/day), ~100 more tier B (dials-bound,
  ~150/day), rest tier C. Tier is a recommendation; overrides are logged with a reason. Engagement promotes.
- Contact score = persona × seniority lift (learned from 2026 NB opps by size band, smoothed) × engagement;
  gates before scoring: still-at-company, email present, HasOptedOutOfEmail=false. DoNotCall gates calls.
  rank = account_score × contact_score.
- Cooldowns: 60 days after a no-response sequence; 180 after a negative reply; permanent after unsubscribe.
  One rep per account at a time; same rep gets the account back after rest.
- No-email contacts: out of the email pool; call-only pool if direct dial + tier A score; else re-enrichment queue.
- Review: only mode 1–2 first touches go to the Queue. Modes 3–4 start at send-with-undo.
- Sender: path A (Outreach custom fields custom10–14, written the night before each step) first; path B
  (mailbox pool, ~25 mailboxes/rep across ~10 secondary domains) when volume demands. See `docs/`.
- Sequences are built centrally in Outreach; reps cannot create their own.
- Gong tagging: one LLM pass per call against `contracts/taxonomy.yaml`, structured output with verbatim
  evidence quotes; mechanical check (quote verbatim, value in vocab, schema); golden set + weekly 3% sample.
- Auth: `api/auth/` has a local users implementation (seeded `sdr_1`, `sdr_2`, `manager`) and an OIDC stub for
  Google Workspace. Roles: rep, manager, admin. Every write carries user_id.
- Wrapper: Claude Agent SDK app with the org API key (reps need no seats), same MCP tools as the web app.

## How to run

    make seed        # regenerate seed/fixtures from seed/seed.yaml
    make up          # postgres (pgvector) + api
    make load        # load fixtures into raw_* schemas
    make schema      # apply db/schema.sql (app schema)
    make test        # pytest
    make demo        # seed + up + load + schema + score + patch + draft 50 + open the Queue

## Build stages (each has a demo)

1. Foundation: schema, dbt models, fixture adapters, facts, exclusions, score_history.
2. Decisions: scores, persona lift table (recover `truth/persona_size_lift.csv` ordering), patch cutter,
   tiers, enrollment path (idempotent, exclusion re-check), reconciliation job.
3. Draft engine: two plays, modes 2 and 4, fitness check, decision record, golden set + eval runner.
4. Gong tagger: pull, tag, check, `call_tag`, facts with source=gong, golden set from `truth/planted_call_tags.csv`.
5. App: Queue (first), then Patch, then Brief. Login in front. Every action through the API.
6. Loop: stub sender, simulated outcomes, `outcome_event`, meeting resolver, impact dashboard with min cell sizes.
7. Wrapper: Agent SDK app, MCP tools, per-rep identity, tool-call logging.

## Conventions

- Python 3.11, FastAPI, psycopg 3, pydantic. `ruff` for lint. Tests with pytest. Type hints everywhere.
- Web: React + Vite + TypeScript. No component library that fights the design; keyboard-first Queue.
- SQL: schema changes are numbered files in `db/migrations/`; `db/schema.sql` is the current full state.
- Names: tables singular (`draft`, `send`), ids are text, times are `timestamptz`, dates are `date`.
- Commits: small, one concern each. Branch per stage, PR to `main`.
- When a decision changes, edit this file in the same PR.

## What the truth/ folder is for

`seed/fixtures/truth/` is the answer key. Tests assert: exclusions catch every injected trap; the persona
lift job recovers the hidden ordering and refuses sparse cells; the past-customer join finds every planted
link and nothing else; the tagger's precision/recall on planted tags; no excluded contact is ever drafted;
every draft has a decision record; enrollment is idempotent; Postgres reconciles with the Outreach fixture.
