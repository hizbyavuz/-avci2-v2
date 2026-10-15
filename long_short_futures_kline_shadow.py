"""Binance USD-M Futures closed-candle recorder (WebSocket + official archive).

No orders, no Telegram, no fallback to other exchanges. Closed candles only.

Design (2026-10-13 audit):
- One long-lived WebSocket connection. Symbol-set changes are applied with
  SUBSCRIBE/UNSUBSCRIBE on the open connection instead of closing it every
  15 minutes; the periodic reconnect was dropping closed candles.
- Planned reconnects (Binance closes connections after 24h) happen in the
  middle of a minute, when no candle of any interval can close.
- Writes are batched and idempotent on (symbol, interval, open_ms). A duplicate
  never drops the connection; a lock never loses buffered rows.
- Gaps are detected and logged with first missing open_ms and missing count.
  They are never filled with estimated candles. Only Binance's own archive can
  repair them once the completed UTC day is published.
"""
import json
import math
import os
import sqlite3
import time

URL = "wss://fstream.binance.com/market/stream"
SOURCE = "BINANCE_UM_FUTURES_WEBSOCKET"
ARCHIVE_SOURCE = "BINANCE_UM_FUTURES_ARCHIVE"
REST_SOURCE = "BINANCE_UM_FUTURES_REST"
VALID_SOURCES = (SOURCE, ARCHIVE_SOURCE, REST_SOURCE)
INTERVALS = ("1m", "5m", "15m", "30m", "1h", "4h")
PERIOD_MS = {"1m": 60000, "5m": 300000, "15m": 900000, "30m": 1800000,
             "1h": 3600000, "4h": 14400000}
MAX_SYMBOLS = 90                 # 90 x 6 = 540 streams (< 1024 per connection)
MAX_STREAMS_PER_CONNECTION = 1000
CHART_MAX_SYMBOLS = MAX_STREAMS_PER_CONNECTION // len(INTERVALS)
SUBSCRIBE_CHUNK = 100            # params per control message
CONTROL_MESSAGE_GAP_SECONDS = 0.25   # <= 4 control messages/s (limit is 10/s)
PLANNED_RECONNECT_SECONDS = 23 * 3600
RETENTION_SECONDS = 24 * 3600    # keep a symbol subscribed 24h after last request

COLUMNS = ("symbol,interval,open_ms,close_ms,open,high,low,close,volume,"
           "source,received_ms,quote_volume,trade_count,taker_buy_quote")


def log(tag, payload=None):
    if payload is None:
        print(tag, flush=True)
    else:
        print(tag + " " + json.dumps(payload, sort_keys=True, default=str), flush=True)


def parse_last_price(packet):
    """(symbol, last price, event_ms) from any 1m kline update, else None."""
    try:
        event = packet.get("data", packet)
        if not isinstance(event, dict) or event.get("e") != "kline":
            return None
        bar = event.get("k") or {}
        if str(bar.get("i")) != "1m":
            return None
        symbol = str(event.get("s") or bar.get("s") or "").upper()
        price = float(bar["c"])
        event_ms = int(event["E"])
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None
    if not symbol.endswith("USDT") or not math.isfinite(price) or price <= 0 or event_ms <= 0:
        return None
    return symbol, price, event_ms


def parse_forming_range(packet):
    tick=parse_last_price(packet)
    if not tick:return None
    try:
        b=packet.get('data',packet)['k']
        start,end=int(b['t']),int(b['T'])
        op,hi,lo,cl=(float(b[k]) for k in ('o','h','l','c'))
        if (start%60000 or end!=start+59999 or tick[2]<start or
                not all(math.isfinite(v) and v>0 for v in (op,hi,lo,cl)) or
                not lo<=min(op,cl)<=max(op,cl)<=hi):return None
        return (tick[0],start,end,op,hi,lo,cl,tick[2])
    except (KeyError,TypeError,ValueError,OverflowError):return None


def parse_closed_kline(packet):
    """Return a storable row for a closed, internally consistent candle, else None.

    Missing fields (quote volume, trade count, taker buy quote) reject the
    candle instead of raising: one malformed message must not kill the feed.
    """
    try:
        event = packet.get("data", packet)
        if not isinstance(event, dict) or event.get("e") != "kline":
            return None
        bar = event.get("k") or {}
        if not bar.get("x"):
            return None
        symbol = str(event.get("s") or bar.get("s") or "").upper()
        interval = str(bar.get("i") or "")
        if not symbol.endswith("USDT") or interval not in INTERVALS:
            return None
        start = int(bar["t"])
        end = int(bar["T"])
        values = tuple(float(bar[key]) for key in ("o", "h", "l", "c", "v"))
        quote = float(bar["q"])
        taker = float(bar["Q"])
        trades = int(bar["n"])
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None
    if (start <= 0 or end <= start
            or not all(math.isfinite(v) for v in (*values, quote, taker))
            or min(values[:4]) <= 0 or values[4] < 0 or quote < 0 or taker < 0
            or taker > quote or trades < 0
            or values[1] < max(values[0], values[3], values[2])
            or values[2] > min(values[0], values[3])):
        return None
    return (symbol, interval, start, end, *values, SOURCE, int(time.time() * 1000), quote, trades, taker)


def connect(db, timeout=30):
    conn = sqlite3.connect(db, timeout=timeout)
    conn.execute("PRAGMA busy_timeout=%d" % int(timeout * 1000))
    return conn


def initialize(db):
    conn = connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS futures_closed_klines (
        symbol TEXT NOT NULL, interval TEXT NOT NULL,
        open_ms INTEGER NOT NULL, close_ms INTEGER NOT NULL,
        open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
        close REAL NOT NULL, volume REAL NOT NULL,
        source TEXT NOT NULL, received_ms INTEGER NOT NULL,
        PRIMARY KEY(symbol,interval,open_ms)
    )""")
    # Latest traded price from the forming 1m kline (Binance Futures native,
    # indicative only; never a closed candle and never written as one).
    conn.execute("""CREATE TABLE IF NOT EXISTS futures_last_price (
        symbol TEXT PRIMARY KEY, price REAL NOT NULL, event_ms INTEGER NOT NULL,
        received_ms INTEGER NOT NULL, source TEXT NOT NULL)""")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(futures_closed_klines)")}
    for name, dtype in (("quote_volume", "REAL"), ("trade_count", "INTEGER"), ("taker_buy_quote", "REAL")):
        if name not in columns:
            conn.execute(f"ALTER TABLE futures_closed_klines ADD COLUMN {name} {dtype}")
    conn.execute("""CREATE TABLE IF NOT EXISTS futures_current_1m (
        symbol TEXT PRIMARY KEY,open_ms INTEGER,close_ms INTEGER,open REAL,
        high REAL,low REAL,close REAL,event_ms INTEGER,received_ms INTEGER,source TEXT)""")
    conn.commit()
    return conn


def _same(a, b):
    if a is None or b is None:
        return a is b
    a = float(a); b = float(b)
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def upsert_rows(conn, rows):
    """Idempotent insert on (symbol, interval, open_ms). Caller commits.

    - new candle: inserted
    - identical candle: counted as duplicate, untouched
    - stored candle with NULL quote/trade/taker fields: repaired from the new
      authentic Binance row (fields only if OHLCV matches; whole row otherwise)
    - complete stored candle that disagrees: kept, conflict logged
    """
    stats = {"inserted": 0, "duplicate": 0, "repaired": 0, "conflict": 0}
    conflicts = []
    for row in rows:
        cur = conn.execute(f"INSERT OR IGNORE INTO futures_closed_klines ({COLUMNS}) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        if cur.rowcount == 1:
            stats["inserted"] += 1
            continue
        old = conn.execute("""SELECT close_ms,open,high,low,close,volume,
                quote_volume,trade_count,taker_buy_quote,source
                FROM futures_closed_klines WHERE symbol=? AND interval=? AND open_ms=?""",
                           row[:3]).fetchone()
        if old is None:  # pragma: no cover - deleted concurrently
            continue
        same_bar = int(old[0]) == int(row[3]) and all(_same(a, b) for a, b in zip(old[1:6], row[4:9]))
        new_fields = (row[11], row[12], row[13])
        missing_fields = any(v is None for v in old[6:9])
        if missing_fields and not any(v is None for v in new_fields):
            if same_bar:
                conn.execute("""UPDATE futures_closed_klines
                    SET quote_volume=?, trade_count=?, taker_buy_quote=?
                    WHERE symbol=? AND interval=? AND open_ms=?""", (*new_fields, *row[:3]))
            else:
                conn.execute(f"""UPDATE futures_closed_klines SET
                    close_ms=?,open=?,high=?,low=?,close=?,volume=?,source=?,received_ms=?,
                    quote_volume=?,trade_count=?,taker_buy_quote=?
                    WHERE symbol=? AND interval=? AND open_ms=?""", (*row[3:], *row[:3]))
                stats["conflict"] += 1
                conflicts.append({"symbol": row[0], "interval": row[1], "open_ms": row[2],
                                  "stored_source": old[9], "new_source": row[9],
                                  "action": "replaced_incomplete_row"})
            stats["repaired"] += 1
        elif same_bar and all(_same(a, b) for a, b in zip(old[6:9], new_fields)):
            stats["duplicate"] += 1
        else:
            stats["conflict"] += 1
            conflicts.append({"symbol": row[0], "interval": row[1], "open_ms": row[2],
                              "stored_source": old[9], "new_source": row[9],
                              "action": "kept_stored_row"})
    if conflicts:
        log("FUTURES_KLINE_CONFLICT", {"count": len(conflicts), "examples": conflicts[:5]})
    return stats


def store(conn, row):
    """Idempotent single-row write (kept for compatibility)."""
    stats = upsert_rows(conn, [row])
    conn.commit()
    return stats


class GapTracker:
    """Detects missing closed candles between consecutive stored candles."""

    def __init__(self):
        self.last_open = {}

    def observe(self, conn, row):
        key = (row[0], row[1])
        period = PERIOD_MS[row[1]]
        last = self.last_open.get(key)
        # REST/archive may have repaired the tail since this stream last
        # saw a candle. Reconcile the cache before reporting missing bars.
        prev = conn.execute("""SELECT MAX(open_ms) FROM futures_closed_klines
            WHERE symbol=? AND interval=? AND open_ms<?""", (row[0], row[1], row[2])).fetchone()[0]
        if prev is not None:
            last = prev if last is None else max(last, prev)
        gap = None
        if last is not None and row[2] - last > period:
            gap = {"symbol": row[0], "interval": row[1],
                   "first_missing_open_ms": int(last + period),
                   "missing_bars": int((row[2] - last) // period - 1),
                   "resumed_open_ms": int(row[2])}
        if last is None or row[2] > last:
            self.last_open[key] = row[2]
        return gap


class FuturesKlineStream:
    """Long-lived Binance USD-M kline WebSocket with dynamic subscriptions."""

    def __init__(self, db, url=URL, intervals=INTERVALS, max_symbols=MAX_SYMBOLS,
                 ws_module=None, clock=time.time, sleep=time.sleep,
                 retention_seconds=RETENTION_SECONDS):
        if max_symbols * len(intervals) > MAX_STREAMS_PER_CONNECTION:
            raise ValueError("stream budget exceeds one Binance connection")
        self.db = db
        self.url = url
        self.intervals = tuple(intervals)
        self.max_symbols = int(max_symbols)
        self.ws_module = ws_module
        self.clock = clock
        self.sleep = sleep
        self.retention_seconds = retention_seconds
        self.ws = None
        self.subscribed = []          # symbols currently subscribed, priority order kept
        self.last_requested = {}      # symbol -> clock() of the last request
        self.uncovered = []
        self.buffer = []
        self.last_price = {}
        self.current_ranges = {}
        self.gaps = GapTracker()
        self.next_id = 100
        self.totals = {"rows_written": 0, "duplicates": 0, "repaired": 0, "conflicts": 0,
                       "gaps_detected": 0, "missing_bars_detected": 0, "write_retries": 0,
                       "invalid_messages": 0, "connections": 0, "disconnects": 0}
        self.connected_at = None

    # ---- subscription management -------------------------------------
    def plan(self, requested):
        """Return (target symbols, uncovered requested symbols). Pure function of state."""
        now = self.clock()
        requested = [s.upper() for s in dict.fromkeys(requested)
                     if isinstance(s, str) and s.upper().endswith("USDT")]
        for sym in requested:
            self.last_requested[sym] = now
        keep = [s for s in self.subscribed
                if s in requested or now - self.last_requested.get(s, 0) <= self.retention_seconds]
        target = [s for s in requested][: self.max_symbols]
        for sym in keep:  # retain recently requested symbols while capacity allows
            if sym not in target and len(target) < self.max_symbols:
                target.append(sym)
        uncovered = [s for s in requested if s not in target]
        return target, uncovered

    def _control(self, method, symbols):
        params = [s.lower() + "@kline_" + iv for s in symbols for iv in self.intervals]
        for i in range(0, len(params), SUBSCRIBE_CHUNK):
            self.next_id += 1
            self.ws.send(json.dumps({"method": method, "params": params[i:i + SUBSCRIBE_CHUNK],
                                     "id": self.next_id}))
            self.sleep(CONTROL_MESSAGE_GAP_SECONDS)

    def apply(self, requested):
        target, uncovered = self.plan(requested)
        add = [s for s in target if s not in self.subscribed]
        drop = [s for s in self.subscribed if s not in target]
        if drop:
            self._control("UNSUBSCRIBE", drop)
        if add:
            self._control("SUBSCRIBE", add)
        self.subscribed = target
        self.uncovered = uncovered
        if uncovered:
            log("FUTURES_KLINE_CAPACITY_EXCEEDED", {"uncovered": uncovered, "capacity": self.max_symbols})
        return {"added": add, "dropped": drop, "uncovered": uncovered}

    def coverage(self, requested):
        req = list(dict.fromkeys(s.upper() for s in requested))
        return {"tracked": len(self.subscribed), "requested": len(req),
                "covered": sum(s in self.subscribed for s in req),
                "uncovered": [s for s in req if s not in self.subscribed],
                "streams": len(self.subscribed) * len(self.intervals),
                "connection_age_seconds": (round(self.clock() - self.connected_at)
                                           if self.connected_at else None),
                "pending_rows": len(self.buffer), **self.totals}

    # ---- persistence ---------------------------------------------------
    def flush(self, conn):
        if not self.buffer and not self.last_price and not self.current_ranges:
            return True
        rows = self.buffer
        prices = self.last_price
        try:
            gaps = [g for g in (self.gaps.observe(conn, r) for r in sorted(rows, key=lambda r: r[2])) if g]
            stats = upsert_rows(conn, rows)
            now_ms = int(time.time() * 1000)
            conn.executemany("""INSERT INTO futures_last_price(symbol,price,event_ms,received_ms,source)
                VALUES(?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET price=excluded.price,
                event_ms=excluded.event_ms, received_ms=excluded.received_ms, source=excluded.source
                WHERE excluded.event_ms>=futures_last_price.event_ms""",
                             [(sym, px, ev, now_ms, SOURCE) for sym, (px, ev) in prices.items()])
            conn.executemany("""INSERT INTO futures_current_1m VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET open_ms=excluded.open_ms,close_ms=excluded.close_ms,
                open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                event_ms=excluded.event_ms,received_ms=excluded.received_ms,source=excluded.source
                WHERE excluded.event_ms>=futures_current_1m.event_ms""",
                [(*r,now_ms,SOURCE) for r in self.current_ranges.values()])
            conn.commit()
        except sqlite3.OperationalError as exc:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            self.totals["write_retries"] += 1
            log("FUTURES_KLINE_WRITE_RETRY", {"pending_rows": len(rows), "error": str(exc)[:120]})
            return False  # rows stay buffered; nothing is dropped
        self.buffer = []
        self.last_price = {}
        self.current_ranges = {}
        self.totals["rows_written"] += stats["inserted"]
        self.totals["duplicates"] += stats["duplicate"]
        self.totals["repaired"] += stats["repaired"]
        self.totals["conflicts"] += stats["conflict"]
        for gap in gaps:
            self.totals["gaps_detected"] += 1
            self.totals["missing_bars_detected"] += gap["missing_bars"]
            log("FUTURES_KLINE_GAP_DETECTED", gap)
        return True

    # ---- main loop ------------------------------------------------------
    def _handle(self, raw):
        try:
            packet = json.loads(raw)
        except (TypeError, ValueError):
            self.totals["invalid_messages"] += 1
            return
        if isinstance(packet, dict) and "id" in packet and "result" in packet:
            if packet.get("result") is not None:
                log("FUTURES_KLINE_CONTROL_RESPONSE", {"id": packet.get("id"), "result": str(packet.get("result"))[:120]})
            return
        if isinstance(packet, dict) and packet.get("error"):
            log("FUTURES_KLINE_CONTROL_ERROR", {"error": str(packet.get("error"))[:200]})
            return
        if not isinstance(packet, dict):
            return
        tick = parse_last_price(packet)
        if tick and tick[0] in self.subscribed:
            self.last_price[tick[0]] = (tick[1], tick[2])
        forming = parse_forming_range(packet)
        if forming and forming[0] in self.subscribed:
            previous=self.current_ranges.get(forming[0])
            if not previous or forming[-1]>=previous[-1]:self.current_ranges[forming[0]]=forming
        row = parse_closed_kline(packet)
        if row and row[0] in self.subscribed:
            self.buffer.append(row)

    def run(self, requested_fn, stop, refresh_seconds=60, flush_seconds=1.0,
            planned_reconnect_seconds=PLANNED_RECONNECT_SECONDS, max_runtime=None):
        ws_mod = self.ws_module
        if ws_mod is None:
            import websocket as ws_mod
        conn = initialize(self.db)
        started = self.clock()
        backoff = 1.0
        try:
            while not stop.is_set():
                if max_runtime is not None and self.clock() - started >= max_runtime:
                    break
                requested = []
                try:
                    requested = requested_fn()
                    self.ws = ws_mod.create_connection(self.url, timeout=10, enable_multithread=True)
                    self.ws.settimeout(1)
                    self.connected_at = self.clock()
                    self.totals["connections"] += 1
                    self.subscribed = []
                    self.apply(requested)
                    log("FUTURES_KLINE_WS_CONNECTED", self.coverage(requested))
                    backoff = 1.0
                    last_refresh = last_flush = self.clock()
                    while not stop.is_set():
                        now = self.clock()
                        if max_runtime is not None and now - started >= max_runtime:
                            break
                        if now - last_flush >= flush_seconds or len(self.buffer) >= 500:
                            self.flush(conn)
                            last_flush = now
                        if now - last_refresh >= refresh_seconds:
                            requested = requested_fn()
                            self.apply(requested)
                            log("FUTURES_KLINE_STICKY_COVERAGE", self.coverage(requested))
                            last_refresh = now
                        second = int(now) % 60
                        if now - self.connected_at >= planned_reconnect_seconds and 15 <= second <= 40:
                            log("FUTURES_KLINE_PLANNED_RECONNECT", {"connection_age_seconds": round(now - self.connected_at)})
                            break
                        try:
                            raw = self.ws.recv()
                        except ws_mod.WebSocketTimeoutException:
                            continue
                        if raw in (None, "", b""):
                            raise ConnectionError("empty frame / connection closed")
                        self._handle(raw)
                except Exception as exc:
                    self.totals["disconnects"] += 1
                    log("FUTURES_KLINE_WS_DISCONNECTED", {"error_type": type(exc).__name__,
                                                         "error": str(exc)[:160], "retry_in_seconds": backoff})
                    stop.wait(backoff)
                    backoff = min(30.0, backoff * 2)
                finally:
                    self.flush(conn)
                    if self.ws is not None:
                        try:
                            self.ws.close()
                        except Exception:
                            pass
                        self.ws = None
                    self.connected_at = None
        finally:
            for _ in range(5):
                if self.flush(conn):
                    break
                time.sleep(1)
            conn.close()
        return self.coverage([])


def collect(db, symbols, seconds=90, stop=None):
    """Bounded collection on one connection (probe / tests). Not used by the daemon."""
    import threading
    symbols = sorted({s.upper() for s in symbols if s.upper().endswith("USDT")})[:MAX_SYMBOLS]
    if not symbols:
        raise ValueError("No Binance USDT symbols")
    os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)
    stream = FuturesKlineStream(db)
    result = stream.run(lambda: symbols, stop or threading.Event(), max_runtime=seconds)
    return {"source": SOURCE, "symbols": len(symbols), "closed_bars": result["rows_written"]}


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


def archive_urls(symbol, interval, today):
    """Official Binance Vision UM Futures archive files covering ~220 bars.

    Monthly files for complete months, daily files for the current month.
    Only completed UTC days are requested; today is never requested.
    """
    import datetime as dt
    from urllib.parse import quote
    symbol = quote(symbol, safe="")
    period = PERIOD_MS[interval] // 1000
    days = min(42, max(2, int((220 * period + 86399) // 86400) + 2))
    first = today - dt.timedelta(days=days)
    out = []
    cursor = first.replace(day=1)
    while cursor < today.replace(day=1):
        name = f"{symbol}-{interval}-{cursor.year:04d}-{cursor.month:02d}.zip"
        out.append(("monthly", f"{cursor.year:04d}-{cursor.month:02d}",
                    f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/{interval}/{name}"))
        cursor = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    day = max(first, today.replace(day=1))
    while day < today:
        name = f"{symbol}-{interval}-{day.isoformat()}.zip"
        out.append(("daily", day.isoformat(),
                    f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/{interval}/{name}"))
        day += dt.timedelta(days=1)
    return out


def archive_rows(symbol, interval, blob, now_ms=None):
    import csv
    import io
    import zipfile
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    rows = []
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for filename in archive.namelist():
            if not filename.endswith(".csv"):
                continue
            for fields in csv.reader(io.TextIOWrapper(archive.open(filename), encoding="utf-8-sig")):
                if len(fields) < 11 or not fields[0].isdigit():
                    continue
                try:
                    open_ms = int(fields[0]); close_ms = int(fields[6])
                    if close_ms >= now_ms - 250:
                        continue
                    row = (symbol, interval, open_ms, close_ms,
                           *[float(fields[i]) for i in (1, 2, 3, 4, 5)],
                           ARCHIVE_SOURCE, now_ms,
                           float(fields[7]), int(fields[8]), float(fields[10]))
                except (ValueError, OverflowError):
                    continue
                if not all(math.isfinite(v) for v in (*row[4:9], row[11], row[13])):
                    continue
                rows.append(row)
    return rows


def backfill_binance_archive(db, symbols, intervals=INTERVALS, fetch=None, today=None):
    """Import authentic USD-M Futures history from Binance's public ZIP archive.

    Archive is an independent Binance source, not spot or another exchange.
    Returns counts; misses are classified (not_published_or_not_listed vs error).
    """
    import datetime as dt
    import urllib.error
    import urllib.request
    if fetch is None:
        def fetch(url):
            req = urllib.request.Request(url, headers={"User-Agent": "Binance-Futures-Archive-Backfill/1.1"})
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read(16_000_000)
    today = today or dt.datetime.now(dt.timezone.utc).date()
    recent_cutoff = (today - dt.timedelta(days=2)).isoformat()
    conn = initialize(db)
    out = {"rows_added": 0, "repaired": 0, "conflicts": 0, "files_ok": 0,
           "misses": 0, "recent_misses": 0, "miss_reasons": {}}
    try:
        for symbol in symbols:
            for interval in intervals:
                added = 0
                for kind, label, url in archive_urls(symbol, interval, today):
                    try:
                        blob = fetch(url)
                        rows = archive_rows(symbol, interval, blob)
                        stats = upsert_rows(conn, rows)
                        conn.commit()
                        out["files_ok"] += 1
                        added += stats["inserted"]
                        out["rows_added"] += stats["inserted"]
                        out["repaired"] += stats["repaired"]
                        out["conflicts"] += stats["conflict"]
                    except Exception as exc:
                        try:
                            conn.rollback()
                        except sqlite3.Error:
                            pass
                        code = getattr(exc, "code", None)
                        reason = ("not_published_or_not_listed" if code == 404 else
                                  "http_%s" % code if code else type(exc).__name__)
                        out["misses"] += 1
                        out["miss_reasons"][reason] = out["miss_reasons"].get(reason, 0) + 1
                        if kind == "daily" and label >= recent_cutoff:
                            out["recent_misses"] += 1
                        print(f"FUTURES_ARCHIVE_MISS symbol={symbol} interval={interval} "
                              f"file={kind}:{label} reason={reason}", flush=True)
                print(f"FUTURES_ARCHIVE_INTERVAL symbol={symbol} interval={interval} rows_added={added}", flush=True)
    finally:
        conn.close()
    log("FUTURES_ARCHIVE_BACKFILL", out)
    return out


def bridge_recent_futures(db, symbols, intervals=INTERVALS):
    """Native USD-M REST bridge. Disabled in the daemon (HTTP 418/451 observed)."""
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
                    rows = [(symbol, interval, int(b[0]), int(b[6]), float(b[1]), float(b[2]), float(b[3]),
                             float(b[4]), float(b[5]), REST_SOURCE, now, float(b[7]), int(b[8]), float(b[10]))
                            for b in bars if int(b[6]) < now - 250]
                    upsert_rows(conn, rows)
                    conn.commit()
                    print("FUTURES_BRIDGE_OK", symbol, interval, flush=True)
                except Exception as exc:
                    status = getattr(exc, "code", None)
                    print("FUTURES_BRIDGE_UNAVAILABLE", symbol, interval, type(exc).__name__,
                          "http_status=" + str(status) if status is not None else "http_status=unknown", flush=True)
    finally:
        conn.close()


def live_watch_symbols(analyst_db):
    """Protect retained live setups when the analyst rotates to another window."""
    live_db = os.getenv("LS_LIVE_DB") or os.path.join(
        os.path.dirname(os.path.abspath(analyst_db)), "long_short_live_pool.db")
    if not os.path.isfile(live_db):
        return []
    try:
        with sqlite3.connect("file:" + os.path.abspath(live_db) + "?mode=ro",
                             uri=True, timeout=5) as conn:
            rows = conn.execute("""SELECT symbol FROM watch_state
                WHERE stage IN ('WATCH','APPROACHING','CLOSE_CONFIRMED','RETESTING','TRIGGERED')
                ORDER BY analyst_active DESC, analyst_confidence DESC, symbol""").fetchall()
        return list(dict.fromkeys(row[0] for row in rows
                    if isinstance(row[0], str) and row[0].endswith('USDT')))
    except (sqlite3.Error, OSError) as exc:
        log('FUTURES_LIVE_WATCH_UNAVAILABLE', {'error': str(exc)[:160]})
        return []


def requested_symbols(analyst_db):
    """Prepare the current and next scan before the analyst reaches them.

    Use the analyst's exact eligibility filters and rotation, not a second
    universe. All collectors share this bounded request list.
    """
    from long_short_runtime_profile import enabled as lean_enabled, demand as chart_demand
    if lean_enabled():
        return chart_demand(CHART_MAX_SYMBOLS, full_chart=True)[0]
    from long_short_v4_native_pipeline import choose_symbols
    forecast = []
    current = []
    upcoming = []
    if os.getenv('LS_NATIVE_CHAIN_V2','0') == '1':
        try:
            from long_short_analyst import native_futures_websocket_universe, MAX_SYMBOLS as scan_cap
            slot = int(time.time() // 300)
            # Both windows must use one catalogue/ticker snapshot: two reads
            # can disagree when eligibility changes near the volume floor.
            forecast = [row[0] for row in native_futures_websocket_universe(
                slot=slot, slot_offsets=(0, 1), quiet=True)]
            current, upcoming = forecast[:max(1,scan_cap)], forecast[max(1,scan_cap):]
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
            log('FUTURES_WINDOW_PREWARM_UNAVAILABLE', {'error':str(exc)[:160]})
    scan_symbols = []
    if os.path.isfile(analyst_db):
        try:
            with sqlite3.connect("file:" + os.path.abspath(analyst_db) + "?mode=ro", uri=True, timeout=5) as scan_db:
                latest = scan_db.execute("SELECT MAX(scan_time_utc) FROM full_universe_stage_audit").fetchone()[0]
                if latest:
                    scan_symbols = [row[0] for row in scan_db.execute(
                        "SELECT symbol FROM full_universe_stage_audit WHERE scan_time_utc=? ORDER BY quote_volume DESC LIMIT 30",
                        (latest,)).fetchall()]
        except (sqlite3.Error, OSError):
            pass
    selected = choose_symbols("", analyst_db=analyst_db, limit=30)
    pending=[]
    if os.getenv('LS_DATA_OPPORTUNITY_V2','0')=='1':
        from long_short_data_opportunity_v2 import pending_symbols
        pending=pending_symbols()
    # Live shortlisted coins precede future/research-only requests. Never
    # silently describe an oversized forecast as fully covered.
    # choose_symbols reads the analyst shortlist, not the retained live pool.
    # Without this reservation, a rotated-out setup loses its WS subscription
    # and REST repairs even while the live watcher still evaluates it.
    live = live_watch_symbols(analyst_db)
    paper=[]
    if os.getenv('LS_NATIVE_CHAIN_V2','0')=='1':
        try:
            from long_short_trade_ledger import pending_symbols as paper_pending_symbols
            paper=paper_pending_symbols(os.getenv('LS_LIVE_DB') or os.path.join(
                os.path.dirname(os.path.abspath(analyst_db)),'long_short_live_pool.db'))
        except (sqlite3.Error,OSError) as exc:
            log('PAPER_CANDLE_RESERVATION_ERROR',{'error_type':type(exc).__name__})
    ordered = list(dict.fromkeys(paper + live + current + selected + ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
                                + upcoming + scan_symbols + pending))
    if len(ordered) > MAX_SYMBOLS:
        log('FUTURES_REQUEST_CAPACITY', {'capacity':MAX_SYMBOLS,
            'deferred':ordered[MAX_SYMBOLS:],
            'deferred_live':[s for s in live if s not in ordered[:MAX_SYMBOLS]],
            'deferred_paper':[s for s in paper if s not in ordered[:MAX_SYMBOLS]],
            'deferred_forecast':[s for s in forecast if s not in ordered[:MAX_SYMBOLS]]})
    return ordered[:MAX_SYMBOLS]


def continuous_main():
    """Persistent Binance-only closed candle feed; one long-lived connection."""
    import queue
    import signal
    import threading
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    root = os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state"
    os.makedirs(root, exist_ok=True)
    db = os.path.join(root, "binance_futures_kline_shadow.db")
    analyst = os.getenv("LS_ANALYST_DB") or os.path.join(root, "long_short_analyst.db")
    initialize(db).close()

    # Archive imports never block the WebSocket. One worker, WAL + busy_timeout.
    pending = queue.Queue()
    scheduled = set()
    next_due = {}
    lock = threading.Lock()

    def archive_worker():
        while not stop.is_set():
            try:
                symbol = pending.get(timeout=1)
            except queue.Empty:
                continue
            try:
                result = backfill_binance_archive(db, [symbol])
                # Yesterday's file is published with a delay: retry hourly
                # until it exists, otherwise refresh every 6h.
                delay = 3600 if result["recent_misses"] else 6 * 3600
                print("FUTURES_KLINE_BACKFILL_FINISHED " + symbol, flush=True)
            except Exception as exc:
                delay = 1800
                print("FUTURES_KLINE_BACKFILL_ERROR " + symbol + " " + type(exc).__name__, flush=True)
            finally:
                with lock:
                    next_due[symbol] = time.monotonic() + delay
                    scheduled.discard(symbol)
                pending.task_done()

    threading.Thread(target=archive_worker, name="futures-archive-backfill", daemon=True).start()

    def requested():
        symbols = requested_symbols(analyst)
        with lock:
            due = [s for s in symbols if s not in scheduled and time.monotonic() >= next_due.get(s, 0)]
            for s in due:
                scheduled.add(s)
                pending.put(s)
        if due:
            log("FUTURES_KLINE_ARCHIVE_SCHEDULED", {"symbols": due})
        return symbols

    log("FUTURES_REST_BRIDGE_DISABLED_HTTP_418_451")
    from long_short_runtime_profile import enabled as lean_enabled
    stream = FuturesKlineStream(db, max_symbols=CHART_MAX_SYMBOLS if lean_enabled() else MAX_SYMBOLS)
    while not stop.is_set():
        try:
            stream.run(requested, stop)
        except Exception as exc:
            print("FUTURES_KLINE_FEED_ERROR " + type(exc).__name__ + ": " + str(exc)[:160], flush=True)
        stop.wait(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
