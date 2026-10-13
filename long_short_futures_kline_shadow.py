"""Read-only Binance USD-M Futures kline WebSocket probe and recorder.

No orders, no Telegram, no fallback to other exchanges. Closed candles only.
Run separately before considering production integration.
"""
import json
import math
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
    quote = float(bar["q"])
    taker = float(bar["Q"])
    trades = int(bar["n"])
    if (start <= 0 or end <= start or not all(math.isfinite(v) for v in (*values, quote, taker))
            or min(values[:4]) <= 0 or values[4] < 0 or quote < 0 or taker < 0
            or taker > quote or trades < 0
            or values[1] < max(values[0], values[3], values[2])
            or values[2] > min(values[0], values[3])):
        return None
    return (symbol, interval, start, end, *values, SOURCE, int(time.time() * 1000), quote, trades, taker)


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
    symbols = sorted({s.upper() for s in symbols if s.upper().endswith("USDT")})[:30]
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



def backfill_binance_archive(db, symbols, intervals=("1m", "5m", "15m", "30m", "1h", "4h")):
    """Bootstrap authentic USD-M Futures history from Binance's public ZIP archive.

    Archive is an independent Binance source, not spot or another exchange.
    Only completed UTC days/months are imported. Fail closed if unavailable.
    """
    import csv
    import datetime as dt
    import io
    import urllib.request
    import zipfile
    now = dt.datetime.now(dt.timezone.utc)
    conn = initialize(db)
    inserted = 0
    try:
        for symbol in symbols:
            for interval in intervals:
                period = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}[interval]
                days = min(42, max(2, int((220 * period + 86399) // 86400) + 2))
                today = now.date()
                first = today - dt.timedelta(days=days)
                months = []
                cursor = first.replace(day=1)
                while cursor < today.replace(day=1):
                    months.append((cursor.year, cursor.month))
                    cursor = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
                archives = []
                for year, month in months:
                    name = f"{symbol}-{interval}-{year:04d}-{month:02d}.zip"
                    archives.append(f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/{interval}/{name}")
                day = max(first, today.replace(day=1))
                while day < today:
                    name = f"{symbol}-{interval}-{day.isoformat()}.zip"
                    archives.append(f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/{interval}/{name}")
                    day += dt.timedelta(days=1)
                for url in archives:
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "Binance-Futures-Archive-Backfill/1.0"})
                        with urllib.request.urlopen(req, timeout=9) as response:
                            blob = response.read(6_000_000)
                        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                            for filename in archive.namelist():
                                if not filename.endswith(".csv"):
                                    continue
                                for fields in csv.reader(io.TextIOWrapper(archive.open(filename), encoding="utf-8-sig")):
                                    if len(fields) < 11 or not fields[0].isdigit():
                                        continue
                                    try:
                                        open_ms = int(fields[0])
                                        close_ms = int(fields[6])
                                        if close_ms >= int(time.time() * 1000) - 250:
                                            continue
                                        row = (symbol, interval, open_ms, close_ms,
                                               *[float(fields[i]) for i in (1,2,3,4,5)],
                                               "BINANCE_UM_FUTURES_ARCHIVE", int(time.time()*1000),
                                               float(fields[7]), int(fields[8]), float(fields[10]))
                                        conn.execute("""INSERT OR REPLACE INTO futures_closed_klines
                                            (symbol,interval,open_ms,close_ms,open,high,low,close,volume,source,received_ms,quote_volume,trade_count,taker_buy_quote)
                                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
                                        inserted += 1
                                    except (ValueError, OverflowError):
                                        continue
                        conn.commit()
                    except Exception as exc:
                        print(f"FUTURES_ARCHIVE_MISS symbol={symbol} interval={interval} error={type(exc).__name__}", flush=True)
                print(f"FUTURES_ARCHIVE_INTERVAL symbol={symbol} interval={interval} rows_added={inserted}", flush=True)
    finally:
        conn.close()
    print(f"FUTURES_ARCHIVE_BACKFILL rows_added={inserted}", flush=True)


def bridge_recent_futures(db, symbols, intervals=("1m", "5m", "15m", "30m", "1h", "4h")):
    import urllib.parse
    import urllib.request
    conn = initialize(db)
    try:
        for symbol in symbols:
            for interval in intervals:
                query = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": 300})
                try:
                    with urllib.request.urlopen("https://fapi.binance.com/fapi/v1/klines?" + query, timeout=7) as response:
                        bars = json.load(response)
                    now = int(time.time() * 1000)
                    for bar in bars:
                        if int(bar[6]) >= now - 250:
                            continue
                        row = (symbol, interval, int(bar[0]), int(bar[6]), float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]), float(bar[5]), "BINANCE_UM_FUTURES_REST", now, float(bar[7]), int(bar[8]), float(bar[10]))
                        conn.execute("INSERT OR IGNORE INTO futures_closed_klines (symbol,interval,open_ms,close_ms,open,high,low,close,volume,source,received_ms,quote_volume,trade_count,taker_buy_quote) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
                    conn.commit()
                    print("FUTURES_BRIDGE_OK", symbol, interval, flush=True)
                except Exception as exc:
                    # Log the HTTP status without changing fail-closed candle handling.
                    status = getattr(exc, "code", None)
                    print("FUTURES_BRIDGE_UNAVAILABLE", symbol, interval, type(exc).__name__, "http_status=" + str(status) if status is not None else "http_status=unknown", flush=True)
    finally:
        conn.close()


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
    # Archive imports must never block the live closed-candle WebSocket.
    # One bounded worker handles new symbols, using SQLite WAL connections.
    import queue
    pending_backfills = queue.Queue(maxsize=60)
    scheduled_backfills = set()
    def archive_worker():
        while not stop.is_set():
            try:
                symbol = pending_backfills.get(timeout=1)
            except queue.Empty:
                continue
            try:
                backfill_binance_archive(db, [symbol])
                print("FUTURES_KLINE_BACKFILL_FINISHED " + symbol, flush=True)
            except Exception as exc:
                print("FUTURES_KLINE_BACKFILL_ERROR " + symbol + " " + type(exc).__name__, flush=True)
            finally:
                pending_backfills.task_done()
    threading.Thread(target=archive_worker, name="futures-archive-backfill", daemon=True).start()
    while not stop.is_set():
        try:
            selected = choose_symbols("", analyst_db=analyst, limit=30)
            symbols = list(dict.fromkeys(selected + ["BTCUSDT", "ETHUSDT", "SOLUSDT"]))[:30]
            # Collector-only coverage: include the latest scanned market window.
            # Signal selection and trading thresholds remain unchanged.
            if os.path.isfile(analyst):
                try:
                    with sqlite3.connect("file:" + os.path.abspath(analyst) + "?mode=ro", uri=True) as scan_db:
                        latest = scan_db.execute("SELECT MAX(scan_time_utc) FROM full_universe_stage_audit").fetchone()[0]
                        if latest:
                            extras = scan_db.execute("SELECT symbol FROM full_universe_stage_audit WHERE scan_time_utc=? ORDER BY quote_volume DESC LIMIT 30", (latest,)).fetchall()
                            symbols = list(dict.fromkeys(symbols + [x[0] for x in extras]))[:30]
                except (sqlite3.Error, OSError):
                    pass
            if not symbols:
                raise RuntimeError("DATA_NOT_READY: no analyst universe")
            # The analyst universe changes. A single startup backfill leaves
            # newly selected symbols without enough history indefinitely.
            missing = [sym for sym in symbols if sym not in scheduled_backfills]
            if missing:
                print("FUTURES_KLINE_NEW_SYMBOL_BACKFILL " +
                      json.dumps({"symbols":missing}), flush=True)
                for sym in missing:
                    try:
                        pending_backfills.put_nowait(sym)
                        scheduled_backfills.add(sym)
                    except queue.Full:
                        print("FUTURES_KLINE_BACKFILL_QUEUE_FULL " + sym, flush=True)
            # Binance REST returned HTTP 418 (IP restriction). Do not retry
            # REST on each cycle: preserve archive history and collect native
            # closed Futures candles over WebSocket. Missing bars fail closed.
            print("FUTURES_REST_BRIDGE_DISABLED_HTTP_418", flush=True)
            result = collect(db, symbols, seconds=900, stop=stop)
            print("FUTURES_KLINE_FEED " + json.dumps(result), flush=True)
            # No signal source switch: only observe verified native Futures candles.
        except Exception as exc:
            print("FUTURES_KLINE_FEED_ERROR " + type(exc).__name__ + ": " + str(exc)[:160], flush=True)
        stop.wait(5)
    return 0
