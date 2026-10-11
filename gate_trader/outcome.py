"""İşlem sonucunu hesaplama ve dolar karşılığı.

Kötümser kurallar (kendimizi kandırmamak için):
- Aynı mum içinde hem stop hem hedef görüldüyse STOP sayılır.
- Komisyon + kayma her işlemden düşülür.
- Pozisyon boyu, stop olursa kayıp tam olarak RISK_USD olacak şekilde seçilir.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config as C


@dataclass
class Outcome:
    status: str          # TP / SL / TIMEOUT / OPEN
    exit_price: float | None
    exit_ts: int | None


def simulate(side: str, sl: float, tp: float, t: list[int], h: list[float],
             l: list[float], c: list[float], bar_sec: int, timeout_ts: int) -> Outcome:
    """t/h/l/c: girişten SONRA açılan mumlar (giriş mumu dahil değil)."""
    for k in range(len(c)):
        if t[k] + bar_sec > timeout_ts:
            # zaman aşımı mumu: o ana kadar stop/hedef olmadıysa önceki kapanıştan çık
            if k == 0:
                return Outcome("TIMEOUT", c[0], t[0] + bar_sec)
            return Outcome("TIMEOUT", c[k - 1], t[k - 1] + bar_sec)
        hit_sl = l[k] <= sl if side == "LONG" else h[k] >= sl
        hit_tp = h[k] >= tp if side == "LONG" else l[k] <= tp
        if hit_sl:
            return Outcome("SL", sl, t[k] + bar_sec)
        if hit_tp:
            return Outcome("TP", tp, t[k] + bar_sec)
    return Outcome("OPEN", None, None)


def position_notional(stop_pct: float, risk_usd: float | None = None,
                      cost_pct: float | None = None) -> float:
    risk_usd = C.RISK_USD if risk_usd is None else risk_usd
    cost = C.ROUND_TRIP_COST_PCT if cost_pct is None else cost_pct
    return risk_usd / ((stop_pct + cost) / 100.0)


def pnl_usd(side: str, entry: float, exit_price: float, stop_pct: float,
            risk_usd: float | None = None, cost_pct: float | None = None) -> float:
    cost = C.ROUND_TRIP_COST_PCT if cost_pct is None else cost_pct
    notional = position_notional(stop_pct, risk_usd, cost)
    move_pct = (exit_price - entry) / entry * 100.0
    if side == "SHORT":
        move_pct = -move_pct
    return notional * (move_pct - cost) / 100.0
