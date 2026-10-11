"""Timestamped closed-candle quote for research outcome backfill."""
from datetime import datetime, timezone, timedelta


def closed_quote(symbol, now_utc, candle_fetch):
    now = datetime.fromisoformat(now_utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    data = candle_fetch(symbol, "1m", 25)
    rows = data.get("raw") or []
    if not rows:
        return None
    row = rows[-1]
    price = float(row[4])
    closed_at = datetime.fromtimestamp(int(row[6]) / 1000, timezone.utc)
    if price <= 0 or not now - timedelta(minutes=3) <= closed_at <= now:
        return None
    return price, closed_at.isoformat()
