"""The draft engine. One call = one decision record, always.

Order of authority (locked): play skill -> voice profile -> batch custom instructions. The never-list
and fitness/exclusion checks are enforced here in code after generation, so neither the voice nor any
instruction can override them. Modes 3 and 4 render templates and never touch the LLM; mode 2 drafts
through the LLM adapter with the play skill; mode 1 is stage-5+ (interactive).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

from api.adapters import get_adapter, llm_settings
from api.drafts import voice as voice_mod
from api.drafts.plays import load_play

GLOBAL_BANNED = ["i hope this finds you well", "hope this finds you well"]

_CONTACT = """
select c."Id", c."AccountId", c."FirstName", c."LastName", c."Title", c."Email",
       a."Name", a."HeadcountBand__c", a."Industry"
from raw_salesforce.contact c
join raw_salesforce.account a on a."Id" = c."AccountId"
where c."Id" = %s and c."IsDeleted" = 'false'
"""


def never_violations(body: str, play_cfg: dict) -> list[str]:
    lower = body.lower()
    banned = GLOBAL_BANNED + [p.lower() for p in play_cfg.get("banned_phrases", [])]
    return sorted({p for p in banned if p in lower})


def gather_facts(cur: psycopg.Cursor, account_id: str, contact_id: str) -> dict[str, dict]:
    cur.execute("""select distinct on (field) 'account_fact', id, field, value, evidence, source, as_of
                   from account_fact where account_id = %s order by field, as_of desc, id desc""", (account_id,))
    rows = cur.fetchall()
    cur.execute("""select distinct on (field) 'contact_fact', id, field, value, evidence, source, as_of
                   from contact_fact where contact_id = %s order by field, as_of desc, id desc""", (contact_id,))
    rows += cur.fetchall()
    return {field: {"table": table, "id": rid, "value": value, "evidence": evidence, "source": source, "as_of": as_of}
            for table, rid, field, value, evidence, source, as_of in rows}


def fitness(play_cfg: dict, facts: dict[str, dict]) -> dict:
    today = datetime.now(UTC).date()
    missing, stale = [], []
    for req in play_cfg.get("required_facts", []):
        fact = facts.get(req["field"])
        if fact is None:
            missing.append(req["field"])
        elif (today - fact["as_of"]).days > req["max_age_days"]:
            stale.append(req["field"])
    return {"ok": not missing and not stale, "missing": missing, "stale": stale}


def render_template(text: str, ctx: dict[str, str]) -> tuple[str, str]:
    text = re.sub(r"<!--.*?-->\n?", "", text, flags=re.S).strip()
    unresolved = []

    def sub(m: re.Match) -> str:
        val = ctx.get(m.group(1))
        if val is None:
            unresolved.append(m.group(1))
            return m.group(0)
        return str(val)

    text = re.sub(r"\{\{(\w+)\}\}", sub, text)
    if unresolved:
        raise KeyError(f"unresolved merge fields: {unresolved}")
    subject, _, body = text.partition("\n")
    return subject.removeprefix("Subject:").strip(), body.strip()


def _vary(subject: str, body: str, contact_id: str) -> tuple[str, str]:
    """Mode 3: small deterministic variations, still no LLM."""
    n = int(hashlib.sha256(contact_id.encode()).hexdigest(), 16)
    if n % 2:
        subject = subject[0].upper() + subject[1:] if subject else subject
    first_line, _, rest = body.partition("\n")
    if n % 3 == 0 and " — " in first_line:
        name, _, tail = first_line.partition(" — ")
        first_line = f"Hi {name} — {tail}"
    return subject, first_line + "\n" + rest


def _prompt(play: dict, contact: tuple, facts: dict, voice: tuple | None, batch_instructions: str) -> tuple[str, str]:
    _, _, first, last, title, _, company, band, industry = contact
    system = play["skill"]
    if voice:
        _, profile_md, dos, donts = voice
        system += ("\n\n## Rep voice (applies after the play instructions above; it can NEVER override "
                   "the Never list, fitness, or exclusions)\n" + profile_md
                   + "\nDo: " + "; ".join(dos) + "\nDon't: " + "; ".join(donts))
    if batch_instructions:
        system += "\n\n## Batch instructions from the rep (lowest precedence)\n" + batch_instructions
    fact_lines = "\n".join(f"- {field}: {f['value']} (source {f['source']}, as of {f['as_of']})"
                           + (f' — evidence: "{f["evidence"]}"' if f["evidence"] else "")
                           for field, f in sorted(facts.items()))
    user = (f"Prospect: {first} {last}, {title} at {company} ({band} employees, {industry}).\n"
            f"FACTS (cite only these; do not invent anything):\n{fact_lines}\n"
            "Write the step-1 first-touch email. Return JSON with subject and body.")
    return system, user


SCHEMA = {"type": "object", "required": ["subject", "body"],
          "properties": {"subject": {"type": "string"}, "body": {"type": "string"}}}


def draft_one(cur: psycopg.Cursor, *, contact_id: str, rep_id: str, play: str, mode: int, step: int = 1,
              enrollment_id: str | None = None, batch_instructions: str = "", llm=None,
              source: str = "patch") -> dict:
    if mode == 1:
        raise ValueError("mode 1 is interactive and arrives with the app (stage 5)")
    play_data = load_play(play)
    cfg = play_data["config"]
    cur.execute(_CONTACT, (contact_id,))
    contact = cur.fetchone()
    if contact is None:
        raise ValueError(f"unknown or deleted contact {contact_id}")
    account_id = contact[1]

    cur.execute("select reason from exclusion where contact_id = %s and action = 'exclude' and reason <> 'dnc'",
                (contact_id,))
    excl_reasons = sorted(r for (r,) in cur.fetchall())
    exclusion_result = {"ok": not excl_reasons, "reasons": excl_reasons}

    facts = gather_facts(cur, account_id, contact_id)
    fit = fitness(cfg, facts)
    inputs_read = [{"table": f["table"], "id": f["id"], "field": field, "as_of": f["as_of"].isoformat()}
                   for field, f in sorted(facts.items())]

    vp = voice_mod.latest_profile(cur, rep_id) if mode == 2 else None
    voice_version = vp[0] if vp else None
    subject = body = ""
    model = "none"
    status = "pending"
    prompt_material = ""

    if not exclusion_result["ok"] or not fit["ok"]:
        status = "blocked"
    elif mode in (3, 4):
        ctx = {"first_name": contact[2], "company": contact[6], "headcount_band": contact[7],
               "new_exec_title": facts.get("new_exec_hire", {}).get("value", ""),
               "open_roles_covered": facts.get("open_roles_covered", {}).get("value", ""),
               "headcount": facts.get("headcount", {}).get("value", "")}
        template = play_data["templates"][step]
        subject, body = render_template(template, ctx)
        if mode == 3:
            subject, body = _vary(subject, body, contact_id)
        prompt_material = template
    else:  # mode 2
        client = llm or get_adapter("llm")
        model = llm_settings()["draft_model"]
        system, user = _prompt(play_data, contact, facts, vp, batch_instructions)
        prompt_material = system + "\n---\n" + user
        parsed = json.loads(client.complete(system, user, model=model, json_schema=SCHEMA))
        subject, body = parsed.get("subject", ""), parsed.get("body", "")

    violations = never_violations(body, cfg) if body else []
    if violations:
        status = "blocked"
        fit = {**fit, "never_violations": violations}

    draft_id = f"d_{uuid.uuid4().hex[:16]}"
    cur.execute(
        """insert into draft (id, enrollment_id, contact_id, account_id, rep_id, play, step, thread, mode,
                              source, skill_version, voice_version, prompt_hash, model, inputs_read,
                              fitness_result, exclusion_result, subject, body, status)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (draft_id, enrollment_id, contact_id, account_id, rep_id, play, step, "A" if step <= 3 else "B",
         mode, source, play_data["skill_version"], voice_version,
         hashlib.sha256(prompt_material.encode()).hexdigest(), model, Jsonb(inputs_read), Jsonb(fit),
         Jsonb(exclusion_result), subject, body, status))
    return {"id": draft_id, "status": status, "subject": subject, "body": body,
            "fitness": fit, "exclusions": exclusion_result}
