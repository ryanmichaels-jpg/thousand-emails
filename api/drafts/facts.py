"""Account facts with provenance, built nightly from raw_*. Rule 5 of the repo: every row carries
source and as_of, and drafts may only cite facts fresher than the play's freshness rule -- so as_of
here is the date of the underlying signal, not the run date."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

FIELDS = ("headcount", "open_roles_covered", "new_exec_hire")


def build_account_facts(cur: psycopg.Cursor) -> dict[str, int]:
    today = datetime.now(UTC).date()
    cur.execute("delete from account_fact where field = any(%s)", (list(FIELDS),))
    counts: dict[str, int] = {}

    cur.execute("""insert into account_fact (account_id, field, value, source, as_of)
                   select "Id", 'headcount', "NumberOfEmployees", 'salesforce', %s
                   from raw_salesforce.account where "IsDeleted" = 'false'""", (today,))
    counts["headcount"] = cur.rowcount

    cur.execute("""insert into account_fact (account_id, field, value, evidence, source, as_of)
                   select account_id, 'open_roles_covered', count(*)::text,
                          min(title) || ' and ' || (count(*) - 1) || ' more', 'job_post', max(posted_at)::date
                   from raw_enrichment.job_posting
                   where dataset_covered = 'true' and posted_at >= %s
                   group by account_id""", ((today - timedelta(days=45)).isoformat(),))
    counts["open_roles_covered"] = cur.rowcount

    cur.execute("""
        insert into account_fact (account_id, field, value, evidence, source, as_of)
        select distinct on (c."AccountId") c."AccountId", 'new_exec_hire', e.title,
               e.title || ' started ' || e.start, 'enrichment', e.start::date
        from raw_enrichment.employment_history e
        join raw_salesforce.contact c on c."Id" = e.contact_id and c."IsDeleted" = 'false'
        join raw_salesforce.account a on a."Id" = c."AccountId"
        join contact_fact p on p.contact_id = c."Id" and p.field = 'persona' and p.value = 'FIN'
        join contact_fact s on s.contact_id = c."Id" and s.field = 'seniority' and s.value in ('C', 'VP')
        where e.is_current = 'true' and lower(e.company_domain) = lower(a."Domain") and e.start >= %s
        order by c."AccountId", e.start desc""", ((today - timedelta(days=90)).isoformat(),))
    counts["new_exec_hire"] = cur.rowcount
    return counts
