"""Patch cutter: 2,000 contacts per rep every two weeks, by account, via snake draft.

Accounts (best account score first) are snake-drafted across reps so no account splits between reps
and neither rep gets all the good ones. Max 4 contacts per account per patch. Tiers fill by rank
against contracts/sequences.yaml capacities (A 20/day, B 10/day, call_only 5/day over 10 enroll
days). No-email contacts route to call_only (direct dial + a provisional score clearing the tier-A
bar) or reenrich. Every email member gets an enroll_day (200/day) and every 10th member by rank is
flagged holdout_mode4 at cut time -- the permanent 10% template arm.

Usage:  python -m api.patch.cut
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import yaml
from dotenv import load_dotenv

from api.scoring.lift import build_seniority_lift
from api.scoring.personas import load_rules

SEQUENCES = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "sequences.yaml")
LI_MIN = 0.3            # li_score floor for tier A ("li_score_min" in sequences.yaml requires)

_POOL = """
with latest as (
  select distinct on (entity_id) entity_id, contact_score, li_score, rank_score
  from score_history where entity_type = 'contact'
  order by entity_id, as_of desc, id desc
),
persona as (
  select distinct on (contact_id) contact_id, value from contact_fact
  where field = 'persona' order by contact_id, as_of desc, id desc
),
seniority as (
  select distinct on (contact_id) contact_id, value from contact_fact
  where field = 'seniority' order by contact_id, as_of desc, id desc
)
select c."Id", c."AccountId", a."AccountScore__c"::numeric, a."HeadcountBand__c",
       pe.value, se.value, c."Email" <> '', c."Phone" <> '' or c."MobilePhone" <> '',
       case when c."MobilePhone" <> '' then 'mobile' when c."Phone" <> '' then 'hq' else 'none' end,
       l.li_score, l.rank_score,
       c."StillAtCompany__c" = 'true' and c."HasOptedOutOfEmail" = 'false'
from raw_salesforce.contact c
join raw_salesforce.account a on a."Id" = c."AccountId"
left join latest l on l.entity_id = c."Id"
left join persona pe on pe.contact_id = c."Id"
left join seniority se on se.contact_id = c."Id"
where c."IsDeleted" = 'false'
"""


def _business_days(start: date, n: int) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _capacities() -> dict[str, int]:
    with open(SEQUENCES) as f:
        seq = yaml.safe_load(f)
    days = seq["cadence_business_days"]
    return ({t: cfg["daily_cap_per_rep"] * days for t, cfg in seq["tiers"].items()}
            | {"patch_size": seq["patch_size"], "per_day": seq["enroll_per_day_per_rep"],
               "max_per_account": seq["max_contacts_per_account_per_patch"]})


def cut(database_url: str | None = None) -> list[str]:
    load_dotenv()
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("set DATABASE_URL")
    caps = _capacities()
    rules_version = str(load_rules()["version"])
    today = datetime.now(UTC).date()
    period_start = _business_days(today + timedelta(days=1), 1)[0]
    enroll_days = _business_days(period_start, 10)

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("set search_path = app, public")
        cur.execute("select id from users where role = 'rep' and active order by id")
        reps = [r for (r,) in cur.fetchall()]
        cur.execute("select contact_id from exclusion where action = 'exclude' and reason not in ('no_email', 'dnc')")
        hard_excluded = {r for (r,) in cur.fetchall()}
        cur.execute("select contact_id from exclusion where reason = 'dnc'")
        dnc = {r for (r,) in cur.fetchall()}
        cur.execute("select contact_id from exclusion where reason = 'no_email'")
        no_email = {r for (r,) in cur.fetchall()}
        cur.execute("select size_band, persona, smoothed_lift, trusted from persona_size_lift where rules_version = %s",
                    (rules_version,))
        plift = {(b, p): float(lift) for b, p, lift, trusted in cur.fetchall() if trusted}
        slift = build_seniority_lift(cur)

        cur.execute(_POOL)
        by_account: dict[str, dict[str, Any]] = {}
        for cid, aid, ascore, band, persona, seniority, has_email, has_phone, phone_type, li, rank, gates_ok in cur.fetchall():
            if cid in hard_excluded:
                continue
            acct = by_account.setdefault(aid, {"score": float(ascore), "email": [], "no_email": []})
            member = {"contact_id": cid, "account_id": aid, "phone": has_phone, "phone_type": phone_type,
                      "li": float(li) if li is not None else 0.0, "dnc": cid in dnc}
            if rank is not None and cid not in no_email:
                acct["email"].append({**member, "rank_score": float(rank)})
            elif gates_ok and (not has_email or cid in no_email):    # missing or vendor-invalid email: route, don't drop
                provisional = float(ascore) / 100 * plift.get((band, persona), 1.0) * slift.get(seniority, 1.0)
                acct["no_email"].append({**member, "rank_score": provisional})

        for acct in by_account.values():
            acct["email"].sort(key=lambda m: -m["rank_score"])
            acct["no_email"].sort(key=lambda m: -m["rank_score"])
        ordered = sorted((a for a in by_account.values() if a["email"] or a["no_email"]),
                         key=lambda a: -a["score"])

        # snake draft: one account per pick, until every rep's email pool is full
        members: dict[str, list[dict]] = {r: [] for r in reps}
        extras: dict[str, list[dict]] = {r: [] for r in reps}    # no-email members, outside the 2,000
        order, idx = list(reps), 0
        for acct in ordered:
            live = [r for r in reps if len(members[r]) < caps["patch_size"]]
            if not live:
                break
            if idx >= len(order):
                order, idx = [r for r in reversed(order)], 0     # snake turn
            rep = order[idx % len(order)]
            idx += 1
            if rep not in live:
                continue
            room = min(caps["max_per_account"], caps["patch_size"] - len(members[rep]))
            take = acct["email"][:room]
            members[rep] += take
            extras[rep] += acct["no_email"][:caps["max_per_account"] - len(take)]

        patch_ids = []
        for rep in reps:
            pid = f"patch_{rep}_{period_start.isoformat()}"
            patch_ids.append(pid)
            cur.execute("delete from patch_member where patch_id = %s", (pid,))
            cur.execute("delete from patch where id = %s", (pid,))
            cur.execute("insert into patch (id, rep_id, period_start, period_end, rules_version) values (%s, %s, %s, %s, %s)",
                        (pid, rep, period_start, enroll_days[-1], rules_version))
            ranked = sorted(members[rep], key=lambda m: -m["rank_score"])
            a = b = 0
            for i, m in enumerate(ranked):
                rank = i + 1
                if a < caps["A"] and m["phone"] and not m["dnc"] and m["li"] >= LI_MIN:
                    m["tier"], a = "A", a + 1
                elif b < caps["B"] and m["phone"] and not m["dnc"]:
                    m["tier"], b = "B", b + 1
                else:
                    m["tier"] = "C"
                m["rank"], m["enroll_day"] = rank, enroll_days[i // caps["per_day"]]
                m["holdout"] = rank % 10 == 0
            a_floor = min((m["rank_score"] for m in ranked if m["tier"] == "A"), default=float("inf"))
            c_only = 0
            for j, m in enumerate(sorted(extras[rep], key=lambda x: -x["rank_score"])):
                ok = m["phone_type"] in ("mobile", "hq") and not m["dnc"] and m["rank_score"] >= a_floor
                m["tier"] = "call_only" if ok and c_only < caps["call_only"] else "reenrich"
                c_only += m["tier"] == "call_only"
                m["rank"], m["enroll_day"], m["holdout"] = len(ranked) + j + 1, None, False
            with cur.copy("copy patch_member (patch_id, contact_id, account_id, rank, rank_score, recommended_tier, flags, enroll_day) from stdin") as cp:
                for m in ranked + extras[rep]:
                    flags = json.dumps({"phone_type": m["phone_type"], "li_score": m["li"], "dnc": m["dnc"],
                                        "holdout_mode4": m["holdout"]})
                    cp.write_row((pid, m["contact_id"], m["account_id"], m["rank"], round(m["rank_score"], 4),
                                  m["tier"], flags, m["enroll_day"]))
            tiers = {t: sum(1 for m in ranked + extras[rep] if m["tier"] == t) for t in ("A", "B", "C", "call_only", "reenrich")}
            print(f"{pid}: {len(ranked)} email members over {len(enroll_days)} days, tiers {tiers}, "
                  f"{sum(1 for m in ranked if m['holdout'])} mode-4 holdouts")
        conn.commit()
    return patch_ids


if __name__ == "__main__":
    cut()
