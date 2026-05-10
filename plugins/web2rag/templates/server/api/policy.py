"""Per-site policy endpoints — operator-tunable runtime config.

Today's only knob is `pii_allowlist` (Presidio entity types the site is
allowed to keep verbatim). Future per-site policies (custom prompt-injection
languages, scope-drift threshold overrides, custom system prompts) plug in
here without API surface changes — extend SiteConfig and add the fields to
the PUT body.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.store.sites import get as get_site, upsert as upsert_site

router = APIRouter()

# Common Presidio entity types — surfaced for clients but not enforced; any
# string is accepted so operators can also list custom recognizer names.
_KNOWN_ENTITIES = (
    "PERSON", "ORGANIZATION", "LOCATION",
    "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE",
    "URL", "IP_ADDRESS", "CRYPTO", "DATE_TIME", "MEDICAL_LICENSE",
    "US_SSN", "US_BANK_NUMBER",
)


class PolicyPayload(BaseModel):
    pii_allowlist: list[str] = Field(default_factory=list)


@router.get("/sites/{site_id}/policy")
async def get_policy(site_id: str) -> dict:
    cfg = get_site(site_id)
    if cfg is None:
        raise HTTPException(404, f"site {site_id!r} has never been ingested")
    return {
        "site_id": cfg.site_id,
        "pii_allowlist": cfg.pii_allowlist,
        "known_entity_types": list(_KNOWN_ENTITIES),
    }


@router.put("/sites/{site_id}/policy")
async def put_policy(site_id: str, payload: PolicyPayload) -> dict:
    cfg = get_site(site_id)
    if cfg is None:
        raise HTTPException(404, f"site {site_id!r} has never been ingested — run /ingest first")
    cfg.pii_allowlist = [e.strip().upper() for e in payload.pii_allowlist if e.strip()]
    upsert_site(cfg)
    return {
        "site_id": cfg.site_id,
        "pii_allowlist": cfg.pii_allowlist,
        "note": "policy updated; re-ingest the site for the change to take effect on previously-stored chunks",
    }
