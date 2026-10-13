"""Binance-only Coinalyze derivatives reader. Read-only; never creates orders.

This adapter intentionally returns None for missing/stale fields. Coinalyze's
long/short series must not be relabeled Binance global account ratio.
"""
import os
import time
import requests
import json
import sqlite3
import threading

_CACHE_LOCK = threading.Lock()
_CACHE_DB = os.getenv("LS_COINALYZE_CACHE_DB") or os.path.join(
    os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state",
    "coinalyze_cache.db",
)
_MAX_REQUESTS_PER_MINUTE = 36  # reserve headroom below provider's 40/min limit


def _cache_connection():
    os.makedirs(os.path.dirname(os.path.abspath(_CACHE_DB)), exist_ok=True)
    conn = sqlite3.connect(_CACHE_DB, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_cache (
        key TEXT PRIMARY KEY, stored_at REAL NOT NULL, payload TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_budget (
        id INTEGER PRIMARY KEY AUTOINCREMENT, request_at REAL NOT NULL
    )""")
    return conn


def _cached_request(path, params=None, ttl=300):
    """Cross-process TTL cache with atomic quota reservation; no network in DB transaction."""
    key = json.dumps([path, params or {}], sort_keys=True, separators=(",", ":"))
    with _CACHE_LOCK:
        conn = _cache_connection()
        try:
            # BEGIN IMMEDIATE serializes quota checks across processes. The
            # reservation commits BEFORE HTTP so slow providers never lock SQLite.
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            hit = conn.execute("SELECT stored_at,payload FROM api_cache WHERE key=?", (key,)).fetchone()
            if hit and 0 <= now - hit[0] < ttl:
                conn.commit()
                return json.loads(hit[1])
            conn.execute("DELETE FROM api_budget WHERE request_at < ?", (now - 60,))
            used = conn.execute("SELECT COUNT(*) FROM api_budget").fetchone()[0]
            if used >= _MAX_REQUESTS_PER_MINUTE:
                conn.rollback()
                raise RuntimeError("COINALYZE_RATE_BUDGET_EXHAUSTED")
            conn.execute("INSERT INTO api_budget(request_at) VALUES(?)", (now,))
            conn.commit()
            # A failed HTTP attempt still consumes the reservation (conservative).
            result = _get(path, params)
            conn.execute("INSERT OR REPLACE INTO api_cache VALUES(?,?,?)",
                         (key, time.time(), json.dumps(result)))
            conn.commit()
            return result
        finally:
            conn.close()


BASE = "https://api.coinalyze.net/v1"
_cache = {}

def _get(path, params=None):
    key = os.getenv("COINALYZE_API_KEY", "")
    if not key:
        raise RuntimeError("COINALYZE_API_KEY missing")
    r = requests.get(BASE + path, headers={"api_key": key}, params=params or {}, timeout=12)
    r.raise_for_status()
    return r.json()

def _market(symbol):
    symbol = symbol.upper()
    key = symbol + "_PERP.A"
    now = time.monotonic()
    cached = _cache.get("markets")
    if not cached or now - cached[0] > 3600:
        rows = _cached_request("/future-markets", ttl=3600)
        if not isinstance(rows, list):
            raise ValueError("invalid markets")
        _cache["markets"] = (now, {r.get("symbol"): r for r in rows if isinstance(r, dict)})
    m = _cache["markets"][1].get(key)
    if not m or any((m.get("exchange") != "A", m.get("symbol_on_exchange") != symbol,
                     m.get("is_perpetual") is not True, m.get("quote_asset") != "USDT",
                     m.get("margined") != "STABLE")):
        return None
    return key, m

def snapshot(symbol):
    """Get OI current + 1h change + independent Coinalyze L/S observation.

    Cache 5 minutes per symbol; <= 3 requests/symbol per refresh. 40/min
    provider quota means caller must limit refreshed symbols per minute.
    """
    symbol = symbol.upper()
    now = time.monotonic()
    hit = _cache.get(("snapshot", symbol))
    if hit and now - hit[0] < 60:
        return dict(hit[1])
    out = {"source": "COINALYZE_BINANCE_A", "symbol": symbol,
           "oi_now": None, "oi_change_1h": None, "long_short_ratio": None,
           "oi_fresh": False, "ls_fresh": False}
    try:
        market = _market(symbol)
        if not market:
            return out
        key, meta = market
        unit = meta.get("oi_lq_vol_denominated_in")
        out["oi_unit"] = unit
        current = _cached_request("/open-interest", {"symbols": key}, ttl=60)
        row = next((r for r in current if isinstance(r, dict) and r.get("symbol") == key), None)
        if row:
            stamp = float(row["update"])
            if stamp > 1e12: stamp /= 1000
            value = float(row["value"])
            if 0 <= time.time() - stamp <= 180 and value > 0:
                out["oi_now"] = value
                out["oi_fresh"] = True
        # OI change is usable only when the provider explicitly reports base units.
        if out["oi_fresh"] and unit == "BASE_ASSET":
            end = int(time.time())
            hist = _cached_request("/open-interest-history", {"symbols": key, "interval": "5min",
                        "from": end - 4200, "to": end})
            row = next((r for r in hist if isinstance(r, dict) and r.get("symbol") == key), None)
            candles = row.get("history", []) if row else []
            candles = sorted((x for x in candles if isinstance(x, dict)), key=lambda x: x["t"])
            if len(candles) >= 13:
                first_candle, last_candle = candles[-13], candles[-1]
                first = float(first_candle["c"])
                last = float(last_candle["c"])
                interval = float(last_candle["t"]) - float(first_candle["t"])
                if first > 0 and last > 0 and 3540 <= interval <= 3660 and 0 <= time.time()-float(last_candle["t"]) <= 660:
                    out["oi_change_1h"] = (last / first - 1) * 100
        # Do not rely on optional market metadata to suppress an available series.
        if out["oi_fresh"]:
            end = int(time.time())
            ls = _cached_request("/long-short-ratio-history", {"symbols": key, "interval": "5min",
                      "from": end - 3600, "to": end})
            row = next((r for r in ls if isinstance(r, dict) and r.get("symbol") == key), None)
            candles = row.get("history", []) if row else []
            if candles:
                point = max(candles, key=lambda x: x["t"])
                stamp = float(point["t"])
                ratio = float(point["r"])
                if 0 <= time.time() - stamp <= 660 and ratio > 0:
                    out["long_short_ratio"] = ratio
                    out["ls_fresh"] = True
    except (requests.RequestException, ValueError, TypeError, KeyError, StopIteration, IndexError, RuntimeError, sqlite3.Error) as exc:
        out["error_type"] = type(exc).__name__
    _cache[("snapshot", symbol)] = (time.monotonic(), out)
    return out
