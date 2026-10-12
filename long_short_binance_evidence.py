"""Conservative Binance-only evidence assembly; no trading or signal changes."""
from long_short_binance_derivative_gate import assess

def evaluate(oi_ls, funding, taker=None):
    oi_ls = oi_ls or {}
    funding = funding or {}
    taker = taker or {}
    # Coinalyze funding is from a verified Binance perpetual, but it is
    # NOT the native BINANCE_USDM source required by the frozen gate.
    gate = assess(
        oi_ls,
        funding_pct=funding.get("funding_pct") if funding.get("funding_fresh") else None,
        funding_source=funding.get("source"),
        taker_ratio=taker.get("taker_ratio") if taker.get("fresh") else None,
        taker_source=taker.get("source"),
    )
    return {"source": "BINANCE_ONLY_RESEARCH", "ready": gate["ready"],
            "missing": gate["missing"], "automatic_orders": False,
            "oi_available": bool(oi_ls.get("oi_fresh") and oi_ls.get("oi_change_1h") is not None),
            "funding_available": bool(funding.get("funding_fresh") and funding.get("funding_pct") is not None),
            "long_short_available": bool(oi_ls.get("ls_fresh") and oi_ls.get("long_short_ratio") is not None),
            "taker_available": bool(taker.get("fresh") and taker.get("taker_ratio") is not None)}
