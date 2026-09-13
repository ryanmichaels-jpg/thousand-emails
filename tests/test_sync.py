"""The sync is a full refresh per object: running it twice must land on identical row counts,
and every object must reconcile with its adapter's own count. Skips when Postgres is not up
(make up && make schema)."""
import os

import psycopg
import pytest

DB = os.environ.get("DATABASE_URL", "postgresql://thousand:thousand@localhost:5432/thousand")


def _db_ready() -> bool:
    try:
        with psycopg.connect(DB, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("select 1 from information_schema.tables where table_schema = 'app' and table_name = 'sync_state'")
            return cur.fetchone() is not None
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _db_ready(), reason="needs Postgres with the app schema (make up && make schema)")


def test_sync_twice_row_counts_do_not_change():
    from api.sync.run import run
    first = run(DB)
    second = run(DB)
    assert first, "sync loaded nothing"

    def counts(results):
        return {(r["source"], r["table"]): r["rows_local"] for r in results}

    assert counts(first) == counts(second)
    assert all(r["ok"] for r in second), [r for r in second if not r["ok"]]


def test_sync_records_state_and_health():
    from api.sync.run import run
    results = run(DB)
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from app.sync_state where last_ok")
        assert cur.fetchone()[0] == len(results)
        cur.execute("select count(distinct (source, object)) from app.sync_health")
        assert cur.fetchone()[0] == len(results)
