"""Contact scores -> app.score_history.

Gates first (still-at-company, email present, not opted out): a gated contact gets a history row so
the run is auditable, but null contact_score and null rank_score. Then
contact_score = persona lift (band cell if trusted, else the pooled persona lift)
              x seniority lift (pooled)
              x engagement (opens/clicks/positive replies/product usage, multiplicative bumps)
rank_score   = account_score/100 x contact_score
li_score     = LinkedIn activity in [0, 1], written for every contact that has activity data.
"""
from __future__ import annotations

from datetime import UTC, datetime

import psycopg

ENGAGEMENT = {"opened": 1.1, "clicked": 1.2, "replied_positive": 1.5, "product_user": 1.3}

_CONTACTS = """
with persona as (
  select distinct on (contact_id) contact_id, value
  from contact_fact where field = 'persona' order by contact_id, as_of desc, id desc
),
seniority as (
  select distinct on (contact_id) contact_id, value
  from contact_fact where field = 'seniority' order by contact_id, as_of desc, id desc
),
eng as (
  select p.contact_id, bool_or(m.opened = 'true') as opened, bool_or(m.clicked = 'true') as clicked
  from raw_outreach.mailing m join raw_outreach.prospect p on p.id = m.prospect_id
  group by 1
),
rep as (
  select p.contact_id, bool_or(ss.reply_kind in ('positive', 'referral')) as replied_positive
  from raw_outreach.sequence_state ss join raw_outreach.prospect p on p.id = ss.prospect_id
  group by 1
),
bq as (
  select distinct matched_contact_id as contact_id from raw_bigquery.users where matched_contact_id <> ''
),
li as (
  select contact_id, posts_90d::int as posts, comments_30d::int as comments, last_active_days::int as last_active
  from raw_enrichment.linkedin_activity
)
select c."Id", pe.value, se.value, a."HeadcountBand__c", a."AccountScore__c"::numeric,
       c."StillAtCompany__c" = 'true' and c."Email" <> '' and c."HasOptedOutOfEmail" = 'false' as passes_gates,
       coalesce(eng.opened, false), coalesce(eng.clicked, false), coalesce(rep.replied_positive, false),
       bq.contact_id is not null, li.posts, li.comments, li.last_active
from raw_salesforce.contact c
left join raw_salesforce.account a on a."Id" = c."AccountId"
left join persona pe on pe.contact_id = c."Id"
left join seniority se on se.contact_id = c."Id"
left join eng on eng.contact_id = c."Id"
left join rep on rep.contact_id = c."Id"
left join bq on bq.contact_id = c."Id"
left join li on li.contact_id = c."Id"
where c."IsDeleted" = 'false'
"""


def _li_score(posts: int | None, comments: int | None, last_active: int | None) -> float | None:
    if posts is None:
        return None
    score = min(posts, 12) / 12 * 0.5 + min(comments or 0, 20) / 20 * 0.3 + (0.2 if (last_active or 999) <= 30 else 0)
    return round(min(score, 1.0), 4)


def compute_scores(cur: psycopg.Cursor, persona_lift: dict, pooled_persona_lift: dict,
                   seniority_lift: dict, rules_version: str) -> dict[str, int]:
    today = datetime.now(UTC).date()
    cur.execute(_CONTACTS)
    rows = cur.fetchall()
    cur.execute("delete from score_history where entity_type = 'contact' and as_of = %s", (today,))
    n_gated = n_ranked = 0
    with cur.copy("copy score_history (entity_type, entity_id, as_of, contact_score, li_score, rank_score, rules_version) from stdin") as cp:
        for cid, persona, seniority, band, ascore, passes, opened, clicked, replied, bq_user, posts, comments, last_active in rows:
            li = _li_score(posts, comments, last_active)
            contact_score = rank_score = None
            if passes and persona:
                cell = persona_lift.get((band, persona))
                plift = cell["smoothed_lift"] if cell and cell["trusted"] else pooled_persona_lift.get(persona, 1.0)
                slift = seniority_lift.get(seniority, 1.0)
                engagement = 1.0
                for flag, mult in zip((opened, clicked, replied, bq_user), ENGAGEMENT.values()):
                    if flag:
                        engagement *= mult
                contact_score = round(plift * slift * engagement, 4)
                if ascore is not None:
                    rank_score = round(float(ascore) / 100 * contact_score, 4)
                    n_ranked += 1
            else:
                n_gated += passes is False
            cp.write_row(("contact", cid, today, contact_score, li, rank_score, rules_version))
    counts = {"scored": len(rows), "gated": n_gated, "ranked": n_ranked, "as_of": today}
    print(f"score_history: {counts['scored']} contacts written, {counts['gated']} gated (no score), "
          f"{counts['ranked']} with rank_score")
    return counts
