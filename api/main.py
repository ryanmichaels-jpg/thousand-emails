"""FastAPI entrypoint. Stage 5 adds routers: auth, patch, queue, brief. Stage 7 adds the MCP server."""
from __future__ import annotations
import os
from fastapi import FastAPI
import psycopg
from dotenv import load_dotenv
load_dotenv()
app = FastAPI(title="thousand-emails")


@app.get("/health")
def health():
    ok = True; detail = {}
    try:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            detail["app_tables"] = conn.execute("select count(*) from information_schema.tables where table_schema='app'").fetchone()[0]
            detail["raw_schemas"] = [r[0] for r in conn.execute("select schema_name from information_schema.schemata where schema_name like 'raw_%' order by 1")]
    except Exception as e:
        ok = False; detail["error"] = str(e)
    return {"ok": ok, **detail}
