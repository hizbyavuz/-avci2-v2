"""Read-only Binance USD-M Futures kline WebSocket probe and recorder.

No orders, no Telegram, no fallback to other exchanges. Closed candles only.
Run separately before considering production integration.
"""
import json
import os
import sqlite3
import time

URL = "wss://fstream.binance.com/market/stream"
SOURCE = "BINANCE_UM_FUTURES_WEBSOCKET"


def parse_closed_kline(packet):
    event = packet.get("data", packet)
    if event.get("e") != "kline":
        return None
    bar = event.get("k") or {}
    if not bar.get("x"):
        return None
    symbol = str(event.get("s") or bar.get("s") or "").upper()
    interval = str(bar.get("i") or "")
    if not symbol.endswith("USDT") or interval not in ("1m", "5m", "15m", "30m", "1h", "4h"):
        return None
    start = int(bar["t"])
    end = int(bar["T"])
    values = tuple(float(bar[key]) for key in ("o", "h", "l", "c", "v"))
    if start <= 0 or end <= start or min(values[:4]) <= 0 or values[4] < 0:
        return None
    return (symbol, interval, start, end, *values, SOURCE, int(time.time() * 1000), float(bar["q"]), int(bar["n"]), float(bar["Q"]))


def initialize(db):
    conn = sqlite3.connect(db, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS futures_closed_klines (
        symbol TEXT NOT NULL, interval TEXT NOT NULL,
        open_ms INTEGER NOT NULL, close_ms INTEGER NOT NULL,
        open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
        close REAL NOT NULL, volume REAL NOT NULL,
        source TEXT NOT NULL, received_ms INTEGER NOT NULL,
        PRIMARY KEY(symbol,interval,open_ms)
    )""")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(futures_closed_klines)")}
    for name, dtype in (("quote_volume", "REAL"), ("trade_count", "INTEGER"), ("taker_buy_quote", "REAL")):
        if name not in columns:
            conn.execute(f"ALTER TABLE futures_closed_klines ADD COLUMN {name} {dtype}")
    conn.commit()
    return conn


def store(conn, row):
    conn.execute("""INSERT OR REPLACE INTO futures_closed_klines
        (symbol,interval,open_ms,close_ms,open,high,low,close,volume,source,received_ms,quote_volume,trade_count,taker_buy_quote)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
    conn.commit()


def collect(db, symbols, seconds=90, stop=None):
    import websocket
    symbols = sorted({s.upper() for s in symbols if s.upper().endswith("USDT")})[:24]
    if not symbols:
        raise ValueError("No Binance USDT symbols")
    os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)
    conn = initialize(db)
    ws = None
    count = 0
    try:
        ws = websocket.create_connection(URL, timeout=8, enable_multithread=True)
        params = [s.lower() + "@kline_" + interval for s in symbols for interval in ("1m", "5m", "15m", "30m", "1h", "4h")]
        ws.send(json.dumps({"method": "SUBSCRIBE", "params": params, "id": 71}))
        ws.settimeout(2)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not (stop and stop.is_set()):
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            packet = json.loads(raw)
            row = parse_closed_kline(packet)
            if row and row[0] in symbols:
                store(conn, row)
                count += 1
        return {"source": SOURCE, "symbols": len(symbols), "closed_bars": count}
    finally:
        if ws:
            ws.close()
        conn.close()


def main():
    """Optional one-shot Railway probe; never starts with production daemon."""
    from long_short_v4_native_pipeline import choose_symbols
    root = os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state"
    analyst_db = os.getenv("LS_ANALYST_DB") or os.path.join(root, "long_short_analyst.db")
    configured = os.getenv("LS_KLINE_PROBE_SYMBOLS", "").strip()
    symbols = ([s.strip().upper() for s in configured.split(",") if s.strip()]
               if configured else choose_symbols("", analyst_db=analyst_db, limit=12))
    if not symbols:
        print("FUTURES_KLINE_SHADOW_NO_SYMBOLS", flush=True)
        return 2
    result = collect(os.path.join(root, "binance_futures_kline_shadow.db"), symbols, seconds=95)
    print("FUTURES_KLINE_SHADOW_RESULT " + json.dumps(result), flush=True)
    return 0 if result["closed_bars"] > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())


def continuous_main():
    """Persistent Binance-only closed candle feed; observation-only until validated."""
    import signal
    import threading
    from long_short_v4_native_pipeline import choose_symbols
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    root = os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state"
    os.makedirs(root, exist_ok=True)
    db = os.path.join(root, "binance_futures_kline_shadow.db")
    analyst = os.getenv("LS_ANALYST_DB") or os.path.join(root, "long_short_analyst.db")
    while not stop.is_set():
        try:
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] + choose_symbols("", analyst_db=analyst, limit=9)
            symbols = list(dict.fromkeys(symbols))[:12]
            result = collect(db, symbols, seconds=900, stop=stop)
            print("FUTURES_KLINE_FEED " + json.dumps(result), flush=True)
            # No signal source switch: only observe verified native Futures candles.
        except Exception as exc:
            print("FUTURES_KLINE_FEED_ERROR " + type(exc).__name__ + ": " + str(exc)[:160], flush=True)
        stop.wait(5)
    return 0
