"""Persona x size-band lift learned from 2026 new-business history.

touched  = distinct contacts that entered any Outreach sequence in the window
meetings = distinct (opportunity, contact) pairs from OpportunityContactRole plus intro Events,
           restricted to 2026 New Business opportunities
rate     = meetings / touched per (band, persona) cell, shrunk toward the pooled persona rate with
           weight SHRINK_K, expressed as a lift over the overall rate. Cells with fewer than
           MIN_TOUCHED touched are written trusted=false and never used for scoring.
A pooled seniority lift is computed from the same history (bands pooled; every seniority clears
MIN_TOUCHED by a wide margin) and returned for the contact score.
"""
from __future__ import annotations

import psycopg

MIN_TOUCHED = 30
SHRINK_K = 30

_TOUCHED_CELLS = """
with persona as (
  select distinct on (contact_id) contact_id, value
  from contact_fact where field = %(field)s
  order by contact_id, as_of desc, id desc
),
touched as (
  select distinct p.contact_id
  from raw_outreach.sequence_state ss
  join raw_outreach.prospect p on p.id = ss.prospect_id
)
select a."HeadcountBand__c", pe.value, count(*)
from touched t
join raw_salesforce.contact c on c."Id" = t.contact_id
join raw_salesforce.account a on a."Id" = c."AccountId"
join persona pe on pe.contact_id = t.contact_id
group by 1, 2
"""

_MEETING_CELLS = """
with persona as (
  select distinct on (contact_id) contact_id, value
  from contact_fact where field = %(field)s
  order by contact_id, as_of desc, id desc
),
nb_opp as (
  select "Id" from raw_salesforce.opportunity
  where "Type" = 'New Business' and "CreatedDate" >= '2026-01-01'
),
pair as (
  select "OpportunityId" as opp_id, "ContactId" as contact_id from raw_salesforce.opportunity_contact_role
  union
  select "WhatId", "WhoId" from raw_salesforce.event
)
select a."HeadcountBand__c", pe.value, count(*)
from pair
join nb_opp on nb_opp."Id" = pair.opp_id
join raw_salesforce.contact c on c."Id" = pair.contact_id
join raw_salesforce.account a on a."Id" = c."AccountId"
join persona pe on pe.contact_id = pair.contact_id
group by 1, 2
"""


def _cells(cur: psycopg.Cursor, field: str) -> tuple[dict, dict]:
    cur.execute(_TOUCHED_CELLS, {"field": field})
    touched = {(band, value): n for band, value, n in cur.fetchall()}
    cur.execute(_MEETING_CELLS, {"field": field})
    meetings = {(band, value): n for band, value, n in cur.fetchall()}
    return touched, meetings


def _pooled(touched: dict, meetings: dict) -> tuple[dict[str, float], float]:
    by_value: dict[str, list[int]] = {}
    for (band, value), n in touched.items():
        agg = by_value.setdefault(value, [0, 0])
        agg[0] += n
        agg[1] += meetings.get((band, value), 0)
    total_touched = sum(v[0] for v in by_value.values())
    total_meetings = sum(v[1] for v in by_value.values())
    overall = total_meetings / total_touched
    return {v: m / t for v, (t, m) in by_value.items()}, overall


def build_persona_lift(cur: psycopg.Cursor, rules_version: str) -> dict[tuple[str, str], dict]:
    touched, meetings = _cells(cur, "persona")
    pooled_rate, overall = _pooled(touched, meetings)
    cur.execute("delete from persona_size_lift where rules_version = %s", (rules_version,))
    table: dict[tuple[str, str], dict] = {}
    bands = sorted({b for b, _ in touched})
    personas = sorted({p for _, p in touched})
    for band, persona in ((b, p) for b in bands for p in personas):
        t = touched.get((band, persona), 0)
        m = meetings.get((band, persona), 0)
        smoothed = (m + SHRINK_K * pooled_rate[persona]) / (t + SHRINK_K)
        cell = {"touched": t, "meetings": m, "raw_rate": m / t if t else None,
                "smoothed_lift": smoothed / overall, "trusted": t >= MIN_TOUCHED}
        table[(band, persona)] = cell
        cur.execute(
            """insert into persona_size_lift
               (rules_version, size_band, persona, touched, meetings, raw_rate, smoothed_lift, trusted)
               values (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (rules_version, band, persona, t, m, cell["raw_rate"], round(cell["smoothed_lift"], 4), cell["trusted"]))
    pooled_lift = {p: r / overall for p, r in pooled_rate.items()}
    print(f"persona_size_lift: {len(table)} cells, {sum(1 for c in table.values() if not c['trusted'])} untrusted "
          f"(< {MIN_TOUCHED} touched), overall rate {overall:.4f}")
    return table, pooled_lift


def build_seniority_lift(cur: psycopg.Cursor) -> dict[str, float]:
    touched, meetings = _cells(cur, "seniority")
    pooled_rate, overall = _pooled(touched, meetings)
    return {s: r / overall for s, r in pooled_rate.items()}
