"""Read-only Binance USD-M Futures kline WebSocket probe and recorder.

No orders, no Telegram, no fallback to other exchanges. Closed candles only.
Run separately before considering production integration.
"""
import json
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
    if not symbol.endswith("USDT") or interval not in ("1m", "5m"):
        return None
    start = int(bar["t"])
    end = int(bar["T"])
    values = tuple(float(bar[key]) for key in ("o", "h", "l", "c", "v"))
    if start <= 0 or end <= start or min(values[:4]) <= 0 or values[4] < 0:
        return None
    return (symbol, interval, start, end, *values, SOURCE, int(time.time() * 1000))


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
    conn.commit()
    return conn


def store(conn, row):
    conn.execute("""INSERT OR REPLACE INTO futures_closed_klines
        (symbol,interval,open_ms,close_ms,open,high,low,close,volume,source,received_ms)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", row)
    conn.commit()


def collect(db, symbols, seconds=90):
    import websocket
    symbols = sorted({s.upper() for s in symbols if s.upper().endswith("USDT")})[:24]
    if not symbols:
        raise ValueError("No Binance USDT symbols")
    conn = initialize(db)
    ws = None
    count = 0
    try:
        ws = websocket.create_connection(URL, timeout=8, enable_multithread=True)
        params = [s.lower() + "@kline_" + interval for s in symbols for interval in ("1m", "5m")]
        ws.send(json.dumps({"method": "SUBSCRIBE", "params": params, "id": 71}))
        ws.settimeout(2)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
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
