"""Pure safety gate for Binance-only derivative evidence.

This module does not fetch data, alter production scoring, or place orders.
"""
REQUIRED = ("oi_change_1h", "funding_pct", "taker_ratio", "long_short_ratio")

def assess(snapshot, funding_pct=None, taker_ratio=None,
           funding_source=None, taker_source=None):
    """Fail closed until all four fields are verified as Binance-derived.

    Coinalyze L/S is provider-defined; its meaning is not automatically the
    Binance globalLongShortAccountRatio endpoint.
    """
    snapshot = snapshot or {}
    missing = []
    if snapshot.get("source") != "COINALYZE_BINANCE_A":
        missing.append("binance_oi_source")
    if not snapshot.get("oi_fresh") or snapshot.get("oi_unit") != "BASE_ASSET":
        missing.append("oi_unit_or_freshness")
    if snapshot.get("oi_change_1h") is None:
        missing.append("oi_change_1h")
    if not snapshot.get("ls_fresh") or snapshot.get("long_short_ratio") is None:
        missing.append("long_short_ratio")
    if funding_pct is None or funding_source != "COINALYZE_BINANCE_A" or not snapshot.get("funding_fresh"):
        missing.append("funding_pct")
    if taker_ratio is None or taker_source != "BINANCE_UM_FUTURES_WEBSOCKET" or not snapshot.get("taker_fresh") or not snapshot.get("taker_coverage_ready"):
        missing.append("taker_ratio")
    return {"ready": not missing, "missing": missing,
            "source": "BINANCE_ONLY", "automatic_orders": False}
