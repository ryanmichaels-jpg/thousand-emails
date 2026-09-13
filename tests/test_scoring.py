"""Stage-2 answer-key tests: the lift job recovers the ordering planted in
truth/persona_size_lift.csv, refuses sparse cells, and gates never let a contact into the ranking.
Skips when Postgres is not up (make up && make load && make schema)."""
import csv
import os

import psycopg
import pytest

DB = os.environ.get("DATABASE_URL", "postgresql://thousand:thousand@localhost:5432/thousand")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRUTH = os.path.join(ROOT, "seed", "fixtures", "truth", "persona_size_lift.csv")


def _db_ready() -> bool:
    try:
        with psycopg.connect(DB, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("""select count(*) from information_schema.tables
                           where (table_schema, table_name) in
                                 (('app', 'title_classification'), ('raw_salesforce', 'contact'))""")
            return cur.fetchone()[0] == 2
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _db_ready(), reason="needs Postgres with the app schema and raw fixtures loaded")


@pytest.fixture(scope="module")
def result():
    from api.scoring.run import run
    return run(DB)


def _truth_rows() -> list[dict]:
    with open(TRUTH) as f:
        return list(csv.DictReader(f))


def test_lift_ordering_matches_truth_for_trusted_cells(result):
    truth = _truth_rows()
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("""select size_band, persona from app.persona_size_lift
                       where rules_version = %s and trusted
                       order by size_band, smoothed_lift desc""", (result["rules_version"],))
        ours: dict[str, list[str]] = {}
        for band, persona in cur.fetchall():
            ours.setdefault(band, []).append(persona)
    assert ours
    for band, recovered in ours.items():
        cells = [r for r in truth if r["band"] == band and int(r["touched"]) >= 30]
        expected = [r["persona"] for r in sorted(cells, key=lambda r: float(r["realized_rate"]), reverse=True)]
        assert recovered == expected, f"band {band}: recovered {recovered}, truth {expected}"


def test_untrusted_cells_flagged(result):
    expected = {(r["band"], r["persona"]) for r in _truth_rows() if int(r["touched"]) < 30}
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("select size_band, persona from app.persona_size_lift where rules_version = %s and not trusted",
                    (result["rules_version"],))
        assert set(cur.fetchall()) == expected


def test_gated_contacts_have_no_rank(result):
    as_of = result["counts"]["as_of"]
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("""select count(*)
                       from raw_salesforce.contact c
                       join app.score_history s
                         on s.entity_type = 'contact' and s.entity_id = c."Id" and s.as_of = %s
                       where c."IsDeleted" = 'false'
                         and not (c."StillAtCompany__c" = 'true' and c."Email" <> '' and c."HasOptedOutOfEmail" = 'false')
                         and (s.contact_score is not null or s.rank_score is not null)""", (as_of,))
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from app.score_history where as_of = %s and rank_score is not null", (as_of,))
        assert cur.fetchone()[0] == result["counts"]["ranked"] > 0
