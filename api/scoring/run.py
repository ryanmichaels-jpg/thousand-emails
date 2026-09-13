"""Stage-2 scoring entrypoint.

Usage:  python -m api.scoring.run

1. Classify every distinct Title (rules, LLM fallback, cache) -> contact_fact; report accuracy.
2. Learn the persona x size-band lift from 2026 history -> persona_size_lift.
3. Gate, score, rank every contact -> score_history.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import psycopg
from dotenv import load_dotenv

from api.scoring import lift, personas, scores


def run(database_url: str | None = None) -> dict[str, Any]:
    load_dotenv()
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set DATABASE_URL")
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        rules = personas.load_rules()
        version = str(rules["version"])
        cache = personas.classify_titles(cur, rules)
        personas.write_facts(cur, cache)
        accuracy = personas.report_accuracy(cache)
        table, pooled = lift.build_persona_lift(cur, version)
        seniority_lift = lift.build_seniority_lift(cur)
        _print_lift(table)
        counts = scores.compute_scores(cur, table, pooled, seniority_lift, version)
        conn.commit()
    return {"rules_version": version, "accuracy": accuracy, "lift": table,
            "pooled_persona_lift": pooled, "seniority_lift": seniority_lift, "counts": counts}


def _print_lift(table: dict[tuple[str, str], dict]) -> None:
    for band in sorted({b for b, _ in table}):
        cells = sorted(((p, c) for (b, p), c in table.items() if b == band),
                       key=lambda x: x[1]["smoothed_lift"], reverse=True)
        row = "  ".join(f"{p}={c['smoothed_lift']:.2f}{'' if c['trusted'] else '?'}" for p, c in cells)
        print(f"  {band:>10}: {row}   (? = untrusted)")


if __name__ == "__main__":
    run()
