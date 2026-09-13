"""Stage-3 answer-key tests: exclusions catch the injected traps, the patch cutter respects every
cap, holdout is exactly 10%, enrollment is idempotent and guarded, and reconciliation flags drift
in both directions. Skips when Postgres is not up (make up && make load && make schema + migrations).

Trap coverage note: stale_still_at_company and domain_mismatch are not asserted here because the
seed plants no pipeline-visible signal for them (enrichment shows those contacts current at their
account); they belong to the future re-enrichment/verification loop."""
import csv
import os

import psycopg
import pytest

DB = os.environ.get("DATABASE_URL", "postgresql://thousand:thousand@localhost:5432/thousand")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _db_ready() -> bool:
    try:
        with psycopg.connect(DB, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("""select count(*) from information_schema.columns
                           where table_schema = 'app' and table_name = 'enrollment' and column_name = 'origin'""")
            return cur.fetchone()[0] == 1
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _db_ready(), reason="needs Postgres with app schema + migrations applied")


@pytest.fixture(scope="module")
def world():
    from api.adapters import get_adapter
    from api.enroll import jobs
    from api.patch.cut import cut
    from api.scoring.run import run as score
    get_adapter.cache_clear()          # drop adapter state other test modules created (e.g. test_adapters)
    score(DB)
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        cur.execute("delete from enrollment")
        jobs.run_touch_state(cur)
        exclusion_counts = jobs.run_exclusions(cur)
        conn.commit()
    return {"patches": cut(DB), "exclusions": exclusion_counts}


def _mess(kind: str) -> set[str]:
    with open(os.path.join(ROOT, "seed", "fixtures", "truth", "injected_mess.csv")) as f:
        return {r["id"] for r in csv.DictReader(f) if r["kind"] == kind}


def _live(cur, ids: set[str]) -> set[str]:
    cur.execute("""select "Id" from raw_salesforce.contact where "IsDeleted" = 'false' and "Id" = any(%s)""", (list(ids),))
    return {r for (r,) in cur.fetchall()}


def test_exclusions_catch_injected_traps(world):
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        opted = _live(cur, _mess("opted_out_but_in_sequence"))
        cur.execute("select contact_id from app.exclusion where reason = 'opted_out'")
        assert opted <= {r for (r,) in cur.fetchall()}
        orphans = _live(cur, _mess("orphan_contact"))
        cur.execute("select contact_id from app.exclusion where reason = 'unresolved_identity'")
        assert orphans <= {r for (r,) in cur.fetchall()}
        deleted = _mess("soft_deleted")
        cur.execute("select count(*) from app.exclusion where contact_id = any(%s)", (list(deleted),))
        assert cur.fetchone()[0] == 0                      # deleted contacts are filtered, not excluded
        cur.execute("select count(*) from app.patch_member where contact_id = any(%s)", (list(deleted),))
        assert cur.fetchone()[0] == 0


def test_patch_caps(world):
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        for pid in world["patches"]:
            cur.execute("select count(*) from app.patch_member where patch_id = %s and enroll_day is not null", (pid,))
            assert cur.fetchone()[0] == 2000
            cur.execute("""select max(n) from (select count(*) as n from app.patch_member
                           where patch_id = %s group by account_id) x""", (pid,))
            assert cur.fetchone()[0] <= 4
            cur.execute("""select count(distinct enroll_day), max(n) from
                           (select enroll_day, count(*) as n from app.patch_member
                            where patch_id = %s and enroll_day is not null group by enroll_day) x""", (pid,))
            days, per_day = cur.fetchone()
            assert days == 10 and per_day == 200
            cur.execute("""select recommended_tier, count(*) from app.patch_member
                           where patch_id = %s group by 1""", (pid,))
            tiers = dict(cur.fetchall())
            assert tiers.get("A", 0) <= 200 and tiers.get("B", 0) <= 100 and tiers.get("call_only", 0) <= 50


def test_no_hard_excluded_contact_in_patch(world):
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("""select count(*) from app.patch_member pm
                       join app.exclusion e on e.contact_id = pm.contact_id
                       where e.action = 'exclude' and e.reason not in ('no_email', 'dnc')""")
        assert cur.fetchone()[0] == 0
        cur.execute("""select count(*) from app.patch_member pm
                       join app.exclusion e on e.contact_id = pm.contact_id
                       where e.reason = 'no_email' and pm.enroll_day is not null""")
        assert cur.fetchone()[0] == 0                      # invalid/missing email never in the email pool


def test_holdout_is_exactly_ten_percent(world):
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        for pid in world["patches"]:
            cur.execute("""select count(*) filter (where flags->>'holdout_mode4' = 'true'), count(*)
                           from app.patch_member where patch_id = %s and enroll_day is not null""", (pid,))
            holdouts, total = cur.fetchone()
            assert holdouts == total // 10


def test_enroll_is_idempotent_and_records_origin_and_arc(world):
    from api.enroll.path import Excluded, enroll
    pid = world["patches"][0]
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        cur.execute("""select pm.contact_id from patch_member pm
                       where pm.patch_id = %s and pm.recommended_tier = 'C'
                         and not exists (select 1 from exclusion e where e.contact_id = pm.contact_id)
                       order by pm.rank limit 1""", (pid,))
        (contact_id,) = cur.fetchone()
        e1 = enroll(cur, patch_id=pid, contact_id=contact_id, tier="C", play="new_cfo", mode=2, user_id="sdr_1")
        e2 = enroll(cur, patch_id=pid, contact_id=contact_id, tier="C", play="new_cfo", mode=2, user_id="sdr_1")
        assert e1 == e2
        cur.execute("select count(*), min(origin) from enrollment where contact_id = %s and patch_id = %s",
                    (contact_id, pid))
        n, origin = cur.fetchone()
        assert n == 1 and origin == "patch"
        cur.execute("select product_arc, exclusion_checked_at from enrollment where id = %s", (e1,))
        arc, checked = cur.fetchone()
        assert arc and "all" in arc and checked is not None
        cur.execute("select contact_id from exclusion where reason = 'customer' limit 1")
        (blocked,) = cur.fetchone()
        with pytest.raises(Excluded):
            enroll(cur, patch_id=pid, contact_id=blocked, tier="C", play="new_cfo", mode=2, user_id="sdr_1")
        conn.commit()


def test_reconciliation_catches_injected_drift(world):
    from api.adapters import get_adapter
    from api.enroll.reconcile import reconcile
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        clean = reconcile(cur)
        assert clean["missing_in_outreach"] == [] and clean["missing_in_app"] == []
        cur.execute("select id from enrollment limit 1")
        (eid,) = cur.fetchone()
        cur.execute("update enrollment set outreach_state_id = 'ss_ghost_00000001' where id = %s", (eid,))
        drifted = reconcile(cur)
        assert (eid, "ss_ghost_00000001") in drifted["missing_in_outreach"]
        ghost_state = get_adapter("outreach").add_to_sequence("pro_ghost_00000001", "seq_3", None)
        drifted = reconcile(cur)
        assert ghost_state in drifted["missing_in_app"]
        conn.rollback()                                    # leave the enrollment table as we found it
