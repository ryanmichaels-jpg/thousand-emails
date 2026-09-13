# thousand-emails seed data

Synthetic data in the shape of the thousand-emails project: 5,000 accounts, ~26,000 contacts,
eight months of 2026 outbound history, Gong calls, product usage, enrichment, and a `truth/` folder
that records everything the generator planted so the pipeline can be tested against it.

Nothing here is a real person or company. Every input is a distribution in `seed.yaml`.

## Run

    python generate.py                 # writes fixtures/ (deterministic per `seed`)
    DATABASE_URL=postgresql://... python load.py   # optional: load fixtures/ into Postgres as raw_* schemas

Change a number in `seed.yaml`, re-run, get a different world. Lines marked `ASSUMED` were not
specified and are placeholders to correct. `pave_job_families.yaml` is a placeholder for the public
list of roles Pave has data for.

## What's in fixtures/

| Folder | Files | Shape of |
|---|---|---|
| `salesforce/` | account, contact, contract, opportunity, opportunity_contact_role, event, task | Salesforce objects, field names as Salesforce would have them (`__c` for custom) |
| `outreach/` | sequence, prospect, sequence_state, mailing | Outreach API resources for the 2026 touched contacts |
| `enrichment/` | employment_history, org_shape, linkedin_activity, job_posting | What a vendor returns: work history keyed by `person_id`, headcount by function, LinkedIn signals, open roles |
| `bigquery/` | users, searches, datalab_queries | Free market-data product: users (60% matched to a contact), title searches with `US All` + one location and a valuation filter, raw Data Lab questions |
| `gong/` | call.csv, transcript.jsonl | Call metadata for 8 months; transcripts for the last 3 months, speaker turns with timestamps |
| `calendar/` | event | Booked meetings with attendees; ~10% are ambiguous (personal email, agency, subsidiary domain) for the meeting resolver |
| `benchmarks/` | customer_benchmark | Median headcount by function and size band: config value vs realized from generated customers |
| `truth/` | see below | The answer key |

## The answer key (`truth/`)

- `persona_size_lift.csv`: the hidden meeting lift per size band × persona that generated the 2026 meetings, plus touched/meeting counts and realized rates. The conversion-table job should recover the ordering (HR at small companies, TR/Comp at large) and should refuse to trust the sparse cells.
- `contact_persona.csv`: true persona, seniority and clean title for every contact, including the ones whose Title was typo'd. `persona_rules.yaml` is scored against this.
- `meeting_takers.csv`: which contact took the meeting for each 2026 NB opportunity.
- `past_customer_links.csv`: contacts planted with a prior job at a customer or former customer during its subscription, with evidence level. The overlap join should find all of them and nothing else.
- `planted_call_tags.csv`: every pain (family/subdivision), objection and competitor mention planted in a transcript, with the verbatim evidence quote and turn. Tagger precision/recall is measured here.
- `injected_mess.csv`: every imperfection injected (duplicates, title typos, domain mismatches, stale still-at-company flags, orphans, soft deletes, opted-out-but-sequenced, mid-year type changes). Each is a test: was it caught or handled?
  Both formerly-invisible traps are now planted with pipeline-visible signals: domain mismatches give the
  contact an email at a different account's domain (caught by the `email_domain_mismatch` shadow rule), and
  stale still-at-company contacts get an end-dated account position plus a newer current employer in
  `enrichment/employment_history.csv` (caught by the `left_per_vendor` shadow rule).
- `summary.json`, `seed.yaml`: counts and the config that produced them.

## Things the generator gets deliberately right

- Contacts per account are fixed by size band (1 / 3 / 5 / 7 / 11) as specified.
- Persona mix shifts with size on both sides: who exists at the company, and who takes the meeting. The two are different distributions, so the naive "share of meetings" table is biased and the rate table is not.
- Accounts are snake-drafted to two SDRs by account score. Customers and former customers are AE-owned.
- Contract dates live on a Contract object; churned accounts get an end date and reason; a few customers signed in 2026 and were prospects on Jan 1.
- Free product usage clusters in the 1–50 and 51–200 bands.
- Job postings at tech companies are mostly in covered families; elsewhere it's about half.
- Cooldowns are testable: sequences end at dates spread across the year, including inside the last 60 days.
- Transcripts are template-based (no API cost, fully deterministic). Every planted line is a verbatim prospect turn, so the mechanical evidence-quote check has ground truth. Swap in Claude-generated transcripts later by replacing `gen_gong`; keep the planted-line contract.

## Known simplifications

- 2026 touched volume (30% of eligible prospects) is a knob, not a measurement. Set `history.touched_share_of_contacts` to match reality.
- Reply, open and meeting rates are flat except for persona and seniority lift; no play or mode effects exist yet in history because the system doesn't exist yet in 2026.
- Employment history for non-planted prior jobs uses invented companies not in the account table.
- Gong calls are linked to contacts directly; real Gong links through email/CRM ids, which the pipeline should resolve rather than trust.
