#!/usr/bin/env python3
"""Seed-data generator for the thousand-emails project.

Reads seed.yaml (distributions, not records), writes fixture CSV/JSONL files in the
shape of each source system, plus a truth/ folder recording what was planted so the
pipeline can be tested against it. Stdlib + PyYAML only. Deterministic per seed.
"""
import csv
import datetime as dt
import json
import math
import os
import random
import shutil
from collections import Counter, defaultdict

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "seed.yaml")) as _f:
    CFG = yaml.safe_load(_f)
with open(os.path.join(HERE, CFG["job_postings"]["families_file"])) as _f:
    FAM = yaml.safe_load(_f)["families"]
R = random.Random(CFG["seed"])
OUT = os.path.join(HERE, CFG["out_dir"])
AS_OF = dt.date.fromisoformat(CFG["as_of"])
BANDS = CFG["accounts"]["bands"]
PERSONAS = ["HR", "TRC", "TAL", "FIN", "OTHER"]
SENIORITY = ["C", "VP", "Director", "Manager", "IC"]

# ----------------------------------------------------------------------------- helpers
def wchoice(d):
    keys = list(d.keys()); w = [d[k] for k in keys]
    return R.choices(keys, weights=w, k=1)[0]

def rand_date(start, end):
    return start + dt.timedelta(days=R.randint(0, max(0, (end - start).days)))

def rand_dt(start, end):
    d = rand_date(start, end)
    return dt.datetime(d.year, d.month, d.day, R.randint(8, 18), R.choice([0, 15, 30, 45]))  # noqa: DTZ001 (fixtures are naive local times)

def business_days_after(d, n):
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5: n -= 1
    return d

class Ids:
    def __init__(self): self.c = Counter()
    def __call__(self, prefix):
        self.c[prefix] += 1
        return f"{prefix}{self.c[prefix]:012d}"
ID = Ids()

def writer(path, fields):
    full = os.path.join(OUT, path); os.makedirs(os.path.dirname(full), exist_ok=True)
    f = open(full, "w", newline="")  # noqa: SIM115 (caller closes)
    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader()
    return f, w

def dump_csv(path, rows, fields=None):
    if not rows and not fields: return
    fields = fields or list(rows[0].keys())
    f, w = writer(path, fields)
    for r in rows: w.writerow(r)
    f.close()

MESS = []
def mess(kind, entity, eid, note=""):
    MESS.append({"kind": kind, "entity": entity, "id": eid, "note": note})

# ----------------------------------------------------------------------------- vocab
FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Christopher", "Karen", "Daniel", "Lisa", "Matthew", "Nancy", "Anthony", "Betty", "Mark", "Sandra", "Steven", "Ashley", "Andrew", "Kimberly", "Paul", "Emily", "Joshua", "Donna", "Kenneth", "Michelle", "Kevin", "Carol", "Brian", "Amanda", "George", "Melissa", "Timothy", "Deborah", "Ronald", "Stephanie", "Jason", "Rebecca", "Edward", "Sharon", "Jeffrey", "Laura", "Ryan", "Cynthia", "Jacob", "Kathleen", "Gary", "Amy", "Nicholas", "Angela", "Eric", "Shirley", "Jonathan", "Anna", "Stephen", "Brenda", "Larry", "Pamela", "Justin", "Emma", "Scott", "Nicole", "Brandon", "Helen", "Benjamin", "Samantha", "Samuel", "Katherine", "Gregory", "Christine", "Alexander", "Debra", "Priya", "Wei", "Aisha", "Diego", "Fatima", "Hiroshi", "Ingrid", "Kwame", "Leila", "Mateo", "Noor", "Olga", "Rafael", "Sofia", "Tariq", "Yuki", "Zara", "Arjun", "Chloe", "Dmitri", "Elena"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell", "Carter", "Roberts", "Gomez", "Phillips", "Evans", "Turner", "Diaz", "Parker", "Cruz", "Edwards", "Collins", "Reyes", "Stewart", "Morris", "Morales", "Murphy", "Cook", "Rogers", "Gutierrez", "Ortiz", "Morgan", "Cooper", "Peterson", "Bailey", "Reed", "Kelly", "Howard", "Ramos", "Kim", "Cox", "Ward", "Richardson", "Watson", "Brooks", "Chavez", "Wood", "James", "Bennett", "Gray", "Mendoza", "Ruiz", "Hughes", "Price", "Alvarez", "Castillo", "Sanders", "Patel", "Myers", "Long", "Ross", "Foster", "Okafor", "Novak", "Haddad", "Lindqvist", "Tanaka", "Rossi", "Schmidt", "Dubois"]
CO_A = ["Blue", "North", "Bright", "Clear", "Silver", "Summit", "Harbor", "Cedar", "Atlas", "Nova", "Beacon", "Granite", "Meridian", "Vantage", "Prime", "Apex", "Crest", "Ember", "Lumen", "Pioneer", "Orchard", "Copper", "Sable", "Cobalt", "Juniper", "Maple", "Quill", "Ridge", "Signal", "Tidal", "Vector", "Willow", "Arbor", "Basil", "Delta", "Echo", "Falcon", "Garnet", "Halo", "Iris", "Kestrel", "Lark", "Mosaic", "Nimbus", "Onyx", "Pebble", "Quartz", "Raven", "Solstice", "Terra", "Umber", "Vale", "Wren", "Zenith"]
CO_B = {
    "Technology": ["Labs", "Software", "Systems", "Cloud", "Data", "AI", "Networks", "Digital", "Robotics", "Analytics", "Security", "Platforms", "Apps"],
    "Financial Services": ["Capital", "Partners", "Financial", "Advisors", "Holdings", "Lending", "Insurance", "Wealth", "Payments"],
    "Manufacturing": ["Manufacturing", "Industries", "Fabrication", "Works", "Components", "Machinery", "Metals", "Plastics"],
    "CPG": ["Foods", "Brands", "Beverages", "Goods", "Naturals", "Snacks", "Beauty", "Home"],
    "Hospitality": ["Hotels", "Resorts", "Hospitality", "Dining", "Restaurants", "Lodging", "Group"],
    "Healthcare": ["Health", "Clinics", "Medical", "Care", "Therapeutics", "Wellness", "Diagnostics"],
    "Retail": ["Retail", "Stores", "Outfitters", "Market", "Supply", "Trading", "Co"],
    "Professional Services": ["Consulting", "Advisory", "Group", "Associates", "Solutions", "Services"],
    "Media": ["Media", "Studios", "Publishing", "Entertainment", "Broadcasting", "Press"],
}
SUFFIX = ["", "", "", " Inc", " Co", " Group", " Corp"]
TLD = {"US": ".com", "UK": ".co.uk", "Canada": ".ca", "EU": ".io", "APAC": ".com"}

TITLES = {
 "HR": {"C": ["Chief People Officer", "Chief Human Resources Officer", "CHRO"],
        "VP": ["VP of People", "VP, Human Resources", "Vice President, People Operations", "SVP People"],
        "Director": ["Director of People", "Director, Human Resources", "Head of People", "People Director", "Director of People Operations"],
        "Manager": ["People Operations Manager", "HR Manager", "HR Business Partner", "Senior HRBP", "People Partner"],
        "IC": ["HR Generalist", "People Operations Specialist", "HR Coordinator", "People Ops Associate"]},
 "TRC": {"C": ["Chief Total Rewards Officer"],
         "VP": ["VP of Total Rewards", "VP, Compensation & Benefits", "Vice President, Rewards"],
         "Director": ["Director of Total Rewards", "Director, Compensation", "Head of Compensation", "Head of Total Rewards", "Director of Compensation & Benefits", "Sr. Director, Global Rewards"],
         "Manager": ["Compensation Manager", "Total Rewards Manager", "Senior Compensation Manager", "Manager, Rewards", "Compensation & Benefits Manager"],
         "IC": ["Compensation Analyst", "Senior Compensation Analyst", "Total Rewards Analyst", "Compensation Partner", "Rewards Specialist"]},
 "TAL": {"C": ["Chief Talent Officer"],
         "VP": ["VP of Talent", "VP, Talent Acquisition", "Vice President, Recruiting"],
         "Director": ["Director of Talent Acquisition", "Head of Talent", "Director, Recruiting", "Head of Recruiting"],
         "Manager": ["Talent Acquisition Manager", "Recruiting Manager", "Senior Recruiting Manager"],
         "IC": ["Senior Recruiter", "Technical Recruiter", "Talent Partner", "Recruiter"]},
 "FIN": {"C": ["Chief Financial Officer", "CFO"],
         "VP": ["VP of Finance", "VP, FP&A", "Vice President, Finance"],
         "Director": ["Director of Finance", "Head of FP&A", "Director, Financial Planning & Analysis", "Controller"],
         "Manager": ["Finance Manager", "FP&A Manager", "Senior Manager, FP&A"],
         "IC": ["Financial Analyst", "Senior Financial Analyst", "FP&A Analyst"]},
 "OTHER": {"C": ["Chief Executive Officer", "Chief Operating Officer", "Founder & CEO", "COO"],
           "VP": ["VP of Operations", "VP, Business Operations", "Vice President, Strategy"],
           "Director": ["Director of Operations", "Chief of Staff", "Head of Business Operations"],
           "Manager": ["Operations Manager", "Business Operations Manager", "Office Manager"],
           "IC": ["Operations Analyst", "Executive Assistant", "Business Analyst"]},
}
TYPO = lambda t: R.choice([t.replace("Director", "Dir."), t.replace("Vice President", "VP"), t.replace("Manager", "Mgr"), t.replace("Senior", "Sr"), t.lower(), t.upper(), t.replace("Compensation", "Comp"), t + " ", t.replace("of", "-")])

# ----------------------------------------------------------------------------- 1. accounts
def gen_accounts():
    A = CFG["accounts"]; C = CFG["customers"]
    types = []
    for t, n in A["type_mix"].items(): types += [t] * n
    R.shuffle(types)
    accounts, contracts, used_domains = [], [], set()
    band_bounds = {"1-50": (5, 50), "51-200": (51, 200), "201-1000": (201, 1000), "1001-5000": (1001, 5000), "5000+": (5001, 60000)}
    for i in range(A["total"]):
        typ = types[i]; band = wchoice(A["band_share"]); ind = wchoice(A["industry_share"]); reg = wchoice(A["region_share"])
        lo, hi = band_bounds[band]; hc = int(math.exp(R.uniform(math.log(lo), math.log(hi))))
        tries = 0
        while True:
            tries += 1
            core = f"{R.choice(CO_A)} {R.choice(CO_B[ind])}" if tries < 4 else f"{R.choice(CO_A)}{R.choice(CO_A).lower()} {R.choice(CO_B[ind])}"
            name = core + R.choice(SUFFIX)
            dom = core.lower().replace(" ", "").replace("&", "") + TLD[reg]
            if dom not in used_domains: used_domains.add(dom); break
        # account score: band and industry fit plus noise; hidden truth is fine, it's an input not a target
        band_fit = {"1-50": 35, "51-200": 60, "201-1000": 75, "1001-5000": 70, "5000+": 55}[band]
        score = max(1, min(100, int(R.gauss(band_fit + (10 if ind == "Technology" else -5), 14))))
        acc = {"Id": ID("001"), "Name": name, "Website": "https://www." + dom, "Domain": dom, "Industry": ind,
               "NumberOfEmployees": hc, "HeadcountBand__c": band, "Type": {"prospect": "Prospect", "customer": "Customer", "churned": "Former Customer"}[typ],
               "BillingCountry": reg, "OwnerId": "", "AccountScore__c": score,
               "CreatedDate": rand_date(dt.date(2019, 1, 1), dt.date(2026, 6, 30)).isoformat(), "IsDeleted": "false", "_type": typ}
        if typ in ("customer", "churned"):
            if typ == "customer":
                if R.random() < 0.7: yrs = R.uniform(*C["tenure_years"])
                else: yrs = R.uniform(0.5, 3.0)
                start = AS_OF - dt.timedelta(days=int(yrs * 365)); end = None; status = "Active"; reason = ""
            else:
                start = rand_date(dt.date(2020, 1, 1), dt.date(2024, 6, 30))
                yrs = 1
                while R.random() > C["annual_churn_hazard"] and yrs < 6: yrs += 1
                end = min(start + dt.timedelta(days=int(yrs * 365 + R.randint(-30, 30))), AS_OF - dt.timedelta(days=30))
                status = "Churned"; reason = wchoice(C["churn_reasons"])
            acc["OwnerId"] = f"ae_{R.randint(1, 4)}"
            acc["CreatedDate"] = min(dt.date.fromisoformat(acc["CreatedDate"]), start - dt.timedelta(days=R.randint(30, 400))).isoformat()
            contracts.append({"Id": ID("a0C"), "AccountId": acc["Id"], "StartDate": start.isoformat(), "EndDate": end.isoformat() if end else "",
                              "Status": status, "ChurnReason__c": reason, "ARR__c": int(C["arr_by_band"][band] * R.uniform(0.6, 1.6)), "Plan__c": R.choice(["Benchmarking", "Benchmarking + Planning", "Full Suite"])})
            acc["_contract"] = (start, end)
        accounts.append(acc)
    # midyear type change: a few customers whose contract started in 2026 were prospects on Jan 1
    for acc in accounts:
        if acc["_type"] == "customer" and acc["_contract"][0] >= dt.date(2026, 1, 1) and R.random() < 0.5:
            mess("account_type_changed_midyear", "account", acc["Id"], f"Prospect -> Customer on {acc['_contract'][0]}")
    # snake draft prospects to SDRs by score
    pros = sorted([a for a in accounts if a["_type"] == "prospect"], key=lambda a: -a["AccountScore__c"])
    sdrs = CFG["team"]["sdrs"]; order = sdrs + sdrs[::-1]
    for i, a in enumerate(pros): a["OwnerId"] = order[i % len(order)]
    return accounts, contracts

# ----------------------------------------------------------------------------- 2. org shape
def gen_org_shape(accounts):
    rows = []
    for a in accounts:
        med = CFG["org_shape"]["medians"][a["HeadcountBand__c"]]
        tight = a["_type"] != "prospect"
        for fn, m in med.items():
            if m == 0: hc = 1 if R.random() < 0.15 else 0
            else: hc = max(0, round(m * math.exp(R.gauss(0, 0.25 if tight else 0.45))))
            rows.append({"account_id": a["Id"], "domain": a["Domain"], "function": fn, "headcount": hc, "source": "vendor_org_chart", "as_of": rand_date(AS_OF - dt.timedelta(days=90), AS_OF).isoformat()})
        a["_org"] = {r["function"]: r["headcount"] for r in rows[-len(med):]}
    return rows

# ----------------------------------------------------------------------------- 3. contacts
def gen_contacts(accounts):
    Cc = CFG["contacts"]; M = CFG["mess"]
    contacts, truth_persona, employment, past_links, li = [], [], [], [], []
    customers = [a for a in accounts if a["_type"] in ("customer", "churned")]
    for a in accounts:
        n = CFG["accounts"]["contacts_per_band"][a["HeadcountBand__c"]]
        pshare = CFG["persona_contact_share"][a["HeadcountBand__c"]]
        for _ in range(n):
            persona = wchoice(pshare); sen = wchoice(CFG["seniority_share"])
            if a["HeadcountBand__c"] == "1-50" and sen == "IC" and R.random() < 0.6: sen = "Manager"
            title = R.choice(TITLES[persona][sen])
            fn, ln = R.choice(FIRST), R.choice(LAST)
            estat = wchoice(Cc["email_status_share"])
            email = "" if estat == "none" else f"{fn}.{ln}{R.choice(['', '', str(R.randint(1, 9))])}@{a['Domain']}".lower()
            ptype = wchoice(Cc["phone_type_share"])
            phone = "" if ptype == "none" else f"+1-{R.randint(201, 989)}-{R.randint(200, 999)}-{R.randint(1000, 9999)}"
            still = R.random() < Cc["still_at_company_true"]
            cid = ID("003")
            c = {"Id": cid, "AccountId": a["Id"], "FirstName": fn, "LastName": ln, "Title": title, "Email": email,
                 "Phone": phone if ptype in ("direct", "hq") else "", "MobilePhone": phone if ptype == "mobile" else "",
                 "HasOptedOutOfEmail": "true" if R.random() < Cc["opted_out"] else "false",
                 "DoNotCall": "true" if R.random() < Cc["do_not_call"] else "false",
                 "StillAtCompany__c": "true" if still else "false",
                 "LastActivityDate": "", "OwnerId": a["OwnerId"], "CreatedDate": rand_date(dt.date.fromisoformat(a["CreatedDate"]), AS_OF).isoformat(),
                 "IsDeleted": "false", "_persona": persona, "_sen": sen, "_band": a["HeadcountBand__c"], "_estat": estat, "_ptype": ptype, "_acc": a}
            # mess: typos, stale flag, domain mismatch, soft delete
            if R.random() < M["title_typos"]: c["Title"] = TYPO(title); mess("title_typo", "contact", cid, f"{title!r} -> {c['Title']!r}")
            if still and R.random() < M["stale_still_at_company"]: c["_left"] = True; mess("stale_still_at_company", "contact", cid, "flag true, person left")
            if email and R.random() < M["domain_mismatch"]:
                c["Email"] = email.split("@")[0] + "@" + R.choice(accounts)["Domain"]; mess("domain_mismatch", "contact", cid, c["Email"])
            if R.random() < M["soft_deleted"]: c["IsDeleted"] = "true"; mess("soft_deleted", "contact", cid)
            contacts.append(c)
            truth_persona.append({"contact_id": cid, "persona": persona, "seniority": sen, "clean_title": title, "email_status": estat, "phone_type": ptype})
            # LinkedIn activity
            active = R.random() < Cc["linkedin_active_share"]
            li.append({"contact_id": cid, "posts_90d": R.randint(1, 12) if active else R.choice([0, 0, 0, 1]), "comments_30d": R.randint(1, 20) if active else 0,
                       "followers_band": R.choice(["<500", "500-2k", "2k-10k", "10k+"]) if active else R.choice(["<500", "500-2k"]),
                       "last_active_days": R.randint(0, 20) if active else R.randint(30, 400), "as_of": AS_OF.isoformat()})
            # employment history: current + 1..3 prior
            pid = ID("per")
            cur_start = rand_date(dt.date(2016, 1, 1), AS_OF - dt.timedelta(days=60))
            if "_left" in c:
                # the stale-flag trap is plantable in vendor data: the account position ended 30-200 days
                # ago and a newer current employer exists. Local Random keeps the global RNG sequence intact.
                LR = random.Random(f"left:{cid}")
                left_end = max(AS_OF - dt.timedelta(days=LR.randint(30, 200)), cur_start + dt.timedelta(days=30))
                employment.append({"person_id": pid, "contact_id": cid, "company": a["Name"], "company_domain": a["Domain"], "title": title, "start": cur_start.isoformat(), "end": left_end.isoformat(), "is_current": "false"})
                new_co = f"{LR.choice(CO_A)} {LR.choice([w for ws in CO_B.values() for w in ws])}"
                employment.append({"person_id": pid, "contact_id": cid, "company": new_co, "company_domain": new_co.lower().replace(" ", "") + ".works",
                                   "title": title, "start": (left_end + dt.timedelta(days=LR.randint(5, 30))).isoformat(), "end": "", "is_current": "true"})
            else:
                employment.append({"person_id": pid, "contact_id": cid, "company": a["Name"], "company_domain": a["Domain"], "title": title, "start": cur_start.isoformat(), "end": "", "is_current": "true"})
            end = cur_start; planted = a["_type"] == "prospect" and R.random() < Cc["past_customer_share"]
            for k in range(R.randint(1, 3)):
                start = end - dt.timedelta(days=R.randint(400, 1800))
                if planted and k == 0:
                    pc = R.choice(customers); cs, ce = pc["_contract"]; ce = ce or AS_OF
                    # overlap the employment with the subscription window
                    start = max(start, cs - dt.timedelta(days=R.randint(0, 400))); e2 = min(end, ce + dt.timedelta(days=R.randint(0, 200)))
                    if e2 <= start: e2 = start + dt.timedelta(days=300)
                    ov_s, ov_e = max(start, cs), min(e2, ce)
                    lvl = R.choices(["employed_during", "on_deal", "used_product"], weights=[0.5, 0.25, 0.25])[0]
                    employment.append({"person_id": pid, "contact_id": cid, "company": pc["Name"], "company_domain": pc["Domain"], "title": R.choice(TITLES[persona][R.choice(SENIORITY[1:])]), "start": start.isoformat(), "end": e2.isoformat(), "is_current": "false"})
                    past_links.append({"contact_id": cid, "person_id": pid, "former_account_id": pc["Id"], "former_account_status": pc["Type"], "overlap_start": ov_s.isoformat(), "overlap_end": ov_e.isoformat(), "evidence_level": lvl})
                    c["_past"] = (pc, lvl, start, e2)
                    end = start - dt.timedelta(days=R.randint(10, 120)); continue
                employment.append({"person_id": pid, "contact_id": cid, "company": f"{R.choice(CO_A)} {R.choice([w for ws in CO_B.values() for w in ws])}", "company_domain": "", "title": R.choice(TITLES[persona][R.choice(SENIORITY)]), "start": start.isoformat(), "end": end.isoformat(), "is_current": "false"})
                end = start - dt.timedelta(days=R.randint(10, 120))
    # duplicates and orphans
    for c in R.sample(contacts, int(len(contacts) * M["duplicate_contacts"])):
        d = dict(c); d["Id"] = ID("003"); d["Email"] = c["Email"].replace(".", "_", 1) if R.random() < 0.5 else c["Email"]; d["Title"] = TYPO(c["Title"]); d["_dup_of"] = c["Id"]
        contacts.append(d); mess("duplicate_contact", "contact", d["Id"], f"duplicate of {c['Id']}")
    for c in R.sample(contacts, int(len(contacts) * M["orphan_contacts"])):
        mess("orphan_contact", "contact", c["Id"], f"AccountId was {c['AccountId']}"); c["AccountId"] = ""
    return contacts, truth_persona, employment, past_links, li

# ----------------------------------------------------------------------------- 4. history (2026)
def gen_history(accounts, contacts):
    H = CFG["history"]
    seqs = [{"id": f"seq_{i+1}", "name": n, "steps": 13 if "li" in n else (10 if "call" in n else 5), "email_steps": 5, "call_steps": 5 if "call" in n else 0, "li_steps": 3 if "li" in n else 0} for i, n in enumerate(H["sequences"])]
    eligible = [c for c in contacts if c["_acc"]["_type"] == "prospect" and c["IsDeleted"] == "false" and c["AccountId"] and c["Email"] and c["_estat"] != "none" and "_dup_of" not in c and c["HasOptedOutOfEmail"] == "false"]
    touched = R.sample(eligible, int(len(eligible) * H["touched_share_of_contacts"]))
    # mess: a few opted-out contacts were sequenced anyway
    opted = [c for c in contacts if c["HasOptedOutOfEmail"] == "true" and c["_acc"]["_type"] == "prospect" and c["Email"] and "_dup_of" not in c]
    touched += R.sample(opted, min(len(opted), int(len(contacts) * CFG["mess"]["opted_out_but_in_sequence"])))
    # hidden lift per (band, persona): meeting share / contact share, times seniority lift
    lift = {}
    for b in BANDS:
        for p in PERSONAS:
            lift[(b, p)] = CFG["persona_meeting_share"][b][p] / CFG["persona_contact_share"][b][p]
    L = [lift[(c["_band"], c["_persona"])] * CFG["seniority_meeting_lift"][c["_sen"]] for c in touched]
    base = H["nb_opps_target"] / sum(L)
    prospects_o, states, mailings, tasks, events, opps, ocr, cal = [], [], [], [], [], [], [], []
    h_start = dt.date(2026, 1, 5); h_end = AS_OF - dt.timedelta(days=3)
    meeting_takers = []
    realized = defaultdict(lambda: [0, 0])
    for c, l in zip(touched, L):
        seq = R.choices(seqs, weights=[0.15, 0.2, 0.65])[0]
        start = rand_date(h_start, h_end)
        pid = ID("pro")
        prospects_o.append({"id": pid, "contact_id": c["Id"], "email": c["Email"], "first_name": c["FirstName"], "last_name": c["LastName"], "title": c["Title"], "company": c["_acc"]["Name"], "owner": c["OwnerId"], "custom10": "", "custom11": "", "custom12": "", "custom13": "", "custom14": ""})
        email_days = [0, 2, 4, 7, 9]; call_days = [1, 3, 5, 6, 8]
        bounced = c["_estat"] == "unverified" and R.random() < 0.35 or (c["_estat"] == "catch_all" and R.random() < 0.08)
        reply_at, reply_kind = None, ""
        p_meet = min(0.6, base * l); realized[(c["_band"], c["_persona"])][1] += 1
        for i, d in enumerate(email_days):
            sent = business_days_after(start, d)
            if sent > AS_OF: break
            m = {"id": ID("mail"), "prospect_id": pid, "sequence_id": seq["id"], "step": i + 1, "thread": "A" if i < 3 else "B", "sent_at": rand_dt(sent, sent).isoformat(), "opened": "false", "clicked": "false", "replied": "false", "bounced": "false", "mailbox": f"{c['OwnerId']}@pave-pool-{R.randint(1, 4)}.com"}
            if bounced and i == 0: m["bounced"] = "true"; mailings.append(m); break
            if R.random() < 0.38: m["opened"] = "true"
            if m["opened"] == "true" and R.random() < 0.12: m["clicked"] = "true"
            if not reply_at and R.random() < H["reply_rate"] * (1.6 if i == 0 else 1.0):
                m["replied"] = "true"; reply_at = sent + dt.timedelta(days=R.randint(0, 2))
                reply_kind = "positive" if R.random() < H["positive_reply_share"] else R.choice(["not_interested", "not_interested", "ooo", "referral", "unsubscribe"])
            mailings.append(m)
            tasks.append({"Id": ID("00T"), "WhoId": c["Id"], "WhatId": c["AccountId"], "Subject": f"Email: step {i+1}", "TaskSubtype": "Email", "ActivityDate": sent.isoformat(), "Status": "Completed", "OwnerId": c["OwnerId"]})
            if reply_at: break
        if seq["call_steps"] and (c["Phone"] or c["MobilePhone"]) and not bounced:
            for d in call_days:
                cd = business_days_after(start, d)
                if cd > AS_OF or (reply_at and cd > reply_at): break
                tasks.append({"Id": ID("00T"), "WhoId": c["Id"], "WhatId": c["AccountId"], "Subject": "Call", "TaskSubtype": "Call", "ActivityDate": cd.isoformat(), "Status": "Completed", "CallDisposition": R.choices(["No Answer", "Left Voicemail", "Connected", "Gatekeeper", "Wrong Number"], weights=[50, 25, 15, 7, 3])[0], "OwnerId": c["OwnerId"]})
        seq_end = business_days_after(start, 9)
        state = "bounced" if bounced else ("replied" if reply_at else ("active" if seq_end > AS_OF else "finished"))
        states.append({"id": ID("ss"), "prospect_id": pid, "sequence_id": seq["id"], "state": state, "reply_kind": reply_kind, "started_at": start.isoformat(), "finished_at": (reply_at or min(seq_end, AS_OF)).isoformat(), "owner": c["OwnerId"]})
        c["LastActivityDate"] = (reply_at or min(seq_end, AS_OF)).isoformat()
        if c["HasOptedOutOfEmail"] == "true": mess("opted_out_but_in_sequence", "contact", c["Id"], "sequenced despite opt-out")
        # meeting?
        if not bounced and reply_kind != "unsubscribe" and R.random() < p_meet:
            realized[(c["_band"], c["_persona"])][0] += 1
            mdate = (reply_at or business_days_after(start, R.randint(2, 12))) + dt.timedelta(days=R.randint(2, 9))
            if mdate > AS_OF + dt.timedelta(days=10): mdate = AS_OF - dt.timedelta(days=R.randint(1, 20))
            oid = ID("006"); amt = int(CFG["customers"]["arr_by_band"][c["_band"]] * R.uniform(0.6, 1.5))
            age = (AS_OF - mdate).days
            stage = "Discovery" if age < 14 else R.choices(["Demo", "Proposal", "Closed Won", "Closed Lost"], weights=[30, 20, 15, 35])[0]
            opps.append({"Id": oid, "AccountId": c["AccountId"], "Name": f"{c['_acc']['Name']} - New Business", "Type": "New Business", "StageName": stage, "Amount": amt,
                         "CreatedDate": mdate.isoformat(), "CloseDate": (mdate + dt.timedelta(days=75)).isoformat(), "IsWon": "true" if stage == "Closed Won" else "false", "OwnerId": f"ae_{R.randint(1, 4)}", "SDR__c": c["OwnerId"], "LeadSource": "Outbound"})
            if R.random() < 0.7: ocr.append({"Id": ID("00K"), "OpportunityId": oid, "ContactId": c["Id"], "Role": "Decision Maker" if c["_sen"] in ("C", "VP", "Director") else "Evaluator", "IsPrimary": "true"})
            evid = ID("00U")
            events.append({"Id": evid, "WhoId": c["Id"], "WhatId": oid, "Subject": "Intro call - Pave", "StartDateTime": rand_dt(mdate, mdate).isoformat(), "DurationInMinutes": 30, "OwnerId": f"ae_{R.randint(1, 4)}"})
            att = c["Email"]; kind = "clean"
            r = R.random()
            if r < 0.05: att = f"{c['FirstName']}.{c['LastName']}{R.randint(1,99)}@gmail.com".lower(); kind = "personal_email"
            elif r < 0.08: att = f"{c['FirstName'][0]}{c['LastName']}@{R.choice(['agencypartners.com','talentbridge.co','outsourcedhr.com'])}".lower(); kind = "agency"
            elif r < 0.10: att = c["Email"].split("@")[0] + "@" + c["_acc"]["Domain"].replace(".", "-labs.", 1); kind = "subsidiary"
            cal.append({"event_id": evid, "title": "Intro call - Pave", "start": events[-1]["StartDateTime"], "organizer": f"{events[-1]['OwnerId']}@pave.com", "attendees": json.dumps([f"{events[-1]['OwnerId']}@pave.com", att]), "resolution_kind": kind, "true_contact_id": c["Id"], "true_account_id": c["AccountId"]})
            meeting_takers.append({"opportunity_id": oid, "contact_id": c["Id"], "persona": c["_persona"], "seniority": c["_sen"], "band": c["_band"], "meeting_date": mdate.isoformat()})
    truth_lift = [{"band": b, "persona": p, "hidden_lift": round(lift[(b, p)], 4), "touched": realized[(b, p)][1], "meetings": realized[(b, p)][0],
                   "realized_rate": round(realized[(b, p)][0] / realized[(b, p)][1], 5) if realized[(b, p)][1] else ""} for b in BANDS for p in PERSONAS]
    return seqs, prospects_o, states, mailings, tasks, events, opps, ocr, cal, meeting_takers, truth_lift, touched

# ----------------------------------------------------------------------------- 5. gong
PAIN_LINES = {
 "no_reliable_market_data": ["Honestly we don't trust the survey data we have, it's a year old by the time we get it.", "We're basically guessing on market rates for half our roles."],
 "stale_survey_data": ["Our survey refresh is annual and the market moves faster than that.", "By the time the survey comes out the numbers are already stale."],
 "geo_differentials": ["We can't figure out how to pay people in Denver versus the Bay Area.", "Geo differentials are a constant argument with hiring managers."],
 "startup_equity_benchmarks": ["Nobody can tell us what a Series B company should be granting for equity.", "Equity benchmarks are the thing we have zero data on."],
 "budget_uncertainty": ["We don't know what the merit budget should be this year, finance keeps asking.", "Every year the budget number is a negotiation with no data behind it."],
 "headcount_planning": ["We're planning headcount for next year and have no idea what those roles will cost.", "Headcount planning is a spreadsheet with made-up salaries."],
 "merit_cycle_modeling": ["Modeling the merit cycle takes us six weeks in spreadsheets.", "Our merit cycle is a spreadsheet with forty tabs and one person who understands it."],
 "range_design": ["We don't have real salary ranges, we have whatever people were hired at.", "Building ranges from scratch is the project I keep pushing."],
 "salary_reprices": ["We had to reprice engineering twice last year and it was chaos.", "Repricing is reactive, someone gets an offer and we scramble."],
 "retention_adjustments": ["We lost two senior engineers to offers we couldn't match in time.", "Retention adjustments are all one-offs with no consistency."],
 "pay_compression": ["New hires are coming in above tenured people and it's getting noticed.", "Compression is the thing managers complain about most."],
 "promotion_bands": ["Promotions don't map to bands so every one is a negotiation.", "We don't have a promotion framework tied to pay."],
 "pay_transparency_law": ["The pay transparency laws mean we have to post ranges and we don't have defensible ones.", "Transparency legislation is forcing our hand on ranges."],
 "manager_enablement": ["Managers can't explain pay decisions to their teams.", "We need to give managers something to say when someone asks about their comp."],
 "employee_questions": ["Employees ask how their pay was decided and we don't have a good answer.", "The number one question in our engagement survey is about pay fairness."],
 "offer_explanations": ["Candidates push back on offers and recruiters can't explain the number.", "We lose candidates because the offer conversation is all vibes."],
}
OBJ_LINES = {"budget": "We don't have budget for this until next fiscal year.", "timing": "Timing's tough, we're mid merit cycle right now.", "have_a_survey_vendor": "We already pay for a survey, I'd have to justify a second source.",
             "too_small": "We're probably too small to need a tool like this.", "using_spreadsheets": "Our spreadsheets work fine for now.", "need_finance_buyin": "I'd need finance on board before I could move on this."}
FILLER_REP = ["Thanks for making the time today.", "Can you walk me through how comp decisions get made today?", "How often are you refreshing market data?", "Who else is involved when ranges change?", "What happens when a hiring manager pushes back on a number?", "Let me show you how the benchmarking view works.", "What would need to be true for this to be a priority this quarter?", "Makes sense. What's the process when someone gets a competing offer?"]
FILLER_PROSPECT = ["Sure, happy to.", "It's mostly me and one analyst.", "Quarterly if we're lucky.", "Finance and the CEO for anything over a certain amount.", "It usually escalates to me.", "Okay, that's useful.", "Let me think about that.", "We'd want to see it work for our roles first."]

def gen_gong(accounts, contacts, touched, opps, meeting_takers):
    G = CFG["gong"]; months = CFG["history_months"]
    by_cid = {c["Id"]: c for c in contacts}
    cust_contacts = [c for c in contacts if c["_acc"]["_type"] == "customer" and c["Email"]]
    with_phone = [c for c in touched if c["Phone"] or c["MobilePhone"]]
    mt_by_opp = {m["opportunity_id"]: m for m in meeting_takers}
    calls, planted = [], []
    tpath = os.path.join(OUT, "gong", "transcript.jsonl"); os.makedirs(os.path.dirname(tpath), exist_ok=True); tf = open(tpath, "w")  # noqa: SIM115 (held open across the whole call loop)
    n_total = G["calls_per_month"] * months
    transcript_from = AS_OF - dt.timedelta(days=30 * G["months_of_transcripts"])
    fams = G["pain_families"]
    for i in range(n_total):
        ctype = wchoice(G["call_types"]); when = rand_dt(dt.date(2026, 1, 5), AS_OF)
        if ctype == "cold_call": c = R.choice(with_phone); rep = c["OwnerId"]
        elif ctype == "demo" and opps and R.random() < 0.6: o = R.choice(opps); c = by_cid[mt_by_opp[o["Id"]]["contact_id"]]; rep = o["OwnerId"]
        elif R.random() < 0.35: c = R.choice(cust_contacts); rep = c["_acc"]["OwnerId"]
        else: c = by_cid[R.choice(meeting_takers)["contact_id"]] if meeting_takers else R.choice(touched); rep = f"ae_{R.randint(1,4)}"
        cid = ID("call")
        call = {"id": cid, "type": ctype, "started": when.isoformat(), "duration_minutes": G["duration_minutes"][ctype] + R.randint(-5, 8) if ctype != "cold_call" else R.randint(1, 9),
                "rep": rep, "direction": "outbound", "contact_id": c["Id"], "contact_email": c["Email"], "crm_account_id": c["AccountId"], "crm_contact_id": c["Id"], "has_transcript": "false"}
        if when.date() >= transcript_from and ctype != "cold_call" or (ctype == "cold_call" and when.date() >= transcript_from and R.random() < 0.3):
            call["has_transcript"] = "true"
            turns = []; n_turns = 8 if ctype == "cold_call" else R.randint(28, 44)
            pains = R.sample([(f, s) for f, subs in fams.items() for s in subs], R.randint(1, 3) if ctype != "cold_call" else R.randint(0, 1))
            objs = R.sample(G["objections"], R.randint(0, 2)); comp = R.choice(G["competitors"]) if R.random() < 0.35 else None
            items = [("pain", f, s, R.choice(PAIN_LINES[s])) for f, s in pains] + [("objection", "objection", o, OBJ_LINES[o]) for o in objs]
            if comp: items.append(("competitor", "competitor", comp, f"We've looked at {comp} before, it didn't stick."))
            prospect_turns = [t for t in range(3, n_turns - 2) if t % 2 == 1]   # planted lines are always the prospect's
            items = items[:len(prospect_turns)]
            inserts = dict(zip(R.sample(prospect_turns, len(items)), items))
            for t in range(n_turns):
                spk = "rep" if t % 2 == 0 else "prospect"
                if t in inserts:
                    _kind, fam, val, line = inserts[t]
                    turns.append({"t": t, "speaker": spk, "start_s": t * 40, "text": line})
                    planted.append({"call_id": cid, "family": fam, "value": val, "evidence_quote": line, "speaker": spk, "turn": t})
                    continue
                turns.append({"t": t, "speaker": spk, "start_s": t * 40, "text": R.choice(FILLER_REP if spk == "rep" else FILLER_PROSPECT)})
            tf.write(json.dumps({"call_id": cid, "turns": turns}) + "\n")
        calls.append(call)
    tf.close()
    return calls, planted

# ----------------------------------------------------------------------------- 6. bigquery
DATALAB_Q = ["what should our merit budget be for 2026", "how do we handle a salary reprice for engineering", "span of control benchmarks for engineering managers",
             "how much should we raise ranges after a pay transparency law", "what is a typical promotion increase percentage", "how do series B companies structure equity refreshes",
             "geo differential for remote employees", "how to explain pay bands to employees", "average merit increase tech companies 2026", "compression between new hires and tenured staff",
             "how to build salary ranges from scratch", "bonus target by level for product managers", "what percent of companies did an off-cycle adjustment", "how to price a director of product in austin"]

def gen_bigquery(accounts, contacts):
    B = CFG["bigquery"]; users, searches, lab = [], [], []
    by_acc = defaultdict(list)
    for c in contacts:
        if c["AccountId"] and c["Email"]: by_acc[c["AccountId"]].append(c)
    n_acc = int(len(accounts) * B["accounts_with_usage_share"])
    weights = [B["users_band_share"][a["HeadcountBand__c"]] for a in accounts]
    chosen = set()
    while len(chosen) < n_acc: chosen.add(R.choices(range(len(accounts)), weights=weights, k=1)[0])
    fam_titles = [(f, t) for f, d in FAM.items() if d["covered"] for t in d["titles"]]
    for ix in chosen:
        a = accounts[ix]
        for _ in range(R.randint(*B["users_per_account"])):
            link = R.random() < 0.6 and by_acc[a["Id"]]
            c = R.choice(by_acc[a["Id"]]) if link else None
            email = c["Email"] if c else f"{R.choice(FIRST)}.{R.choice(LAST)}@{a['Domain']}".lower()
            uid = ID("usr"); first = rand_dt(dt.date(2025, 6, 1), AS_OF - dt.timedelta(days=5)); last = rand_dt(first.date(), AS_OF)
            users.append({"user_id": uid, "email": email, "domain": a["Domain"], "matched_contact_id": c["Id"] if c else "", "first_seen": first.isoformat(), "last_seen": last.isoformat(), "plan": "market_data_free"})
            for _ in range(R.randint(*B["searches_per_user"])):
                f, t = R.choice(fam_titles)
                searches.append({"search_id": ID("srch"), "user_id": uid, "searched_at": rand_dt(first.date(), last.date()).isoformat(), "job_family": f, "title_query": t,
                                 "location_1": "US All", "location_2": R.choice(B["locations"][1:]), "valuation_filter": R.choice(B["valuation_filters"])})
            for _ in range(R.choice([0, 0, 1, 1, 2, 4])):
                lab.append({"query_id": ID("lab"), "user_id": uid, "asked_at": rand_dt(first.date(), last.date()).isoformat(), "raw_text": R.choice(DATALAB_Q)})
    return users, searches, lab

# ----------------------------------------------------------------------------- 7. job postings
def gen_jobs(accounts):
    J = CFG["job_postings"]; rows = []
    covered = [(f, t) for f, d in FAM.items() if d["covered"] for t in d["titles"]]
    uncovered = [(f, t) for f, d in FAM.items() if not d["covered"] for t in d["titles"]]
    for a in accounts:
        if R.random() > J["accounts_with_open_roles_share"]: continue
        rel = J["dataset_relevance_by_industry"].get(a["Industry"], J["dataset_relevance_by_industry"]["default"])
        for _ in range(R.randint(*J["open_roles_per_account"])):
            f, t = R.choice(covered) if R.random() < rel else R.choice(uncovered)
            posted = rand_date(AS_OF - dt.timedelta(days=120), AS_OF)
            rows.append({"posting_id": ID("job"), "account_id": a["Id"], "domain": a["Domain"], "title": t, "job_family": f, "level": R.choice(["IC", "Senior", "Lead", "Manager", "Director"]),
                         "location": R.choice(CFG["bigquery"]["locations"][1:]), "posted_at": posted.isoformat(), "url": f"https://boards.example/{a['Domain']}/{ID('j')}",
                         "excerpt": f"{a['Name']} is hiring a {t} to join our {f} team.", "dataset_covered": "true" if FAM[f]["covered"] else "false"})
    return rows

# ----------------------------------------------------------------------------- 8. rep sent emails (voice corpus)
def gen_sent_emails():
    """Two distinct rep voices for the voice-profile extractor. Own Random stream: adding or editing
    these never shifts the rest of the world."""
    VR = random.Random("rep-voice")

    def co():
        return f"{VR.choice(CO_A)} {VR.choice([w for ws in CO_B.values() for w in ws])}"
    t1_open = ["Saw {co} is hiring two comp analysts.", "Quick one.", "Ranges came up twice on calls this week.",
               "Your team posted a rewards role Friday.", "New CFO usually means new comp questions.",
               "Merit cycle season is close.", "Comp reviews eat quarters."]
    t1_mid = ["Pave prices roles against live data, not last year's survey.", "We cut range-building from weeks to days.",
              "Numbers both sides trust, one source.", "Live percentiles beat stale survey matches.",
              "Budget talks go faster with shared numbers."]
    t1_q = ["Worth 15 minutes?", "Who owns ranges at {co}?", "Is this on your plate this quarter?",
            "Open to a quick look?", "Should I send a two-line summary instead?"]
    t2_open = ["I was reading about {co}'s growth and it made me think about how comp planning usually gets harder right at this stage.",
               "I've been talking with a few people teams your size lately, and the same compensation questions keep coming up.",
               "I noticed {co} has been hiring steadily, which usually means pay ranges are getting a fresh look."]
    t2_mid = ["What we hear most often is that the hardest part isn't the numbers themselves, it's getting everyone to agree on where they came from.",
              "Pave connects live market data straight into your planning, so the conversation starts from numbers everyone already trusts.",
              "Teams tell us the biggest relief is walking into a budget review with one source of truth instead of three spreadsheets."]
    t2_q = ["Would it be helpful to see how that looks with your own roles?", "Would a short walkthrough be useful sometime in the next couple of weeks?",
            "Is compensation planning something on your radar this quarter?"]
    template_body = ("{first} — saw {co} brought on a new CFO recently.\n\nMost finance leaders spend their first quarter "
                     "asking how pay decisions are defended. Pave prices your roles against live market data.\n\n"
                     "When your new CFO asks how ranges were set, what's the answer today?")
    rows = []
    for rep, signoff in (("sdr_1", "— Rep One"), ("sdr_2", "Warm regards,\nRep Two")):
        for i in range(40):
            is_template = i >= 30
            first, company = VR.choice(FIRST), co()
            if is_template:
                subject, body = f"new CFO at {company}", template_body.format(first=first, co=company)
            elif rep == "sdr_1":
                subject = VR.choice(["ranges", "comp question", "quick one", "pricing roles", "merit cycle"])
                body = f"{first} —\n\n{VR.choice(t1_open).format(co=company)} {VR.choice(t1_mid)}\n\n{VR.choice(t1_q).format(co=company)}\n\n{signoff}"
            else:
                subject = VR.choice([f"A thought on compensation planning at {company}", f"How {company} might simplify comp reviews",
                                     "Hoping this is useful as planning season approaches"])
                body = (f"Hi {first},\n\nHope your week's going well! {VR.choice(t2_open).format(co=company)} "
                        f"{VR.choice(t2_mid)} {VR.choice(t2_mid)}\n\n{VR.choice(t2_q)} No worries at all if the "
                        f"timing isn't right — happy to circle back whenever works.\n\n{signoff}")
            rows.append({"id": f"sent_{rep}_{i + 1:03d}", "rep": rep, "to_name": first, "subject": subject, "body": body,
                         "sent_at": (dt.date(2026, 1, 5) + dt.timedelta(days=VR.randint(0, 240))).isoformat(),
                         "replied": "true" if not is_template and VR.random() < 0.4 else "false",
                         "is_template": "true" if is_template else "false"})
    voices = {"sdr_1": {"style": "terse_direct", "greeting": "dash", "signoff": "— Rep One", "target_words": [25, 60]},
              "sdr_2": {"style": "warm_long", "greeting": "hi_hope", "signoff": "Warm regards, Rep Two", "target_words": [90, 150]}}
    return rows, voices

# ----------------------------------------------------------------------------- main
def main():
    if os.path.exists(OUT): shutil.rmtree(OUT)
    os.makedirs(OUT)
    accounts, contracts = gen_accounts()
    org = gen_org_shape(accounts)
    contacts, truth_persona, employment, past_links, li = gen_contacts(accounts)
    seqs, prospects_o, states, mailings, tasks, events, opps, ocr, cal, takers, truth_lift, touched = gen_history(accounts, contacts)
    calls, planted = gen_gong(accounts, contacts, touched, opps, takers)
    users, searches, lab = gen_bigquery(accounts, contacts)
    jobs = gen_jobs(accounts)
    sent_emails, rep_voices = gen_sent_emails()

    # salesforce
    dump_csv("salesforce/account.csv", accounts, ["Id", "Name", "Website", "Domain", "Industry", "NumberOfEmployees", "HeadcountBand__c", "Type", "BillingCountry", "OwnerId", "AccountScore__c", "CreatedDate", "IsDeleted"])
    dump_csv("salesforce/contract.csv", contracts)
    dump_csv("salesforce/contact.csv", contacts, ["Id", "AccountId", "FirstName", "LastName", "Title", "Email", "Phone", "MobilePhone", "HasOptedOutOfEmail", "DoNotCall", "StillAtCompany__c", "LastActivityDate", "OwnerId", "CreatedDate", "IsDeleted"])
    dump_csv("salesforce/opportunity.csv", opps); dump_csv("salesforce/opportunity_contact_role.csv", ocr)
    dump_csv("salesforce/event.csv", events); dump_csv("salesforce/task.csv", tasks, ["Id", "WhoId", "WhatId", "Subject", "TaskSubtype", "ActivityDate", "Status", "CallDisposition", "OwnerId"])
    # outreach
    dump_csv("outreach/sequence.csv", seqs); dump_csv("outreach/prospect.csv", prospects_o); dump_csv("outreach/sequence_state.csv", states); dump_csv("outreach/mailing.csv", mailings)
    dump_csv("outreach/sent_email.csv", sent_emails, ["id", "rep", "to_name", "subject", "body", "sent_at", "replied", "is_template"])
    # enrichment
    dump_csv("enrichment/employment_history.csv", employment); dump_csv("enrichment/org_shape.csv", org); dump_csv("enrichment/linkedin_activity.csv", li); dump_csv("enrichment/job_posting.csv", jobs)
    # bigquery, gong, calendar
    dump_csv("bigquery/users.csv", users); dump_csv("bigquery/searches.csv", searches); dump_csv("bigquery/datalab_queries.csv", lab)
    dump_csv("gong/call.csv", calls); dump_csv("calendar/event.csv", cal)
    # benchmarks: realized medians from customers vs config
    bench = []
    for b in BANDS:
        for fn in CFG["org_shape"]["medians"][b]:
            vals = sorted(a["_org"][fn] for a in accounts if a["_type"] == "customer" and a["HeadcountBand__c"] == b)
            med = vals[len(vals) // 2] if vals else ""
            bench.append({"size_band": b, "function": fn, "median_headcount_config": CFG["org_shape"]["medians"][b][fn], "median_headcount_realized": med, "n_customers": len(vals)})
    dump_csv("benchmarks/customer_benchmark.csv", bench)
    # truth
    dump_csv("truth/persona_size_lift.csv", truth_lift); dump_csv("truth/contact_persona.csv", truth_persona); dump_csv("truth/past_customer_links.csv", past_links)
    dump_csv("truth/meeting_takers.csv", takers); dump_csv("truth/planted_call_tags.csv", planted); dump_csv("truth/injected_mess.csv", MESS, ["kind", "entity", "id", "note"])
    shutil.copy(os.path.join(HERE, "seed.yaml"), os.path.join(OUT, "truth", "seed.yaml"))
    with open(os.path.join(OUT, "truth", "rep_voice.json"), "w") as vf:
        json.dump(rep_voices, vf, indent=2)
    # summary
    summ = {"accounts": Counter(a["Type"] for a in accounts), "bands": Counter(a["HeadcountBand__c"] for a in accounts), "industries": Counter(a["Industry"] for a in accounts),
            "contacts": len(contacts), "contacts_touched_2026": len(touched), "nb_opportunities_2026": len(opps), "mailings": len(mailings), "tasks": len(tasks),
            "gong_calls": len(calls), "gong_transcripts": sum(1 for c in calls if c["has_transcript"] == "true"), "planted_tags": len(planted),
            "bigquery_users": len(users), "searches": len(searches), "datalab_queries": len(lab), "job_postings": len(jobs), "past_customer_links": len(past_links), "mess_rows": len(MESS),
            "sent_emails": len(sent_emails)}
    with open(os.path.join(OUT, "truth", "summary.json"), "w") as sf:
        json.dump({k: (dict(v) if isinstance(v, Counter) else v) for k, v in summ.items()}, sf, indent=2)
    for k, v in summ.items(): print(f"{k:28} {dict(v) if isinstance(v, Counter) else v}")

if __name__ == "__main__":
    main()
