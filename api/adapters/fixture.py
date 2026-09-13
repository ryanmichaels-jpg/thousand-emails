"""Fixture adapters: read seed/fixtures/<source>/*.csv and behave like the real thing, including
idempotency and an in-memory write log so tests can assert what would have been sent to the vendor."""
from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar

from .base import Row, SendResult

FIXTURES = os.environ.get("FIXTURES_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "seed", "fixtures"))


def _read(source: str, name: str) -> Iterator[Row]:
    with open(os.path.join(FIXTURES, source, f"{name}.csv"), newline="") as f:
        yield from csv.DictReader(f)


class FixtureSalesforce:
    OBJECTS: ClassVar[dict[str, str]] = {"Account": "account", "Contact": "contact", "Contract": "contract", "Opportunity": "opportunity",
                                         "OpportunityContactRole": "opportunity_contact_role", "Event": "event", "Task": "task"}
    def __init__(self): self.writes: list[tuple[str, str, Row]] = []
    def pull(self, sobject, fields, since=None):
        for r in _read("salesforce", self.OBJECTS[sobject]):
            yield {k: r.get(k, "") for k in fields} if fields else r
    def count(self, sobject): return sum(1 for _ in _read("salesforce", self.OBJECTS[sobject]))
    def update_fields(self, sobject, updates):
        n = 0
        for rid, fields in updates: self.writes.append((sobject, rid, dict(fields))); n += 1
        return n


class FixtureOutreach:
    def __init__(self):
        self.prospects: dict[str, str] = {}          # email -> prospect id
        self.custom: dict[str, dict[str, str]] = {}
        self.states: dict[tuple[str, str], str] = {} # (prospect, sequence) -> state id
        self.finished: dict[str, str] = {}
        for r in _read("outreach", "prospect"): self.prospects[r["email"]] = r["id"]
    def pull(self, resource, since=None): yield from _read("outreach", resource)
    def count(self, resource): return sum(1 for _ in _read("outreach", resource))
    def list_sequences(self): return list(_read("outreach", "sequence"))
    def upsert_prospect(self, contact):
        email = contact["Email"].lower()
        if email not in self.prospects: self.prospects[email] = f"pro_new_{len(self.prospects) + 1:08d}"
        return self.prospects[email]
    def set_custom_fields(self, prospect_id, fields): self.custom.setdefault(prospect_id, {}).update(fields)
    def add_to_sequence(self, prospect_id, sequence_id, mailbox_id=None):
        key = (prospect_id, sequence_id)
        if key not in self.states: self.states[key] = f"ss_new_{len(self.states) + 1:08d}"
        return self.states[key]
    def finish_sequence_state(self, state_id, reason): self.finished[state_id] = reason


class FixtureGong:
    def list_calls(self, since=None): yield from _read("gong", "call")
    def count_calls(self, since=None): return sum(1 for _ in _read("gong", "call"))
    def get_transcripts(self, call_ids):
        want = set(call_ids)
        with open(os.path.join(FIXTURES, "gong", "transcript.jsonl")) as f:
            for line in f:
                rec = json.loads(line)
                if rec["call_id"] in want: yield rec
    def get_call_parties(self, call_id):
        for r in _read("gong", "call"):
            if r["id"] == call_id: return [{"email": r["contact_email"], "crm_contact_id": r["crm_contact_id"], "crm_account_id": r["crm_account_id"]}]
        return []


class FixtureBigQuery:
    TABLES: ClassVar[dict[str, str]] = {"users": "users", "searches": "searches", "datalab_queries": "datalab_queries"}
    def query(self, sql): raise NotImplementedError("fixture BigQuery supports export_table only; put SQL in dbt")
    def export_table(self, table, since=None): yield from _read("bigquery", self.TABLES[table])
    def count(self, table): return sum(1 for _ in _read("bigquery", self.TABLES[table]))


class FixtureEnrichment:
    def employment_history(self, contact_ids):
        want = set(contact_ids)
        for r in _read("enrichment", "employment_history"):
            if r["contact_id"] in want: yield r
    def org_shape(self, account_domains):
        want = set(account_domains)
        for r in _read("enrichment", "org_shape"):
            if r["domain"] in want: yield r
    def linkedin_activity(self, contact_ids):
        want = set(contact_ids)
        for r in _read("enrichment", "linkedin_activity"):
            if r["contact_id"] in want: yield r
    def job_postings(self, account_domains):
        want = set(account_domains)
        for r in _read("enrichment", "job_posting"):
            if r["domain"] in want: yield r
    def verify_email(self, emails):
        # the fixture world already knows each contact's status via truth/contact_persona.csv
        status = {r["contact_id"]: r["email_status"] for r in _read("truth", "contact_persona")}
        by_email = {r["Email"].lower(): status.get(r["Id"], "unverified") for r in _read("salesforce", "contact") if r["Email"]}
        return {e: by_email.get(e.lower(), "invalid") for e in emails}


class FixtureCalendar:
    def events(self, since=None):
        for r in _read("calendar", "event"):
            r["attendees"] = json.loads(r["attendees"]); yield r
    def count_events(self, since=None): return sum(1 for _ in _read("calendar", "event"))


class StubSender:
    """Records sends; never touches a network. Path A/B implementations replace this."""
    def __init__(self): self.sent: list[dict[str, Any]] = []; self.suppressed: dict[str, str] = {}
    def send(self, draft_id, to_email, subject, body, thread_key=None):
        if to_email.lower() in self.suppressed: raise PermissionError(f"suppressed: {to_email}")
        r = SendResult(message_id=f"stub-{len(self.sent) + 1:08d}", mailbox="stub@pave-pool-1.com", sent_at=datetime.now(UTC))
        self.sent.append({"draft_id": draft_id, "to": to_email, "subject": subject, "thread_key": thread_key, **r.__dict__})
        return r
    def suppress(self, email, reason): self.suppressed[email.lower()] = reason
    def health(self): return {"stub@pave-pool-1.com": {"bounce_rate": 0.0, "complaint_rate": 0.0, "sent_today": len(self.sent)}}
