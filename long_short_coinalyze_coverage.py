"""Read-only Binance USD-M Coinalyze symbol coverage discovery.

Uses one /future-markets request, no derivatives API quota per coin.
"""
from long_short_coinalyze_adapter import _get

def coverage(symbols):
    requested = tuple(dict.fromkeys(str(s).upper() for s in symbols))
    rows = _get("/future-markets")
    valid = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol_on_exchange")
        if (row.get("symbol") == f"{symbol}_PERP.A"
                and row.get("exchange") == "A"
                and row.get("is_perpetual") is True
                and row.get("quote_asset") == "USDT"
                and row.get("margined") == "STABLE"):
            valid.add(symbol)
    covered = [s for s in requested if s in valid]
    missing = [s for s in requested if s not in valid]
    return {"source":"COINALYZE_BINANCE_A","requested_count":len(requested),
            "covered_count":len(covered),"missing_count":len(missing),
            "covered":covered,"missing":missing}
