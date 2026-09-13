"""The enrollment path. Guardrails live here, not in prompts: exclusions are re-checked at the moment
of enrollment, the write is idempotent per (contact, sequence, patch), and origin + product_arc are
recorded on every row."""
from __future__ import annotations

import os
from datetime import UTC, datetime

import psycopg
import yaml
from psycopg.types.json import Jsonb

from api.adapters import get_adapter

SEQUENCES = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "sequences.yaml")

# scoped reasons: no_email only blocks email tiers, dnc only blocks the call tier
SCOPE_IGNORE = {"A": {"dnc"}, "B": {"dnc"}, "C": {"dnc"}, "call_only": {"no_email"}}

PERSONA_PRODUCT = {"TRC": "compensation_planning", "HR": "total_rewards", "FIN": "market_pricing"}


class Excluded(Exception):
    def __init__(self, contact_id: str, reasons: list[str]):
        self.reasons = reasons
        super().__init__(f"{contact_id} excluded: {', '.join(reasons)}")


def _sequence_for(tier: str) -> str:
    with open(SEQUENCES) as f:
        return yaml.safe_load(f)["tiers"][tier]["outreach_sequence_id"]


def check_exclusions(cur: psycopg.Cursor, contact_id: str, tier: str) -> None:
    cur.execute("select reason from exclusion where contact_id = %s and action = 'exclude'", (contact_id,))
    blocked = sorted({r for (r,) in cur.fetchall()} - SCOPE_IGNORE.get(tier, set()))
    if blocked:
        raise Excluded(contact_id, blocked)


def recommend_product_arc(cur: psycopg.Cursor, contact_id: str) -> dict:
    """Product user -> market_data (they already touch the data); otherwise by persona."""
    cur.execute("select 1 from raw_bigquery.users where matched_contact_id = %s limit 1", (contact_id,))
    if cur.fetchone():
        return {"all": "market_data"}
    cur.execute("""select value from contact_fact where contact_id = %s and field = 'persona'
                   order by as_of desc, id desc limit 1""", (contact_id,))
    row = cur.fetchone()
    return {"all": PERSONA_PRODUCT.get(row[0] if row else "", "market_data")}


def enroll(cur: psycopg.Cursor, *, patch_id: str, contact_id: str, tier: str, play: str, mode: int,
           user_id: str, origin: str = "patch", product_arc: dict | None = None) -> str:
    sequence_id = _sequence_for(tier)
    cur.execute("select id from enrollment where contact_id = %s and sequence_id = %s and patch_id = %s",
                (contact_id, sequence_id, patch_id))
    existing = cur.fetchone()
    if existing:
        return existing[0]
    check_exclusions(cur, contact_id, tier)
    cur.execute("""select "Email" from raw_salesforce.contact where "Id" = %s and "IsDeleted" = 'false'""", (contact_id,))
    (email,) = cur.fetchone()
    outreach = get_adapter("outreach")
    prospect_id = outreach.upsert_prospect({"Email": email})
    state_id = outreach.add_to_sequence(prospect_id, sequence_id, None)
    enrollment_id = f"enr_{patch_id}_{contact_id}"
    cur.execute(
        """insert into enrollment (id, patch_id, contact_id, sequence_id, play, mode, outreach_prospect_id,
                                   outreach_state_id, product_arc, origin, enrolled_by, exclusion_checked_at)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (enrollment_id, patch_id, contact_id, sequence_id, play, mode, prospect_id, state_id,
         Jsonb(product_arc or recommend_product_arc(cur, contact_id)), origin, user_id, datetime.now(UTC)))
    return enrollment_id
