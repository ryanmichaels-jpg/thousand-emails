"""Nightly reconciliation: app.enrollment must agree with Outreach (the adapter's sequence states).

Forward: every enrollment's outreach_state_id exists in Outreach. Reverse: every Outreach state
started on/after our first enrollment maps back to an enrollment row (older states are the imported
2026 history and out of scope). Drift in either direction is returned and printed, never silently
repaired.

Usage:  python -m api.enroll.reconcile
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

import psycopg
from dotenv import load_dotenv

from api.adapters import get_adapter


def reconcile(cur: psycopg.Cursor) -> dict[str, list]:
    states = {s["id"]: s for s in get_adapter("outreach").pull("sequence_state", None)}
    cur.execute("select id, outreach_state_id, enrolled_at::date from enrollment")
    enrollments = cur.fetchall()
    missing_in_outreach = [(eid, sid) for eid, sid, _ in enrollments if sid not in states]
    if enrollments:
        cutoff = min(d for _, _, d in enrollments).isoformat()
        ours = {sid for _, sid, _ in enrollments}
        missing_in_app = [sid for sid, s in states.items()
                          if (s.get("started_at") or "") >= cutoff and sid not in ours]
    else:
        missing_in_app = []
    report = {"missing_in_outreach": missing_in_outreach, "missing_in_app": missing_in_app}
    ok = not missing_in_outreach and not missing_in_app
    print(f"reconcile: {len(enrollments)} enrollments vs {len(states)} outreach states -> "
          f"{'clean' if ok else f'{len(missing_in_outreach)} missing in outreach, {len(missing_in_app)} missing in app'}")
    return report


def main() -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set DATABASE_URL")
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        report = reconcile(cur)
        cur.execute(
            """insert into sync_health (run_at, source, object, rows_local, rows_remote, null_rates, ok)
               values (%s, 'outreach', 'enrollment_reconcile', %s, %s, '{}', %s)""",
            (datetime.now(UTC), len(report["missing_in_outreach"]), len(report["missing_in_app"]),
             not report["missing_in_outreach"] and not report["missing_in_app"]))
        conn.commit()


if __name__ == "__main__":
    main()
