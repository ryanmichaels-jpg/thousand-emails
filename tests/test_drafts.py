"""Stage-4 invariants: no excluded contact is ever drafted, every draft is a full decision record,
modes 3/4 never touch the LLM, and the voice can never override a play's never-list. Skips when
Postgres is not up (make up && make load && make schema)."""
import json
import os

import psycopg
import pytest

DB = os.environ.get("DATABASE_URL", "postgresql://thousand:thousand@localhost:5432/thousand")


def _db_ready() -> bool:
    try:
        with psycopg.connect(DB, connect_timeout=2) as conn, conn.cursor() as cur:
            cur.execute("""select count(*) from information_schema.tables
                           where (table_schema, table_name) in (('app', 'draft'), ('raw_outreach', 'sent_email'))""")
            return cur.fetchone()[0] == 2
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _db_ready(), reason="needs Postgres with app schema + raw fixtures (incl. sent_email)")


class CountingLLM:
    def __init__(self, body: str = "placeholder body. Worth a look?"):
        self.calls = 0
        self.body = body

    def complete(self, system, user, *, model, max_tokens=800, json_schema=None):
        self.calls += 1
        return json.dumps({"subject": "stub subject", "body": self.body})


@pytest.fixture(scope="module")
def world():
    from api.adapters import get_adapter
    from api.drafts import facts, voice
    from api.enroll import jobs
    get_adapter.cache_clear()
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        cur.execute("delete from draft")
        jobs.run_touch_state(cur)
        jobs.run_exclusions(cur)
        facts.build_account_facts(cur)
        versions = {rep: voice.build_profile(cur, rep) for rep in ("sdr_1", "sdr_2")}
        cur.execute("""select c."Id" from raw_salesforce.contact c
                       join account_fact af on af.account_id = c."AccountId" and af.field = 'open_roles_covered'
                       where c."IsDeleted" = 'false' and c."Email" <> ''
                         and not exists (select 1 from exclusion e where e.contact_id = c."Id")
                       order by c."Id" limit 3""")
        clean = [r for (r,) in cur.fetchall()]
        cur.execute("select contact_id from exclusion where reason = 'customer' and action = 'exclude' limit 1")
        (excluded,) = cur.fetchone()
        conn.commit()
    assert len(clean) == 3
    return {"clean": clean, "excluded": excluded, "voice_versions": versions}


def test_mode2_draft_writes_full_decision_record(world):
    from api.drafts.engine import draft_one
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        r = draft_one(cur, contact_id=world["clean"][0], rep_id="sdr_1", play="hiring_covered_roles", mode=2)
        assert r["status"] == "pending" and r["subject"] and r["body"]
        cur.execute("""select skill_version, voice_version, prompt_hash, model, inputs_read,
                              fitness_result, exclusion_result from draft where id = %s""", (r["id"],))
        skill_version, voice_version, prompt_hash, model, inputs_read, fit, excl = cur.fetchone()
        assert skill_version == "hiring_covered_roles/v1"
        assert voice_version == world["voice_versions"]["sdr_1"]
        assert len(prompt_hash) == 64 and model != "none"
        assert inputs_read and all({"table", "id", "field", "as_of"} <= set(i) for i in inputs_read)
        assert fit["ok"] and excl["ok"]
        conn.commit()


def test_excluded_contact_is_never_drafted(world):
    from api.drafts.engine import draft_one
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        r = draft_one(cur, contact_id=world["excluded"], rep_id="sdr_1", play="hiring_covered_roles", mode=2,
                      llm=CountingLLM())
        assert r["status"] == "blocked" and r["body"] == "" and r["exclusions"]["reasons"]
        cur.execute("""select count(*) from draft d
                       join exclusion e on e.contact_id = d.contact_id
                        and e.action = 'exclude' and e.reason not in ('no_email', 'dnc')
                       where d.status <> 'blocked'""")
        assert cur.fetchone()[0] == 0
        conn.commit()


def test_modes_3_and_4_never_call_the_llm(world):
    from api.drafts.engine import draft_one
    spy = CountingLLM()
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        for mode in (3, 4):
            r = draft_one(cur, contact_id=world["clean"][1], rep_id="sdr_1", play="hiring_covered_roles",
                          mode=mode, llm=spy)
            assert r["status"] == "pending" and r["body"]
        assert spy.calls == 0
        cur.execute("select count(*) from draft where contact_id = %s and mode in (3, 4) and model <> 'none'",
                    (world["clean"][1],))
        assert cur.fetchone()[0] == 0
        conn.commit()


def test_voice_cannot_override_the_never_list(world):
    from api.drafts.engine import draft_one
    poisoned = CountingLLM(body="I hope this finds you well! Let's talk pricing. Worth a look?")
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        r = draft_one(cur, contact_id=world["clean"][2], rep_id="sdr_2", play="hiring_covered_roles", mode=2, llm=poisoned)
        assert r["status"] == "blocked"
        assert "i hope this finds you well" in r["fitness"]["never_violations"]
        conn.commit()


def test_every_draft_has_a_decision_record(world):
    with psycopg.connect(DB) as conn, conn.cursor() as cur:
        cur.execute("""select count(*), count(*) filter (where length(prompt_hash) = 64
                          and inputs_read is not null and fitness_result is not null
                          and exclusion_result is not null and skill_version <> '')
                       from app.draft""")
        total, complete = cur.fetchone()
        assert total > 0 and total == complete
