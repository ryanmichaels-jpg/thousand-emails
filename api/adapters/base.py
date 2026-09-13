"""Adapter interfaces. THE rule of this repo: nothing outside api/adapters imports a vendor client.

Each source has a Fixture implementation (reads seed/fixtures/<source>/) and a Real implementation.
config/sources.yaml decides which one `get_adapter()` returns. Add a method here before you need it.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

Row = dict[str, Any]


class Salesforce(Protocol):
    """System of record. Reads are incremental by watermark; writes are field updates only."""
    def pull(self, sobject: str, fields: list[str], since: datetime | None) -> Iterator[Row]: ...
    def count(self, sobject: str) -> int: ...
    def update_fields(self, sobject: str, updates: Iterable[tuple[str, Row]]) -> int: ...   # (Id, {field: value}) -> rows updated


class Outreach(Protocol):
    """Sequence definitions, prospects, sequence states, mailings. Enrollment goes through here."""
    def pull(self, resource: str, since: datetime | None) -> Iterator[Row]: ...
    def count(self, resource: str) -> int: ...                                             # JSON:API meta.count in the real client
    def upsert_prospect(self, contact: Row) -> str: ...                                        # returns prospect id; idempotent by email
    def set_custom_fields(self, prospect_id: str, fields: dict[str, str]) -> None: ...        # custom10..custom14 carry drafted bodies (path A)
    def add_to_sequence(self, prospect_id: str, sequence_id: str, mailbox_id: str | None) -> str: ...  # returns sequence_state id; idempotent
    def finish_sequence_state(self, state_id: str, reason: str) -> None: ...
    def list_sequences(self) -> list[Row]: ...


class Gong(Protocol):
    def list_calls(self, since: datetime | None) -> Iterator[Row]: ...
    def count_calls(self, since: datetime | None) -> int: ...                              # records.totalRecords in the real client
    def get_transcripts(self, call_ids: list[str]) -> Iterator[Row]: ...                     # batches of <=100 ids
    def get_call_parties(self, call_id: str) -> list[Row]: ...


class BigQuery(Protocol):
    """Product usage: free market-data users, searches, Data Lab questions."""
    def query(self, sql: str) -> Iterator[Row]: ...
    def export_table(self, table: str, since: datetime | None) -> Iterator[Row]: ...
    def count(self, table: str) -> int: ...


class Enrichment(Protocol):
    def employment_history(self, contact_ids: list[str]) -> Iterator[Row]: ...
    def org_shape(self, account_domains: list[str]) -> Iterator[Row]: ...
    def linkedin_activity(self, contact_ids: list[str]) -> Iterator[Row]: ...
    def job_postings(self, account_domains: list[str]) -> Iterator[Row]: ...
    def verify_email(self, emails: list[str]) -> dict[str, str]: ...                          # email -> verified|catch_all|unverified|invalid


class Calendar(Protocol):
    def events(self, since: datetime | None) -> Iterator[Row]: ...
    def count_events(self, since: datetime | None) -> int: ...


@dataclass
class SendResult:
    message_id: str
    mailbox: str
    sent_at: datetime


class Sender(Protocol):
    """Path A = OutreachSender (writes fields, Outreach sends). Path B = PoolSender. StubSender for demos."""
    def send(self, draft_id: str, to_email: str, subject: str, body: str, thread_key: str | None) -> SendResult: ...
    def suppress(self, email: str, reason: str) -> None: ...
    def health(self) -> dict[str, Any]: ...                                                    # bounce/complaint rates per mailbox


class LLM(Protocol):
    def complete(self, system: str, user: str, *, model: str, max_tokens: int = 800, json_schema: dict | None = None) -> str: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...
