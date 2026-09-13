"""Nightly rules-of-engagement jobs: contact_touch_state (cooldowns) then exclusion rows.

Every rule in contracts/exclusions.yaml with an implementation here produces exclusion rows with a
reason; the contract decides the action (exclude vs shadow). Guardrail rule of the repo: these rows
are enforced in the patch cutter and re-checked in the enrollment path, never in prompts.

Traps this catches from the fixture world: opted_out_but_in_sequence -> opted_out, orphan_contact ->
unresolved_identity. stale_still_at_company and domain_mismatch are NOT catchable from current
sources (the seed plants no enrichment/verification signal for them); they wait on the
re-enrichment/verification loop.

Usage:  python -m api.enroll.jobs
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta

import psycopg
import yaml
from dotenv import load_dotenv

from api.adapters import get_adapter

CONTRACT = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "exclusions.yaml")
PERMANENT = date(9999, 12, 31)

NEGATIVE_KINDS = ("not_interested",)
POSITIVE_KINDS = ("positive", "referral")


def load_contract() -> dict:
    with open(CONTRACT) as f:
        return yaml.safe_load(f)


def run_touch_state(cur: psycopg.Cursor) -> int:
    cooldowns = load_contract()["cooldowns"]
    cur.execute("""select p.contact_id, ss.state, ss.reply_kind, ss.started_at, ss.finished_at, ss.owner
                   from raw_outreach.sequence_state ss
                   join raw_outreach.prospect p on p.id = ss.prospect_id
                   order by p.contact_id, coalesce(nullif(ss.finished_at, ''), ss.started_at)""")
    latest: dict[str, tuple] = {row[0]: row for row in cur.fetchall()}  # last row per contact wins
    cur.execute("delete from contact_touch_state")
    n = 0
    with cur.copy("copy contact_touch_state (contact_id, last_sequence_end, last_rep, last_outcome, eligible_after) from stdin") as cp:
        for contact_id, (_, state, reply_kind, started_at, finished_at, owner) in latest.items():
            end = date.fromisoformat(finished_at or started_at) if (finished_at or started_at) else None
            if state == "active":
                cp.write_row((contact_id, None, owner or None, None, None))
                n += 1
                continue
            if state == "bounced":
                outcome, eligible = "bounced", None
            elif reply_kind == "unsubscribe":
                outcome, eligible = "unsubscribed", PERMANENT
            elif reply_kind in NEGATIVE_KINDS:
                outcome, eligible = "negative", end + timedelta(days=cooldowns["negative_days"])
            elif reply_kind in POSITIVE_KINDS:
                outcome, eligible = "positive", None                    # in_conversation covers the window
            else:
                outcome, eligible = "no_response", end + timedelta(days=cooldowns["no_response_days"])
            cp.write_row((contact_id, end, owner or None, outcome, eligible))
            n += 1
    return n


# reason -> (source, sql yielding (contact_id, account_id)); restricted to live contacts by the caller
RULE_SQL: dict[str, tuple[str, str]] = {
    "customer": ("salesforce", """
        select c."Id", c."AccountId" from raw_salesforce.contact c
        join raw_salesforce.account a on a."Id" = c."AccountId" where a."Type" = 'Customer'"""),
    "open_opp": ("salesforce", """
        select c."Id", c."AccountId" from raw_salesforce.contact c
        where c."AccountId" in (select "AccountId" from raw_salesforce.opportunity
                                where "StageName" not in ('Closed Won', 'Closed Lost'))"""),
    "ae_owned": ("salesforce", """
        select c."Id", c."AccountId" from raw_salesforce.contact c
        join raw_salesforce.account a on a."Id" = c."AccountId"
        where a."Type" = 'Prospect' and a."OwnerId" like 'ae_%%'"""),
    "touched_60d": ("history", """
        select t."WhoId", c."AccountId" from raw_salesforce.task t
        join raw_salesforce.contact c on c."Id" = t."WhoId"
        where t."ActivityDate" >= %(d60)s
        union
        select p.contact_id, c."AccountId" from raw_outreach.mailing m
        join raw_outreach.prospect p on p.id = m.prospect_id
        join raw_salesforce.contact c on c."Id" = p.contact_id
        where m.sent_at >= %(d60)s"""),
    "cooldown": ("app", """
        select ts.contact_id, c."AccountId" from contact_touch_state ts
        join raw_salesforce.contact c on c."Id" = ts.contact_id
        where ts.eligible_after > current_date"""),
    "bounced": ("outreach", """
        select p.contact_id, c."AccountId" from raw_outreach.sequence_state ss
        join raw_outreach.prospect p on p.id = ss.prospect_id
        join raw_salesforce.contact c on c."Id" = p.contact_id
        where ss.state = 'bounced'"""),
    "unsubscribed": ("outreach", """
        select p.contact_id, c."AccountId" from raw_outreach.sequence_state ss
        join raw_outreach.prospect p on p.id = ss.prospect_id
        join raw_salesforce.contact c on c."Id" = p.contact_id
        where ss.reply_kind = 'unsubscribe'"""),
    "opted_out": ("salesforce", """select c."Id", c."AccountId" from raw_salesforce.contact c where c."HasOptedOutOfEmail" = 'true'"""),
    "dnc": ("salesforce", """select c."Id", c."AccountId" from raw_salesforce.contact c where c."DoNotCall" = 'true'"""),
    "not_at_company": ("salesforce", """select c."Id", c."AccountId" from raw_salesforce.contact c where c."StillAtCompany__c" = 'false'"""),
    "competitor_partner": ("salesforce", """
        select c."Id", c."AccountId" from raw_salesforce.contact c
        join raw_salesforce.account a on a."Id" = c."AccountId"
        where a."Type" in ('Partner', 'Competitor', 'Vendor')"""),
    "unresolved_identity": ("salesforce", """
        select c."Id", c."AccountId" from raw_salesforce.contact c
        left join raw_salesforce.account a on a."Id" = c."AccountId"
        where c."AccountId" = '' or a."Id" is null"""),
    "in_conversation": ("outreach", """
        select p.contact_id, c."AccountId" from raw_outreach.sequence_state ss
        join raw_outreach.prospect p on p.id = ss.prospect_id
        join raw_salesforce.contact c on c."Id" = p.contact_id
        where ss.reply_kind in ('positive', 'referral')
          and coalesce(nullif(ss.finished_at, ''), ss.started_at) >= %(d180)s"""),
}


def run_exclusions(cur: psycopg.Cursor) -> dict[str, int]:
    contract = load_contract()
    actions = {r["reason"]: r["action"] for r in contract["rules"]}
    today = datetime.now(UTC).date()
    params = {"d60": (today - timedelta(days=60)).isoformat(), "d180": (today - timedelta(days=180)).isoformat()}
    cur.execute("delete from exclusion")
    counts: dict[str, int] = {}
    for reason, (source, sql) in RULE_SQL.items():
        cur.execute(
            f"""insert into exclusion (contact_id, account_id, reason, source, action)
                select distinct q.cid, nullif(q.aid, ''), %(reason)s, %(source)s, %(action)s
                from ({sql}) as q(cid, aid)
                join raw_salesforce.contact live on live."Id" = q.cid and live."IsDeleted" = 'false'
                on conflict do nothing""",
            {**params, "reason": reason, "source": source, "action": actions.get(reason, "exclude")})
        counts[reason] = cur.rowcount
    # no_email: missing address, or the enrichment vendor says invalid (adapter call, per the adapter rule)
    cur.execute("""select "Id", "AccountId", "Email" from raw_salesforce.contact where "IsDeleted" = 'false'""")
    contacts = cur.fetchall()
    status = get_adapter("enrichment").verify_email([e for _, _, e in contacts if e])
    rows = [(cid, aid or None) for cid, aid, e in contacts if not e or status.get(e) == "invalid"]
    cur.executemany(
        "insert into exclusion (contact_id, account_id, reason, source, action) values (%s, %s, 'no_email', 'enrichment', %s) on conflict do nothing",
        [(cid, aid, actions.get("no_email", "exclude")) for cid, aid in rows])
    counts["no_email"] = len(rows)
    return counts


def main() -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set DATABASE_URL")
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        touched = run_touch_state(cur)
        counts = run_exclusions(cur)
        conn.commit()
    print(f"contact_touch_state: {touched} contacts")
    for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  exclusion {reason:<20} {n:>6}")


if __name__ == "__main__":
    main()
