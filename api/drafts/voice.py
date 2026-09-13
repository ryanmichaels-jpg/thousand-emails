"""Voice profiles: measured from a rep's own non-template sent emails, replied-to ones weighted double.

Extraction is mechanical (sentence length, greeting, sign-off, hedging), not an LLM impression, so
the same corpus always yields the same profile. Applied after the play skill and before batch custom
instructions; it can never override a play's never-list, fitness, or exclusions (the engine enforces
those regardless of what the voice says)."""
from __future__ import annotations

import re
from statistics import fmean

import psycopg

HEDGES = ("hope your week", "no worries", "happy to circle back", "would love", "i was thinking", "hoping this")


def _features(texts: list[str], weights: list[float]) -> dict:
    def sentences(t: str) -> list[str]:
        return [s.strip() for s in re.split(r"[.!?]+", t) if s.strip()]

    words = [len(t.split()) for t in texts]
    sent_lens = [fmean(len(s.split()) for s in sentences(t)) for t in texts]
    dash_greet = [bool(re.match(r"^\S+ —", t.strip())) for t in texts]
    hi_greet = [t.strip().lower().startswith("hi ") for t in texts]
    hedge = [sum(h in t.lower() for h in HEDGES) for t in texts]
    exclaim = [t.count("!") for t in texts]
    def wavg(xs):
        return sum(x * w for x, w in zip(xs, weights)) / sum(weights)
    return {"avg_words": round(wavg(words), 1), "avg_sentence_words": round(wavg(sent_lens), 1),
            "dash_greeting_share": round(wavg([float(x) for x in dash_greet]), 2),
            "hi_greeting_share": round(wavg([float(x) for x in hi_greet]), 2),
            "hedges_per_email": round(wavg(hedge), 2), "exclaims_per_email": round(wavg(exclaim), 2)}


def _render(rep_id: str, f: dict) -> tuple[str, list[str], list[str]]:
    terse = f["avg_words"] < 75
    dos, donts = [], []
    if f["dash_greeting_share"] >= 0.5:
        dos.append('Open with "«First» —" on its own line.')
        donts.append("No warm-up small talk before the point.")
    if f["hi_greeting_share"] >= 0.5:
        dos.append('Open with "Hi «First»," and one warm line before the point.')
    dos.append(f"Keep emails near {int(f['avg_words'])} words; sentences around {int(f['avg_sentence_words'])} words.")
    if terse:
        donts.append("No hedging phrases (\"no worries if not\", \"hope your week's going well\").")
    else:
        dos.append("Soften the ask; offer an easy out.")
    if f["exclaims_per_email"] < 0.2:
        donts.append("No exclamation marks.")
    profile_md = (f"## Voice — {rep_id}\n\n"
                  f"Average email {f['avg_words']} words, sentences {f['avg_sentence_words']} words. "
                  f"Greeting style: {'dash' if f['dash_greeting_share'] >= 0.5 else 'hi + warm line'}. "
                  f"Hedges per email: {f['hedges_per_email']}.\n\n"
                  "This voice adjusts phrasing only. It never overrides the play's Never list, fitness, or exclusions.")
    return profile_md, dos, donts


def build_profile(cur: psycopg.Cursor, rep_id: str) -> int:
    cur.execute("""select id, body, replied = 'true' from raw_outreach.sent_email
                   where rep = %s and is_template = 'false' order by id""", (rep_id,))
    rows = cur.fetchall()
    if not rows:
        raise ValueError(f"no non-template sent emails for {rep_id}")
    texts = [b for _, b, _ in rows]
    weights = [2.0 if replied else 1.0 for _, _, replied in rows]
    profile_md, dos, donts = _render(rep_id, _features(texts, weights))
    cur.execute("select coalesce(max(version), 0) + 1 from voice_profile where rep_id = %s", (rep_id,))
    (version,) = cur.fetchone()
    cur.execute("""insert into voice_profile (id, rep_id, version, profile_md, dos, donts, approved_at)
                   values (%s, %s, %s, %s, %s, %s, now())""",
                (f"vp_{rep_id}_v{version}", rep_id, version, profile_md,
                 psycopg.types.json.Jsonb(dos), psycopg.types.json.Jsonb(donts)))
    cur.execute("delete from voice_example where rep_id = %s and source = 'sent'", (rep_id,))
    for sid, body, replied in rows:
        cur.execute("""insert into voice_example (id, rep_id, source, body, replied, included, weight)
                       values (%s, %s, 'sent', %s, %s, true, %s)""",
                    (f"vx_{sid}", rep_id, body, replied, 2.0 if replied else 1.0))
    return version


def latest_profile(cur: psycopg.Cursor, rep_id: str) -> tuple[int, str, list, list] | None:
    cur.execute("""select version, profile_md, dos, donts from voice_profile
                   where rep_id = %s and approved_at is not null order by version desc limit 1""", (rep_id,))
    row = cur.fetchone()
    return row if row else None
