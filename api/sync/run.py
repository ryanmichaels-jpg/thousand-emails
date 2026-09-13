"""Sync every source into raw_* schemas through the adapters (never a vendor client directly).

Usage:  DATABASE_URL=... python -m api.sync.run

Each object is a full refresh into raw_<source>.<table> (all text; dbt casts and cleans), then a row in
app.sync_state (watermark = this run) and app.sync_health (rows loaded vs the adapter's own count, lag
since the previous watermark, null rates on key fields). seed/load.py remains the fixture bulk path;
this is the path the real adapters will use.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from api.adapters import get_adapter
from api.adapters.base import Row

SF_OBJECTS = {"Account": "account", "Contact": "contact", "Contract": "contract", "Opportunity": "opportunity",
              "OpportunityContactRole": "opportunity_contact_role", "Event": "event", "Task": "task"}
OUTREACH_RESOURCES = ("prospect", "sequence", "sequence_state", "mailing", "sent_email")
BIGQUERY_TABLES = ("users", "searches", "datalab_queries")

# fields whose null rate we watch, per (source, table): the joins and sends downstream break silently
# when these go missing, so surface them at sync time.
KEY_FIELDS: dict[tuple[str, str], list[str]] = {
    ("salesforce", "account"): ["Domain", "OwnerId"],
    ("salesforce", "contact"): ["Email", "Title", "AccountId"],
    ("outreach", "prospect"): ["email", "contact_id"],
    ("gong", "call"): ["crm_contact_id", "crm_account_id"],
    ("bigquery", "users"): ["domain", "matched_contact_id"],
    ("enrichment", "employment_history"): ["contact_id"],
}


def _pull_all() -> Iterator[tuple[str, str, list[Row], int]]:
    """Yield (source, table, rows, rows_remote) for every object the adapters expose."""
    sf = get_adapter("salesforce")
    for sobject, table in SF_OBJECTS.items():
        yield "salesforce", table, list(sf.pull(sobject, [], None)), sf.count(sobject)

    outreach = get_adapter("outreach")
    for resource in OUTREACH_RESOURCES:
        yield "outreach", resource, list(outreach.pull(resource, None)), outreach.count(resource)

    gong = get_adapter("gong")
    calls = list(gong.list_calls(None))
    yield "gong", "call", calls, gong.count_calls(None)
    with_transcript = [c["id"] for c in calls if str(c.get("has_transcript", "")).lower() == "true"]
    yield "gong", "transcript", list(gong.get_transcripts(with_transcript)), len(with_transcript)

    bq = get_adapter("bigquery")
    for table in BIGQUERY_TABLES:
        yield "bigquery", table, list(bq.export_table(table, None)), bq.count(table)

    # enrichment is request/response: enrich the contacts and domains Salesforce knows about,
    # so rows_remote is whatever the vendor returned for that request.
    contact_ids = [r["Id"] for r in sf.pull("Contact", ["Id"], None)]
    domains = sorted({r["Domain"] for r in sf.pull("Account", ["Domain"], None) if r.get("Domain")})
    enrich = get_adapter("enrichment")
    for table, rows in (("employment_history", list(enrich.employment_history(contact_ids))),
                        ("org_shape", list(enrich.org_shape(domains))),
                        ("linkedin_activity", list(enrich.linkedin_activity(contact_ids))),
                        ("job_posting", list(enrich.job_postings(domains)))):
        yield "enrichment", table, rows, len(rows)

    cal = get_adapter("calendar")
    yield "calendar", "event", list(cal.events(None)), cal.count_events(None)


def _text(v: Any) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


def _load(cur: psycopg.Cursor, source: str, table: str, rows: list[Row]) -> int:
    schema = f"raw_{source}"
    cur.execute(f'create schema if not exists "{schema}"')
    cur.execute(f'drop table if exists "{schema}"."{table}"')
    if source == "gong" and table == "transcript":
        cur.execute(f'create table "{schema}"."{table}" (call_id text primary key, turns jsonb, _synced_at timestamptz default now())')
        with cur.copy(f'copy "{schema}"."{table}" (call_id, turns) from stdin') as cp:
            cp.set_types(["text", "jsonb"])
            for r in rows:
                cp.write_row((r["call_id"], Jsonb(r["turns"])))
    else:
        cols = list(dict.fromkeys(k for r in rows for k in r))
        col_ddl = ", ".join(f'"{c}" text' for c in cols)
        col_list = ", ".join(f'"{c}"' for c in cols)
        cur.execute(f'create table "{schema}"."{table}" ({col_ddl}{", " if cols else ""}_synced_at timestamptz default now())')
        if cols:
            with cur.copy(f'copy "{schema}"."{table}" ({col_list}) from stdin') as cp:
                for r in rows:
                    cp.write_row([_text(r.get(c)) for c in cols])
    cur.execute(f'select count(*) from "{schema}"."{table}"')
    return cur.fetchone()[0]


def _null_rates(rows: list[Row], fields: list[str]) -> dict[str, float]:
    if not rows or not fields:
        return {}
    return {f: round(sum(1 for r in rows if not r.get(f)) / len(rows), 4) for f in fields}


def run(database_url: str | None = None) -> list[dict[str, Any]]:
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set DATABASE_URL")
    now = datetime.now(UTC)
    results: list[dict[str, Any]] = []
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        for source, table, rows, rows_remote in _pull_all():
            rows_local = _load(cur, source, table, rows)
            null_rates = _null_rates(rows, KEY_FIELDS.get((source, table), []))
            ok = rows_local == rows_remote
            cur.execute("select watermark from app.sync_state where source = %s and object = %s", (source, table))
            prev = cur.fetchone()
            lag_minutes = int((now - prev[0]).total_seconds() // 60) if prev and prev[0] else None
            cur.execute(
                """insert into app.sync_state (source, object, watermark, last_run, last_ok)
                   values (%s, %s, %s, %s, %s)
                   on conflict (source, object) do update
                   set watermark = excluded.watermark, last_run = excluded.last_run, last_ok = excluded.last_ok""",
                (source, table, now, now, ok))
            cur.execute(
                """insert into app.sync_health (run_at, source, object, rows_local, rows_remote, lag_minutes, null_rates, ok)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (now, source, table, rows_local, rows_remote, lag_minutes, Jsonb(null_rates), ok))
            results.append({"source": source, "table": table, "rows_local": rows_local, "rows_remote": rows_remote,
                            "lag_minutes": lag_minutes, "null_rates": null_rates, "ok": ok})
        conn.commit()
    _print_summary(results)
    return results


def _print_summary(results: list[dict[str, Any]]) -> None:
    print(f"{'object':<40} {'local':>8} {'remote':>8} {'lag':>6}  ok  null rates")
    for r in results:
        lag = "-" if r["lag_minutes"] is None else f"{r['lag_minutes']}m"
        rates = " ".join(f"{k}={v:.2f}" for k, v in r["null_rates"].items())
        print(f"raw_{r['source']}.{r['table']:<{36 - len(r['source'])}} {r['rows_local']:>8} {r['rows_remote']:>8} {lag:>6}  {'ok' if r['ok'] else 'XX'}  {rates}")
    bad = [r for r in results if not r["ok"]]
    print(f"{len(results)} objects synced, {len(bad)} with count mismatches" + (f": {[(r['source'], r['table']) for r in bad]}" if bad else ""))


if __name__ == "__main__":
    run()
