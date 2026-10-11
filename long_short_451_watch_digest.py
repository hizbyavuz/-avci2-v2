"""Readable per-symbol 451 observation notices. Never confirms a trade."""
from __future__ import annotations

import math


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _fmt(value):
    number = _number(value)
    return f"{number:.10g}" if number is not None else None


def format_watch_digest(analyses, limit=5):
    messages = []
    for analysis in analyses:
        payload = getattr(analysis, "payload", None) or {}
        decision = payload.get("direction_engine") or {}
        if not decision.get("observation_only") or decision.get("eligible"):
            continue
        direction = decision.get("direction")
        if direction not in ("LONG", "SHORT"):
            continue
        symbol = str(getattr(analysis, "symbol", "")).strip()
        price = _fmt(getattr(analysis, "price", None))
        if not symbol or price is None:
            continue

        plan = payload.get("setup_plan") or {}
        # The analyst's setup plan can be for a different preferred side.
        # Never publish its trigger/SL/TP under the wrong direction.
        plan = plan if plan.get("direction") == direction else {}
        trigger = _fmt(plan.get("trigger_level"))
        stop = _fmt(plan.get("invalidation"))
        tp1 = _fmt(plan.get("target1"))
        tp2 = _fmt(plan.get("target2"))
        lines = [f"{'🟢' if direction == 'LONG' else '🔴'} {direction} İÇİN İZLE | {symbol}"]
        if trigger:
            side = "üstünde" if direction == "LONG" else "altında"
            lines.append(f"5 dk mum {trigger} {side} kapanırsa {direction} güçlenir.")
        else:
            lines.append("5 dk teyit seviyesi henüz hesaplanamadı.")
        lines.append(f"Şu an fiyat: {price}")
        if trigger:
            lines.append(
                f"Beklenen: {trigger} {'üstü' if direction == 'LONG' else 'altı'} kapanış → "
                f"ardından seviyeyi {'koruması' if direction == 'LONG' else 'altında kalması'}."
            )
        lines.append("Durum: 🟡 Henüz işlem teyidi değil; kapanış, yapı/alan ve risk teyidi bekleniyor.")
        if stop:
            lines.append(f"🛡️ SL (izleme/geçersizlik): {stop}")
        if tp1:
            lines.append(f"🎯 TP1 (teorik): {tp1}")
        if tp2:
            lines.append(f"🎯 TP2 (teorik): {tp2}")
        if not (stop and tp1 and tp2):
            lines.append("SL/TP: Eksik seviyeler için doğrulanmış hesap yok.")
        lines.append("⚠️ Binance Futures 451: Bazı türev verileri doğrulanamadı. İşlem teyidi DEĞİL.")
        messages.append("\n".join(lines))
        if len(messages) >= max(1, min(5, int(limit))):
            break
    return "\n\n".join(messages) if messages else None
