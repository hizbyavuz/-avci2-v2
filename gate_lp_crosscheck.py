"""Second Solana LP data source for research; cannot approve a pool by itself."""

import json
import math
import re
from urllib import error, request


SOL_ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}\Z")


def parse_summary(mint, payload):
    if not isinstance(payload, dict):
        return {"status": "UNKNOWN", "reason": "INVALID_RESPONSE"}
    if payload.get("mint") and payload["mint"] != mint:
        return {"status": "UNKNOWN", "reason": "MINT_MISMATCH"}
    try:
        pct = float(payload["lpLockedPct"])
    except (TypeError, ValueError, KeyError):
        return {"status": "UNKNOWN", "reason": "LP_FIELD_MISSING"}
    if not math.isfinite(pct) or not 0 <= pct <= 100:
        return {"status": "UNKNOWN", "reason": "INVALID_PERCENTAGE"}
    # Rugcheck's summary is token-wide, not proof for the exact candidate pool.
    return {"status": "OBSERVED", "source": "Rugcheck",
            "token_lp_locked_pct": pct, "pool_match_verified": False}


def fetch_lp_summary(mint, open_url=request.urlopen):
    if not SOL_ADDRESS.fullmatch(mint or ""):
        return {"status": "UNKNOWN", "reason": "INVALID_MINT"}
    try:
        with open_url(
            f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary",
            timeout=12,
        ) as response:
            return parse_summary(mint, json.load(response))
    except (error.URLError, TimeoutError, ValueError):
        return {"status": "UNKNOWN", "reason": "SOURCE_UNAVAILABLE"}
