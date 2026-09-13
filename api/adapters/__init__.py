"""get_adapter(name) -> the implementation config/sources.yaml selects. Real* classes are stubs until built."""
from __future__ import annotations

import os
from functools import cache

import yaml

from . import fixture

CONFIG = os.environ.get("SOURCES_CONFIG", os.path.join(os.path.dirname(__file__), "..", "..", "config", "sources.yaml"))

FIXTURE = {
    "salesforce": fixture.FixtureSalesforce, "outreach": fixture.FixtureOutreach, "gong": fixture.FixtureGong,
    "bigquery": fixture.FixtureBigQuery, "enrichment": fixture.FixtureEnrichment, "calendar": fixture.FixtureCalendar,
    "sender": fixture.StubSender, "llm": fixture.FixtureLLM,
}


def llm_settings() -> dict:
    """The llm block of config/sources.yaml: provider, draft_model, tag_model, embed_dim."""
    with open(CONFIG) as f:
        return yaml.safe_load(f)["llm"]


def _real(name: str):
    from . import real  # imported lazily so fixture mode never needs vendor SDKs installed
    return {"salesforce": real.RealSalesforce, "outreach": real.RealOutreach, "gong": real.RealGong, "bigquery": real.RealBigQuery,
            "enrichment": real.RealEnrichment, "calendar": real.RealCalendar, "sender": real.OutreachSender, "llm": real.RealLLM}[name]


@cache
def get_adapter(name: str):
    with open(CONFIG) as f:
        cfg = yaml.safe_load(f)["sources"]
    mode = cfg[name]["mode"]
    if mode == "fixture": return FIXTURE[name]()
    if mode == "real": return _real(name)(**cfg[name].get("options", {}))
    raise ValueError(f"unknown mode {mode!r} for {name}")
