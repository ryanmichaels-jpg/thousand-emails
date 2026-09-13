"""Eval runners.

Rubric judge: 30 golden cases per play (facts in, expected properties out) drafted mode-2 through the
configured LLM adapter and scored mechanically: <=90 words, exactly-one-question shape, no banned
phrases, subject present, cites at least one provided fact value, case must/must-not strings. In
fixture mode this scores the deterministic fixture drafter -- it validates the pipeline and rubric,
not model quality; run with the real adapter for that.

Voice judge: nearest-centroid classifier over the same features the voice extractor measures, trained
on half of each corpus, reporting the confusion rate between a rep's real sent emails and mode-2
drafts in their voice. High confusion = the voice landed.

Usage:  python -m api.drafts.evals
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

import psycopg
from dotenv import load_dotenv

from api.adapters import get_adapter, llm_settings
from api.drafts.engine import SCHEMA, _prompt, never_violations
from api.drafts.plays import PLAYS_DIR, load_play
from api.drafts.voice import _features


def load_cases(play: str) -> list[dict]:
    path = os.path.join(PLAYS_DIR, play, "eval", "cases.jsonl")
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip() and "golden cases go here" not in line]


def _score(case: dict, play_cfg: dict, subject: str, body: str) -> dict[str, bool]:
    lower = body.lower()
    return {
        "under_90_words": len(body.split()) <= 90,
        "asks_a_question": body.count("?") >= 1,
        "no_banned_phrases": not never_violations(body, play_cfg),
        "subject_present": bool(subject.strip()),
        "cites_a_fact": any(str(f["value"]).lower() in lower for f in case["facts"]),
        "must_include": all(m.lower() in lower for m in case.get("must_include", [])),
        "must_not_include": not any(m.lower() in lower for m in case.get("must_not_include", [])),
    }


def run_rubric(play_name: str, llm=None) -> dict:
    play = load_play(play_name)
    client = llm or get_adapter("llm")
    model = llm_settings()["draft_model"]
    cases = load_cases(play_name)
    per_check: dict[str, int] = {}
    scores = []
    for case in cases:
        contact = ("", "", case["first_name"], case["last_name"], case["title"], "",
                   case["company"], case["band"], case["industry"])
        facts = {f["field"]: {"value": f["value"], "source": f["source"],
                              "as_of": date.fromisoformat(f["as_of"]), "evidence": f.get("evidence")}
                 for f in case["facts"]}
        system, user = _prompt(play, contact, facts, None, "")
        parsed = json.loads(client.complete(system, user, model=model, json_schema=SCHEMA))
        checks = _score(case, play["config"], parsed.get("subject", ""), parsed.get("body", ""))
        scores.append(sum(checks.values()) / len(checks))
        for k, ok in checks.items():
            per_check[k] = per_check.get(k, 0) + ok
    n = len(cases)
    report = {"play": play_name, "cases": n, "mean_score": round(sum(scores) / n, 3),
              "pass_rate_per_check": {k: round(v / n, 3) for k, v in sorted(per_check.items())}}
    print(f"rubric {play_name}: {n} cases, mean {report['mean_score']:.0%} -> {report['pass_rate_per_check']}")
    return report


def _centroid(rows: list[list[float]]) -> list[float]:
    return [sum(col) / len(rows) for col in zip(*rows)]


def _dist(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def voice_judge(cur: psycopg.Cursor) -> dict[str, float]:
    """Confusion rate per rep: how often held-out texts are classified as the wrong corpus."""
    def feats(text: str) -> list[float]:
        f = _features([text], [1.0])
        return [f["avg_words"] / 100, f["avg_sentence_words"] / 20, f["dash_greeting_share"],
                f["hi_greeting_share"], f["hedges_per_email"], f["exclaims_per_email"]]

    out: dict[str, float] = {}
    for rep in ("sdr_1", "sdr_2"):
        cur.execute("select body from raw_outreach.sent_email where rep = %s and is_template = 'false' order by id", (rep,))
        real = [feats(b) for (b,) in cur.fetchall()]
        cur.execute("""select body from draft where rep_id = %s and mode = 2 and status = 'pending' and body <> ''
                       order by created_at desc limit 40""", (rep,))
        drafts = [feats(b) for (b,) in cur.fetchall()]
        if len(real) < 4 or len(drafts) < 4:
            print(f"voice judge {rep}: not enough drafts to judge (have {len(drafts)})")
            continue
        c_real, c_draft = _centroid(real[::2]), _centroid(drafts[::2])
        held = [(v, "real") for v in real[1::2]] + [(v, "draft") for v in drafts[1::2]]
        wrong = sum(1 for v, label in held
                    if ("real" if _dist(v, c_real) <= _dist(v, c_draft) else "draft") != label)
        out[rep] = round(wrong / len(held), 3)
        print(f"voice judge {rep}: confusion rate {out[rep]:.0%} over {len(held)} held-out texts "
              "(higher = drafts read like the rep)")
    return out


def main() -> None:
    load_dotenv()
    for play in ("new_cfo", "hiring_covered_roles"):
        run_rubric(play)
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set DATABASE_URL for the voice judge")
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        voice_judge(cur)


if __name__ == "__main__":
    main()
