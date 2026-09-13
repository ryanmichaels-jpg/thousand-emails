"""Title -> persona + seniority per contracts/persona_rules.yaml.

Rules first (auditable, first match wins, case-insensitive). The LLM fallback fires only for titles
that match none of the specific persona patterns (the '.*' catch-all does not count as a match), asks
for a persona from the same vocabulary, and is cached by title string in app.title_classification so
each distinct title is classified exactly once. Seniority always comes from the rules: its pattern
list has full coverage. Facts land in app.contact_fact with the raw title as evidence.
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

import psycopg
import yaml

from api.adapters import get_adapter, llm_settings

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RULES_PATH = os.path.join(ROOT, "contracts", "persona_rules.yaml")
TRUTH_PATH = os.path.join(ROOT, "seed", "fixtures", "truth", "contact_persona.csv")


def load_rules() -> dict:
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


def classify_by_rules(title: str, rules: dict) -> tuple[str | None, str]:
    """(persona | None if only the catch-all matched, seniority)."""
    persona = None
    for label, pats in rules["persona_patterns"].items():
        if any(p != ".*" and re.search(p, title, re.IGNORECASE) for p in pats):
            persona = label
            break
    seniority = "IC"
    for label, pats in rules["seniority_patterns"].items():
        if any(p != ".*" and re.search(p, title, re.IGNORECASE) for p in pats):
            seniority = label
            break
    return persona, seniority


def _llm_persona(title: str, rules: dict) -> tuple[str, str]:
    settings = llm_settings()
    model = settings[rules["llm_fallback"]["model_key"]]
    schema = {"type": "object", "required": ["persona"],
              "properties": {"persona": {"type": "string", "enum": rules["personas"]}}}
    resp = get_adapter("llm").complete(
        system="You classify job titles for a compensation-software sales team. "
               f"Answer with JSON. Personas: {', '.join(rules['personas'])}. "
               "HR = people/HR leadership, TRC = total rewards & compensation, TAL = talent acquisition, "
               "FIN = finance, OTHER = anything else.",
        user=f"Title: {title}", model=model, json_schema=schema)
    persona = json.loads(resp).get("persona")
    return (persona if persona in rules["personas"] else "OTHER"), model


def classify_titles(cur: psycopg.Cursor, rules: dict) -> dict[str, tuple[str, str]]:
    """Classify every distinct title in raw_salesforce.contact once, through the cache."""
    version = str(rules["version"])
    cur.execute("select \"Title\" from raw_salesforce.contact group by 1")
    titles = [r[0] for r in cur.fetchall()]
    cur.execute("select title, persona, seniority from title_classification where rules_version = %s", (version,))
    cache: dict[str, tuple[str, str]] = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    n_llm = 0
    for title in titles:
        if title in cache:
            continue
        persona, seniority = classify_by_rules(title, rules)
        method, model = "rule", None
        if persona is None:
            (persona, model), method = _llm_persona(title, rules), "llm"
            n_llm += 1
        cur.execute(
            """insert into title_classification (title, persona, seniority, method, model, rules_version)
               values (%s, %s, %s, %s, %s, %s)
               on conflict (title) do update set persona = excluded.persona, seniority = excluded.seniority,
                 method = excluded.method, model = excluded.model, rules_version = excluded.rules_version,
                 classified_at = now()""",
            (title, persona, seniority, method, model, version))
        cache[title] = (persona, seniority)
    print(f"titles: {len(titles)} distinct, {n_llm} new LLM fallbacks (rest rules or cache)")
    return cache


def write_facts(cur: psycopg.Cursor, cache: dict[str, tuple[str, str]]) -> int:
    today = datetime.now(UTC).date()
    cur.execute("delete from contact_fact where field in ('persona', 'seniority') and as_of = %s", (today,))
    cur.execute("select title, method from title_classification")
    method_by_title = dict(cur.fetchall())
    cur.execute("select \"Id\", \"Title\" from raw_salesforce.contact where \"IsDeleted\" = 'false'")
    contacts = cur.fetchall()
    with cur.copy("copy contact_fact (contact_id, field, value, evidence, source, as_of) from stdin") as cp:
        for cid, title in contacts:
            persona, seniority = cache[title]
            source = "title_rules" if method_by_title[title] == "rule" else "llm"
            cp.write_row((cid, "persona", persona, title, source, today))
            cp.write_row((cid, "seniority", seniority, title, source, today))
    return len(contacts)


def report_accuracy(cache: dict[str, tuple[str, str]]) -> dict[str, Any]:
    with open(TRUTH_PATH) as f:
        truth = {r["contact_id"]: r for r in csv.DictReader(f)}
    with open(os.path.join(ROOT, "seed", "fixtures", "salesforce", "contact.csv")) as f:
        contacts = [(r["Id"], r["Title"]) for r in csv.DictReader(f) if r["Id"] in truth]
    p_hit = s_hit = 0
    for cid, title in contacts:
        persona, seniority = cache[title]
        p_hit += persona == truth[cid]["persona"]
        s_hit += seniority == truth[cid]["seniority"]
    n = len(contacts)
    acc = {"contacts": n, "persona_accuracy": round(p_hit / n, 4), "seniority_accuracy": round(s_hit / n, 4)}
    print(f"accuracy vs truth/contact_persona.csv: persona {acc['persona_accuracy']:.2%}, "
          f"seniority {acc['seniority_accuracy']:.2%} over {n} contacts")
    return acc
