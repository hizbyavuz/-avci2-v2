"""Gate.io USDT perpetual public market data. No API key or order execution."""
from __future__ import annotations
import time
import requests
from . import config as C
from .series import INTERVAL_SEC, Series

BASES = ("https://api.gateio.ws/api/v4", "https://fx-api.gateio.ws/api/v4")
TIMEOUT = 15
_session = requests.Session()
_session.headers.update({"Accept": "application/json", "User-Agent": "gate-trader/1"})

class GateError(RuntimeError):
    pass

def get(path: str, params: dict | None = None, retries: int = 2):
    last = None
    for attempt in range(retries + 1):
        for base in BASES:
            try:
                r = _session.get(base + path, params=params or {}, timeout=TIMEOUT)
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    last = GateError("429 rate limit")
                    continue
                if r.status_code >= 400:
                    last = GateError(f"HTTP {r.status_code}: {r.text[:200]}")
                    continue
                return r.json()
            except (requests.RequestException, ValueError) as exc:
                last = exc
        time.sleep(0.5 * (attempt + 1))
    raise GateError(f"{path}: {last}")

def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default

def universe() -> list[dict]:
    """Highest-volume USDT perpetual contracts."""
    rows = get("/futures/usdt/tickers") or []
    out = []
    for r in rows:
        con = str(r.get("contract") or "")
        if not con.endswith("_USDT") or con.upper() in C.EXCLUDE:
            continue
        vol = _f(r.get("volume_24h_quote")) or _f(r.get("volume_24h_settle"))
        last = _f(r.get("last"))
        if last <= 0 or vol < C.MIN_QUOTE_VOLUME_24H:
            continue
        fr = r.get("funding_rate")
        out.append({"contract": con, "last": last, "vol24": vol,
                    "funding": _f(fr, None) if fr not in (None, "") else None})
    out.sort(key=lambda x: -x["vol24"])
    return out[:C.MAX_COINS]

def _parse(rows, interval: str) -> Series:
    s = Series(interval)
    seen = set()
    for r in sorted(rows or [], key=lambda r: int(_f(r.get("t")))):
        t = int(_f(r.get("t")))
        if t <= 0 or t in seen:
            continue
        seen.add(t)
        s.t.append(t)
        s.o.append(_f(r.get("o")))
        s.h.append(_f(r.get("h")))
        s.l.append(_f(r.get("l")))
        s.c.append(_f(r.get("c")))
        s.qv.append(_f(r.get("sum")))
    return s

def candles(contract: str, interval: str, limit: int = 300,
            now_ts: float | None = None) -> Series:
    now_ts = time.time() if now_ts is None else now_ts
    rows = get("/futures/usdt/candlesticks",
               {"contract": contract, "interval": interval, "limit": min(limit + 1, 2000)})
    return _parse(rows, interval).drop_open_bar(now_ts)

def candles_range(contract: str, interval: str, start_ts: int, end_ts: int,
                  pause: float = 0.15) -> Series:
    sec = INTERVAL_SEC[interval]
    step = 1900 * sec
    rows: list = []
    a = int(start_ts)
    while a < end_ts:
        b = min(a + step, int(end_ts))
        try:
            part = get("/futures/usdt/candlesticks",
                       {"contract": contract, "interval": interval, "from": a, "to": b})
        except GateError:
            part = []
        rows.extend(part or [])
        a = b + sec
        time.sleep(pause)
    return _parse(rows, interval).drop_open_bar(time.time())

def candles_since(contract: str, interval: str, start_ts: int) -> Series:
    now = time.time()
    return candles_range(contract, interval, start_ts, int(now), pause=0.0)

def oi_change_1h(contract: str) -> float | None:
    try:
        rows = get("/futures/usdt/contract_stats",
                   {"contract": contract, "interval": "5m", "limit": 13}) or []
        rows.sort(key=lambda r: int(_f(r.get("time"))))
        vals = [_f(r.get("open_interest")) for r in rows if _f(r.get("open_interest")) > 0]
        if len(vals) >= 2:
            return (vals[-1] - vals[0]) / vals[0] * 100
    except GateError:
        pass
    return None
