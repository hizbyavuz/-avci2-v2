"""Telegram mesaj metinleri (düz Türkçe)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import config as C
from .gates import fmt

IST = timezone(timedelta(hours=3))


def coin(contract: str) -> str:
    return contract.replace("_USDT", "")


def hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%d.%m %H:%M")


def signal(sid: int, contract: str, d, notional: float) -> str:
    icon = "🟢" if d.side == "LONG" else "🔴"
    sl_pct = abs(d.entry - d.sl) / d.entry * 100
    tp_pct = abs(d.tp - d.entry) / d.entry * 100
    sign_sl, sign_tp = ("−", "+") if d.side == "LONG" else ("+", "−")
    lines = [
        f"{icon} {d.side} — {coin(contract)}  (Gate vadeli)",
        "",
        f"Giriş:  {fmt(d.entry)}",
        f"Stop:   {fmt(d.sl)}  ({sign_sl}%{sl_pct:.2f})",
        f"Hedef:  {fmt(d.tp)}  ({sign_tp}%{tp_pct:.2f})  → {d.rr:.1f}R",
        "",
        f"{C.RISK_USD:.0f}$ risk için pozisyon büyüklüğü: {notional:,.0f}$",
        f"Stop olursa: −{C.RISK_USD:.0f}$   Hedef olursa: ≈ +{C.RISK_USD * d.net_rr:.0f}$ (masraf sonrası)",
        "",
        "Neden:",
    ]
    lines += [f"• {r}" for r in d.reasons]
    lines += [
        "",
        f"⏱ {C.TIMEOUT_HOURS:.0f} saatte hedef/stop olmazsa piyasadan kapat.",
        f"⚠️ Fiyat girişten %{min(0.3, sl_pct/3):.2f}'den fazla kaçtıysa bu sinyale girme.",
        f"Defter #{sid} · Otomatik emir yok",
    ]
    return "\n".join(lines)


def outcome(row, status: str, exit_price: float, pnl: float, totals: dict) -> str:
    icon = {"TP": "✅ HEDEF", "SL": "❌ STOP", "TIMEOUT": "⏱ SÜRE DOLDU"}[status]
    return "\n".join([
        f"{icon} — #{row['id']} {coin(row['contract'])} {row['side']}",
        f"Giriş {fmt(row['entry'])} → Çıkış {fmt(exit_price)}",
        f"Sonuç: {pnl:+.2f}$  ({C.RISK_USD:.0f}$ risk ile, masraf dahil)",
        "",
        f"Defter toplamı: {totals['n']} işlem, {totals['wins']} kazanç, {totals['pnl']:+.2f}$",
    ])


def summary(title: str, s: dict, scans: int | None = None) -> str:
    lines = [f"📊 {title.upper()} | SİSTEM DURUMU"]
    if scans is not None:
        lines.append(f"🔎 Son 24 saat tarama: {scans}")
    lines.append(f"📌 Açık işlem: {s['open']}")
    if not s["n"]:
        lines.append("⏳ Henüz sonuçlanan işlem yok.")
    else:
        wr = s["wins"] / s["n"] * 100
        lines.append(f"✅ Sonuçlanan: {s['n']} | Kazanan: {s['wins']} (%{wr:.0f})")
        lines.append(f"🎯 Hedef: {s['tp']} | 🛑 Stop: {s['sl']} | ⏱ Süre: {s['timeout']}")
        lines.append(f"💰 Tahmini net sonuç: {s['pnl']:+.2f}$ (sanal takip)")
    return "\n".join(lines)


def reasons_of(row) -> list[str]:
    try:
        return json.loads(row["reasons"] or "[]")
    except ValueError:
        return []
