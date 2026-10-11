"""Mum serisi ve göstergeler.

Tüm göstergeler nedenseldir: i. değer yalnızca 0..i mumlarını kullanır.
Bu sayede canlı motor ve geçmiş test aynı kodu, ileriye bakmadan kullanır.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

INTERVAL_SEC = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


@dataclass
class Series:
    interval: str
    t: list[int] = field(default_factory=list)      # açılış zamanı, saniye
    o: list[float] = field(default_factory=list)
    h: list[float] = field(default_factory=list)
    l: list[float] = field(default_factory=list)
    c: list[float] = field(default_factory=list)
    qv: list[float] = field(default_factory=list)   # USDT hacmi
    _cache: dict = field(default_factory=dict, repr=False)

    def __len__(self) -> int:
        return len(self.c)

    @property
    def sec(self) -> int:
        return INTERVAL_SEC[self.interval]

    def last_closed_index(self, at_ts: float) -> int:
        """at_ts anında kapanmış son mumun indeksi (yoksa -1)."""
        return bisect_right(self.t, at_ts - self.sec) - 1

    def drop_open_bar(self, now_ts: float) -> "Series":
        n = self.last_closed_index(now_ts) + 1
        if n < len(self):
            for name in ("t", "o", "h", "l", "c", "qv"):
                setattr(self, name, getattr(self, name)[:n])
            self._cache.clear()
        return self

    # --- önbellekli göstergeler -------------------------------------------
    def ema(self, n: int) -> list[float]:
        key = ("ema", n)
        if key not in self._cache:
            self._cache[key] = ema(self.c, n)
        return self._cache[key]

    def atr(self, n: int = 14) -> list[float]:
        key = ("atr", n)
        if key not in self._cache:
            self._cache[key] = atr(self.h, self.l, self.c, n)
        return self._cache[key]

    def pivots(self, k: int) -> list[tuple[int, float, str]]:
        key = ("piv", k)
        if key not in self._cache:
            self._cache[key] = pivots(self.h, self.l, k)
        return self._cache[key]


def ema(values: list[float], n: int) -> list[float]:
    out = [float("nan")] * len(values)
    if len(values) < n:
        return out
    a = 2.0 / (n + 1)
    e = sum(values[:n]) / n
    out[n - 1] = e
    for i in range(n, len(values)):
        e = values[i] * a + e * (1 - a)
        out[i] = e
    return out


def atr(h: list[float], l: list[float], c: list[float], n: int = 14) -> list[float]:
    out = [float("nan")] * len(c)
    if len(c) <= n:
        return out
    tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    a = sum(tr[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(c)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def pivots(h: list[float], l: list[float], k: int) -> list[tuple[int, float, str]]:
    """(indeks, fiyat, 'H'|'L'). Pivot ancak indeks+k mumu kapanınca bilinir."""
    out = []
    for j in range(k, len(h) - k):
        win_h = h[j - k:j + k + 1]
        win_l = l[j - k:j + k + 1]
        if h[j] == max(win_h) and win_h.count(h[j]) == 1:
            out.append((j, h[j], "H"))
        if l[j] == min(win_l) and win_l.count(l[j]) == 1:
            out.append((j, l[j], "L"))
    return out
