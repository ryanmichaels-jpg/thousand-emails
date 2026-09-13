#!/usr/bin/env python3
"""Load fixtures/ into Postgres as raw tables (one schema per source, all columns text).

Usage:  DATABASE_URL=postgresql://user:pass@localhost:5432/thousand python load.py
Requires: pip install psycopg   (psycopg 3)
The raw layer is deliberately untyped; dbt models cast and clean.
"""
import csv, json, os, sys, glob
import psycopg

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
url = os.environ.get("DATABASE_URL")
if not url: sys.exit("set DATABASE_URL")

with psycopg.connect(url) as conn, conn.cursor() as cur:
    for path in sorted(glob.glob(os.path.join(OUT, "*", "*.csv"))):
        source = os.path.basename(os.path.dirname(path)); table = os.path.splitext(os.path.basename(path))[0]
        schema = f"raw_{source}"
        with open(path, newline="") as f:
            cols = next(csv.reader(f))
        cur.execute(f'create schema if not exists "{schema}"')
        cur.execute(f'drop table if exists "{schema}"."{table}"')
        cur.execute(f'create table "{schema}"."{table}" ({", ".join(f"\"{c}\" text" for c in cols)}, _synced_at timestamptz default now())')
        with open(path) as f, cur.copy(f'copy "{schema}"."{table}" ({", ".join(f"\"{c}\"" for c in cols)}) from stdin with (format csv, header true)') as cp:
            for chunk in iter(lambda: f.read(1 << 20), ""): cp.write(chunk)
        cur.execute(f'select count(*) from "{schema}"."{table}"'); print(f"{schema}.{table:28} {cur.fetchone()[0]:>8}")
    # transcripts: jsonl -> jsonb rows
    cur.execute("create schema if not exists raw_gong")
    cur.execute("drop table if exists raw_gong.transcript")
    cur.execute("create table raw_gong.transcript (call_id text primary key, turns jsonb, _synced_at timestamptz default now())")
    with open(os.path.join(OUT, "gong", "transcript.jsonl")) as f:
        rows = [(json.loads(l)["call_id"], l) for l in f]
    cur.executemany("insert into raw_gong.transcript (call_id, turns) values (%s, (%s::jsonb)->'turns')", rows)
    print(f"raw_gong.transcript              {len(rows):>8}")
    conn.commit()
