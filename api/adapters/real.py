"""Real adapters. Each is a stub with the vendor SDK named and the auth it needs. Build one at a time,
with a contract test in tests/contract/ (recorded responses, or a live sandbox where one exists:
Salesforce Developer Edition and the BigQuery sandbox are free)."""
from __future__ import annotations


class _NotBuilt(NotImplementedError):
    def __init__(self, name, needs): super().__init__(f"{name} not built yet. Needs: {needs}. Set config/sources.yaml to fixture.")


class RealSalesforce:
    """simple-salesforce (REST/SOQL) for incremental pulls; Bulk API 2.0 for the initial load.
    Auth: connected app + integration user (OAuth client credentials) -> env SF_CLIENT_ID, SF_CLIENT_SECRET, SF_DOMAIN."""
    def __init__(self, **opts): raise _NotBuilt("RealSalesforce", "connected app + integration user; pip install simple-salesforce")


class RealOutreach:
    """Outreach REST API (JSON:API). Auth: OAuth app -> env OUTREACH_CLIENT_ID/SECRET/REFRESH_TOKEN.
    Rate limits are per-app per-hour; the client must back off and the enrollment path must be idempotent."""
    def __init__(self, **opts): raise _NotBuilt("RealOutreach", "Outreach OAuth app")


class RealGong:
    """Gong API: /v2/calls (list), /v2/calls/extensive (parties + CRM ids), /v2/calls/transcript (<=100 ids per call).
    Auth: access key + secret (basic) -> env GONG_ACCESS_KEY, GONG_SECRET."""
    def __init__(self, **opts): raise _NotBuilt("RealGong", "Gong API key from a Gong admin")


class RealBigQuery:
    """google-cloud-bigquery with a service account. Env GOOGLE_APPLICATION_CREDENTIALS, BQ_PROJECT, BQ_DATASET."""
    def __init__(self, **opts): raise _NotBuilt("RealBigQuery", "service account with read on the usage dataset")


class RealEnrichment:
    """Whichever vendor Pave uses (ZoomInfo / Apollo / Clay / PDL). Employment history needs a person-id keyed API."""
    def __init__(self, **opts): raise _NotBuilt("RealEnrichment", "vendor API key")


class RealCalendar:
    """Google Calendar API (workspace domain-wide delegation) or the booking tool's webhook."""
    def __init__(self, **opts): raise _NotBuilt("RealCalendar", "workspace calendar credentials")


class OutreachSender:
    """Path A: write drafted bodies to Outreach custom fields; Outreach sends within its own limits. Uses RealOutreach."""
    def __init__(self, **opts): raise _NotBuilt("OutreachSender", "RealOutreach")


class PoolSender:
    """Path B: a cold-email platform with an API and managed inboxes (mailbox pool, warmup, rotation, reply sync).
    Guardrails from contracts/guardrails/email.yaml are enforced here, before any send."""
    def __init__(self, **opts): raise _NotBuilt("PoolSender", "sending platform API key + secondary domains")
