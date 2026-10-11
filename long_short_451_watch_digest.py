"""Format research-only observations from missing Futures data.

Never sends messages, places orders, or changes trade eligibility.
"""
from __future__ import annotations


def format_watch_digest(analyses, limit=5):
    rows = []
    for analysis in analyses:
        payload = getattr(analysis, "payload", None) or {}
        decision = payload.get("direction_engine") or {}
        if not decision.get("observation_only") or decision.get("eligible"):
            continue
        direction = decision.get("direction")
        if direction not in ("LONG", "SHORT"):
            continue
        symbol = str(getattr(analysis, "symbol", "")).strip()
        price = getattr(analysis, "price", None)
        if not symbol or not isinstance(price, (int, float)) or price <= 0:
            continue
        rows.append((symbol, direction, price))
        if len(rows) >= max(1, min(5, int(limit))):
            break
    if not rows:
        return None
    lines = [
        "🟡 LONG/SHORT İZLEME ÖNERİLERİ",
        "⚠️ Binance Futures 451: Bazı türev verileri doğrulanamadı. İşlem teyidi DEĞİL.",
    ]
    for symbol, direction, price in rows:
        lines.append(f"{symbol} | {direction} eğilimi | fiyat: {price:g}")
    return "\n".join(lines)
