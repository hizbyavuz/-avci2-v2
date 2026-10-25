"""Read-only Binance client, append-only evidence and explicit Telegram outbox."""
import gzip
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .core import CFG, MINUTE, finite


class DataUnavailable(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DataUnavailable("REDIRECT_REFUSED")


class Binance:
    ALLOWED = frozenset(['/fapi/v1/time', '/fapi/v1/exchangeInfo',
                         '/fapi/v1/ticker/24hr', '/fapi/v1/klines',
                         '/fapi/v1/depth', '/fapi/v1/fundingRate'])

    def __init__(self):
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.blocked_until = 0.0
        self.offset_ms = 0

    def now(self):
        return int(time.time()*1000)+self.offset_ms

    def get(self, endpoint, **params):
        if endpoint not in self.ALLOWED:
            raise ValueError("NON_PUBLIC_ENDPOINT_REFUSED")
        with self.lock:
            if time.monotonic() < self.blocked_until:
                raise DataUnavailable("EXCHANGE_COOLDOWN")
            delay = max(0, self.next_request-time.monotonic())
            if delay:
                time.sleep(delay)
            self.next_request = time.monotonic()+0.17
        # One official host, no proxy/venue fallback or region switching.
        url = 'https://fapi.binance.com'+endpoint+'?'+urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': 'TA-Paper-Research/1',
                                                  'Accept-Encoding': 'gzip'}, method='GET')
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=12) as resp:
                raw = resp.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise DataUnavailable("OVERSIZED_RESPONSE")
                if resp.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                used = int(resp.headers.get('X-MBX-USED-WEIGHT-1M', 0))
                if used > 1200:
                    with self.lock:
                        self.blocked_until = time.monotonic()+60
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (418, 429, 451, 403):
                with self.lock:
                    try:
                        delay = max(60, int(exc.headers.get('Retry-After', 60)))
                    except (TypeError, ValueError):
                        delay = 60
                    self.blocked_until = time.monotonic()+(3600 if exc.code in (451, 403) else delay)
            raise DataUnavailable('BINANCE_HTTP_'+str(exc.code)) from None
        except (OSError, ValueError) as exc:
            raise DataUnavailable(type(exc).__name__) from None

    def sync_time(self):
        begin = int(time.time()*1000)
        payload = self.get('/fapi/v1/time')
        end = int(time.time()*1000)
        if end-begin > 3000:
            raise DataUnavailable("CLOCK_SYNC_LATENCY")
        self.offset_ms = int(payload['serverTime'])-(begin+end)//2
        if abs(self.offset_ms) > 5000:
            raise DataUnavailable("HOST_CLOCK_DRIFT")

    def candles(self, symbol, interval, limit=120, **params):
        data = self.get('/fapi/v1/klines', symbol=symbol, interval=interval, limit=limit, **params)
        if not isinstance(data, list):
            raise DataUnavailable("INVALID_KLINES_RESPONSE")
        return data

    def funding(self, symbol, start, end):
        rows, cursor = [], start
        while cursor <= end:
            part = self.get('/fapi/v1/fundingRate', symbol=symbol, startTime=cursor,
                            endTime=end, limit=1000)
            if not isinstance(part, list):
                raise DataUnavailable("INVALID_FUNDING_RESPONSE")
            rows.extend(part)
            if len(part) < 1000:
                return rows
            next_cursor = int(part[-1]['fundingTime'])+1
            if next_cursor <= cursor:
                raise DataUnavailable("FUNDING_PAGINATION_STALLED")
            cursor = next_cursor
        return rows


def tradable(market, ticker, now, cfg=CFG):
    if any(market.get(k) != v for k, v in dict(status='TRADING', contractType='PERPETUAL',
                                              quoteAsset='USDT', marginAsset='USDT',
                                              underlyingType='COIN').items()):
        return False
    subtypes = ' '.join(market.get('underlyingSubType', [])).upper()
    if any(tag in subtypes for tag in ('STOCK', 'COMMOD', 'TRADFI', 'INDEX', 'FOREX', 'PREMARKET')):
        return False
    if market.get('baseAsset', '').upper() in {
            'USDC', 'FDUSD', 'TUSD', 'DAI', 'USDP', 'BUSD', 'USDE', 'USD1',
            'XAU', 'XAG', 'XPT', 'XPD', 'CL', 'BZ', 'US500', 'US100'}:
        return False
    onboard = int(market.get('onboardDate', 0))
    if onboard <= 0 or now-onboard < cfg.min_listing_days*86_400_000:
        return False
    if not ticker or ticker.get('symbol') != market.get('symbol'):
        return False
    age = now-int(ticker.get('closeTime', 0))
    return 0 <= age <= 120_000 and finite(ticker.get('quoteVolume', 0)) >= cfg.min_quote_volume


def book_check(book, now, direction, notional, cfg=CFG):
    if not 0 <= now-int(book.get('E', 0)) <= cfg.max_book_age_ms:
        raise ValueError("STALE_BOOK")
    bids, asks = book['bids'], book['asks']
    bid, ask = finite(bids[0][0], True), finite(asks[0][0], True)
    if ask < bid or (ask-bid)/((ask+bid)/2) > cfg.max_spread_fraction:
        raise ValueError("SPREAD_TOO_WIDE")
    levels = asks if direction == 'LONG' else bids
    remaining, quantity = notional, 0.0
    last_price = None
    for raw_price, raw_qty in levels:
        price, qty = finite(raw_price, True), finite(raw_qty, True)
        if last_price is not None and ((direction == 'LONG' and price < last_price) or
                                      (direction == 'SHORT' and price > last_price)):
            raise ValueError("UNSORTED_BOOK")
        take = min(remaining, price*qty)
        quantity += take/price
        remaining -= take
        last_price = price
        if remaining <= 1e-8:
            break
    if remaining > 1e-8:
        raise ValueError("INSUFFICIENT_BOOK_DEPTH")
    vwap = notional/quantity
    best = ask if direction == 'LONG' else bid
    if abs(vwap/best-1) > cfg.entry_slippage:
        raise ValueError("BOOK_IMPACT_EXCEEDS_MODEL")
    return dict(bid=bid, ask=ask, spread=(ask-bid)/((ask+bid)/2), vwap=vwap,
                observed_ms=now, event_ms=int(book['E']))


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class Store:
    def __init__(self, path, cfg=CFG):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS manifests(hash TEXT PRIMARY KEY,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ms INTEGER,kind TEXT,key TEXT,payload TEXT);
          CREATE TABLE IF NOT EXISTS candles(symbol TEXT,tf TEXT,open_ms INTEGER,payload TEXT,
            PRIMARY KEY(symbol,tf,open_ms));
          CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,ms INTEGER,kind TEXT,payload BLOB);
          CREATE TABLE IF NOT EXISTS assessments(key TEXT PRIMARY KEY,ms INTEGER,symbol TEXT,payload TEXT);
          CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,config_hash TEXT,status TEXT,symbol TEXT,
            direction TEXT,created_ms INTEGER,payload TEXT);
          CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY,ms INTEGER,text TEXT,state TEXT,message_id TEXT);
          CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        ''')
        from dataclasses import asdict
        self.db.execute('INSERT OR IGNORE INTO manifests VALUES(?,?)', (cfg.fingerprint, encode(asdict(cfg))))
        hashes = [r[0] for r in self.db.execute('SELECT hash FROM manifests')]
        if hashes != [cfg.fingerprint]:
            raise RuntimeError("CONFIG_CHANGED_USE_NEW_DATABASE")
        self.db.commit()

    def event(self, kind, key, payload, now=None):
        self.db.execute('INSERT INTO events(ms,kind,key,payload) VALUES(?,?,?,?)',
                        (now or int(time.time()*1000), kind, key, encode(payload)))
        self.db.commit()

    def snapshot(self, kind, data, now):
        self.db.execute('INSERT INTO snapshots(ms,kind,payload) VALUES(?,?,?)',
                        (now, kind, zlib.compress(encode(data).encode())))
        self.db.commit()

    def candles(self, symbol, tf, rows):
        for row in rows:
            payload = encode(row)
            old = self.db.execute('SELECT payload FROM candles WHERE symbol=? AND tf=? AND open_ms=?',
                                  (symbol, tf, row[0])).fetchone()
            if old and old[0] != payload:
                self.event('DATA_ISSUE', symbol, {'reason': 'CLOSED_CANDLE_CHANGED', 'tf': tf, 'open_ms': row[0]})
                raise ValueError('CLOSED_CANDLE_CHANGED')
            self.db.execute('INSERT OR IGNORE INTO candles VALUES(?,?,?,?)', (symbol, tf, row[0], payload))
        self.db.commit()

    def assessment(self, p):
        key = f"{p['symbol']}:{p['candle_ms']}:{p['config_hash']}"
        cur = self.db.execute('INSERT OR IGNORE INTO assessments VALUES(?,?,?,?)',
                              (key, p['observed_ms'], p['symbol'], encode(p)))
        self.db.commit()
        return cur.rowcount == 1

    def save(self, p):
        self.db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                        'status=excluded.status,payload=excluded.payload',
                        (p['id'], p['config_hash'], p['status'], p['symbol'], p['direction'],
                         p['observed_ms'], encode(p)))
        self.db.execute('INSERT INTO events(ms,kind,key,payload) VALUES(?,?,?,?)',
                        (int(time.time()*1000), 'PLAN_STATE', p['id'], encode(p)))
        self.db.commit()

    def plans(self, active=False):
        sql = 'SELECT payload FROM plans'
        if active:
            sql += " WHERE status IN ('PENDING','OPEN','FUNDING_PENDING','DATA_HOLD')"
        return [json.loads(r[0]) for r in self.db.execute(sql)]

    def setting(self, key, value=None):
        if value is not None:
            self.db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            (key, str(value)))
            self.db.commit()
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def queue(self, key, text, now):
        self.db.execute('INSERT OR IGNORE INTO outbox VALUES(?,?,?,?,NULL)', (key, now, text, 'PENDING'))
        self.db.commit()


class Telegram:
    def __init__(self, root, enabled=False):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip() if enabled else ''
        self.chat = os.getenv('TELEGRAM_CHAT_ID', '').strip()
        # Reuse only the destination previously configured and successfully used
        # by this user's motor. Never pick a stranger from getUpdates.
        cache = Path(root)/'long_short_telegram_chat_cache.db'
        if not self.chat and cache.is_file():
            try:
                with sqlite3.connect('file:'+str(cache.resolve())+'?mode=ro', uri=True) as db:
                    row = db.execute("SELECT value FROM runtime_settings WHERE key='telegram_chat_id'").fetchone()
                    self.chat = str(row[0]).strip() if row else ''
            except sqlite3.Error:
                pass

    @property
    def ready(self):
        return bool(self.token and self.chat)

    def flush(self, store, now):
        if not self.ready:
            return 0
        delivered = 0
        for row in store.db.execute("SELECT * FROM outbox WHERE state='PENDING' ORDER BY ms LIMIT 5").fetchall():
            if now-row['ms'] > 10*MINUTE:
                store.db.execute("UPDATE outbox SET state='STALE' WHERE key=?", (row['key'],))
                store.db.commit()
                continue
            # Persist before send: an ambiguous network failure is not retried
            # blindly, which prevents duplicate trade messages after restart.
            store.db.execute("UPDATE outbox SET state='DELIVERY_UNKNOWN' WHERE key=?", (row['key'],))
            store.db.commit()
            body = encode({'chat_id': self.chat, 'text': row['text'], 'disable_web_page_preview': True}).encode()
            req = urllib.request.Request('https://api.telegram.org/bot'+self.token+'/sendMessage',
                                         data=body, headers={'Content-Type': 'application/json'}, method='POST')
            try:
                with urllib.request.build_opener(NoRedirect).open(req, timeout=12) as response:
                    result = json.load(response)
                if result.get('ok') and str(result['result']['chat']['id']) == self.chat:
                    store.db.execute("UPDATE outbox SET state='DELIVERED',message_id=? WHERE key=?",
                                     (str(result['result']['message_id']), row['key']))
                    store.db.commit()
                    store.event('TELEGRAM_ACK', row['key'], {'delivered': True})
                    delivered += 1
            except Exception as exc:
                # Exception text can contain the token-bearing URL.
                store.event('TELEGRAM_ERROR', row['key'], {'type': type(exc).__name__})
        return delivered
