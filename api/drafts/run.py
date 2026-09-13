"""Batch drafting for a rep's earliest tier A/B first touches.

Usage:  python -m api.drafts.run --rep sdr_1 --limit 60 [--real]

--real drafts through RealLLM (Anthropic) and is gated on ANTHROPIC_API_KEY; without the key it
prints the gate and does nothing. Without --real the fixture LLM drafts deterministically. Play per
contact: new_cfo when its required facts are present and fresh, else hiring_covered_roles. Mode 4
for cut-time holdouts (no LLM), mode 2 otherwise. Every attempt writes a decision record.
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg
from dotenv import load_dotenv

from api.drafts import facts as facts_mod
from api.drafts import voice as voice_mod
from api.drafts.engine import draft_one, fitness, gather_facts
from api.drafts.plays import load_play

PLAY_PREFERENCE = ("new_cfo", "hiring_covered_roles")


def run(rep: str = "sdr_1", limit: int = 60, real: bool = False, database_url: str | None = None) -> list[dict]:
    load_dotenv()
    llm = None
    if real:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("gate: ANTHROPIC_API_KEY is not set; refusing to run real drafts. Nothing written.")
            return []
        from api.adapters.real import RealLLM
        llm = RealLLM()
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set DATABASE_URL")
    plays = {name: load_play(name)["config"] for name in PLAY_PREFERENCE}
    results = []
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        facts_mod.build_account_facts(cur)
        if voice_mod.latest_profile(cur, rep) is None:
            voice_mod.build_profile(cur, rep)
        cur.execute("select id from patch where rep_id = %s order by cut_at desc limit 1", (rep,))
        row = cur.fetchone()
        if row is None:
            sys.exit(f"no patch for {rep}; run python -m api.patch.cut first")
        patch_id = row[0]
        cur.execute("""select contact_id, account_id, flags from patch_member
                       where patch_id = %s and recommended_tier in ('A', 'B') and enroll_day is not null
                       order by enroll_day, rank limit %s""", (patch_id, limit))
        members = cur.fetchall()
        for contact_id, account_id, flags in members:
            available = gather_facts(cur, account_id, contact_id)
            play = next((p for p in PLAY_PREFERENCE if fitness(plays[p], available)["ok"]), PLAY_PREFERENCE[-1])
            mode = 4 if flags.get("holdout_mode4") else 2
            results.append(draft_one(cur, contact_id=contact_id, rep_id=rep, play=play, mode=mode, llm=llm))
        conn.commit()
    by = {}
    for r in results:
        key = r["status"]
        by[key] = by.get(key, 0) + 1
    print(f"{rep}: {len(results)} drafts via {'RealLLM' if real else 'fixture LLM'} -> {by}")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rep", default="sdr_1")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--real", action="store_true")
    args = ap.parse_args()
    run(rep=args.rep, limit=args.limit, real=args.real)
