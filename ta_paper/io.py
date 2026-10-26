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
import uuid
import zlib

from .core import CFG, MINUTE, finite, closed_bars
from .rate_limit import CooldownError, RequestGate, request_weight, retry_deadline
from .provenance import identity


class DataUnavailable(RuntimeError):
    pass


class BookRejected(ValueError):
    def __init__(self, reason, evidence):
        super().__init__(reason)
        self.evidence = evidence


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DataUnavailable("REDIRECT_REFUSED")


class Binance:
    ALLOWED = frozenset(['/fapi/v1/time', '/fapi/v1/exchangeInfo',
                         '/fapi/v1/ticker/24hr', '/fapi/v1/klines',
                         '/fapi/v1/depth', '/fapi/v1/fundingRate'])

    def __init__(self, cooldown_path=None, legacy=None, candle_store=None):
        self.gate = RequestGate(cooldown_path, legacy)
        self.cache_lock = threading.Lock()
        self.candle_cache = {}
        self.candle_store = candle_store
        self.cache_hits = 0
        self.incremental_fetches = 0
        self.full_fetches = 0
        self.offset_ms = 0
        self.observed_weight = 0
        self.observed_weight_window = None
        self.local_requests = 0
        self.local_estimated_weight = 0
        self.local_minute = None
        self.local_minute_requests = 0
        self.local_minute_weight = 0
        self.endpoint_usage = {}

    def now(self):
        return int(time.time()*1000)+self.offset_ms

    def usage(self):
        return dict(reported_ip_weight_1m=self.observed_weight,
                    reported_minute=self.observed_weight_window,
                    local_requests_since_start=self.local_requests,
                    local_estimated_weight_since_start=self.local_estimated_weight,
                    local_request_minute=self.local_minute,
                    local_requests_1m=self.local_minute_requests,
                    local_estimated_weight_1m=self.local_minute_weight,
                    endpoint_usage=self.endpoint_usage.copy(),
                    protective_threshold=1000, candle_cache_hits=self.cache_hits,
                    incremental_candle_fetches=self.incremental_fetches,
                    full_candle_fetches=self.full_fetches)

    def get(self, endpoint, *, _unknown_recheck=False, **params):
        if endpoint not in self.ALLOWED:
            raise ValueError("NON_PUBLIC_ENDPOINT_REFUSED")
        if _unknown_recheck and (endpoint != '/fapi/v1/time' or params):
            raise ValueError('RECHECK_TIME_ENDPOINT_ONLY')
        weight=request_weight(endpoint,params)
        if (not _unknown_recheck and self.observed_weight_window==self.now()//MINUTE and
                self.observed_weight+weight>1000):
            self.gate.block((self.now()//MINUTE+1)*MINUTE+2000-self.offset_ms,
                            'SHARED_IP_WEIGHT_HIGH','WEIGHT_HEADER')
        try:
            self.gate.acquire(weight, unknown_recheck=_unknown_recheck)
        except CooldownError as exc:
            raise DataUnavailable(str(exc)) from None
        # One official host, no proxy/venue fallback or region switching.
        url = 'https://fapi.binance.com'+endpoint+'?'+urllib.parse.urlencode(params)
        self.local_requests+=1
        self.local_estimated_weight+=weight
        minute = self.now()//MINUTE
        if minute != self.local_minute:
            self.local_minute = minute
            self.local_minute_requests = self.local_minute_weight = 0
        self.local_minute_requests += 1
        self.local_minute_weight += weight
        self.endpoint_usage[endpoint] = self.endpoint_usage.get(endpoint, 0)+weight
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
                self.observed_weight = used
                self.observed_weight_window = self.now()//MINUTE
                if used >= 1000:
                    self.gate.block((self.now()//MINUTE+1)*MINUTE+2000-self.offset_ms,
                                    'SHARED_IP_WEIGHT_HIGH', 'WEIGHT_HEADER')
                    print('TA_PAPER_REQUEST_USAGE '+encode(self.usage()),flush=True)
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (418, 429, 451, 403):
                raw = exc.read(8192)
                if exc.headers.get('Content-Encoding') == 'gzip':
                    try:
                        raw = zlib.decompressobj(16+zlib.MAX_WBITS).decompress(raw, 8192)
                    except zlib.error:
                        raw = b''
                body = raw.decode('utf-8', errors='replace')
                until, source = retry_deadline(exc.code, exc.headers, body, int(time.time()*1000))
                self.gate.block(until, 'BINANCE_HTTP_'+str(exc.code), source)
                print('TA_PAPER_COOLDOWN '+encode(self.gate.state()), flush=True)
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

    def recheck_unknown_cooldown(self, request_id):
        """One user-requested GET on the same host; never override a server timer."""
        if not self.gate.reserve_unknown_recheck(request_id):
            return False
        begin = int(time.time()*1000)
        payload = self.get('/fapi/v1/time', _unknown_recheck=True)
        end = int(time.time()*1000)
        if not isinstance(payload, dict) or type(payload.get('serverTime')) is not int:
            raise DataUnavailable('INVALID_TIME_RESPONSE')
        if end-begin > 3000:
            raise DataUnavailable('CLOCK_SYNC_LATENCY')
        offset = payload['serverTime']-(begin+end)//2
        if abs(offset) > 5000:
            raise DataUnavailable('HOST_CLOCK_DRIFT')
        self.gate.confirm_unknown_recheck()
        self.offset_ms = offset
        if self.observed_weight >= 1000:
            self.gate.block((end//MINUTE+1)*MINUTE+2000, 'SHARED_IP_WEIGHT_HIGH', 'WEIGHT_HEADER')
        return True

    def candles(self, symbol, interval, limit=120, **params):
        # Reuse only verified, immutable closed bars. The decision window and
        # EMA warmup length remain the same as the full latest-N response.
        period = {'30m': 30*MINUTE, '4h': 240*MINUTE}.get(interval)
        key = (symbol, interval, limit)
        now = self.now()
        slot = (now-CFG.publication_delay_ms)//period if period else None
        cached = None
        use_cache = period and not params and limit == CFG.history_bars
        if use_cache:
            with self.cache_lock:
                cached = self.candle_cache.get(key)
            if not cached and self.candle_store:
                # These are already append-only evidence, retained across deploys.
                rows = self.candle_store.recent_candles(symbol, interval, limit-1)
                if rows:
                    try:
                        closed_bars(rows,period,now,require_current=False)
                        cached = (int(rows[-1][0])//period+1, rows)
                    except ValueError:
                        cached = None
            if cached and cached[0] == slot:
                closed_bars(cached[1],period,now)
                self.cache_hits += 1
                with self.cache_lock:
                    self.candle_cache[key] = cached
                return cached[1]
        incremental = bool(use_cache and cached and 0 < slot-cached[0] < 98)
        if incremental:
            # Include one closed overlap to detect revisions, and the open bar
            # to retain exactly the original latest-N window after merging.
            count = slot-cached[0]+2
            data = self.get('/fapi/v1/klines',symbol=symbol,interval=interval,
                            limit=count,startTime=(cached[0]-1)*period)
            self.incremental_fetches += 1
        else:
            data = self.get('/fapi/v1/klines', symbol=symbol, interval=interval, limit=limit, **params)
            if use_cache:self.full_fetches += 1
        if not isinstance(data, list):
            raise DataUnavailable("INVALID_KLINES_RESPONSE")
        if use_cache:
            if incremental:
                incoming = closed_bars(data,period,now,minimum=1)
                old = {int(row[0]): row for row in cached[1]}
                for row in incoming:
                    if row[0] in old and closed_bars([old[row[0]]],period,now,minimum=1,require_current=False)[0] != row:
                        raise DataUnavailable('CLOSED_CANDLE_CHANGED')
                # Never join around a missing new candle or fabricate a bar.
                combined = dict(old)
                combined.update({int(row[0]):row for row in data})
                data = [combined[k] for k in sorted(combined)][-limit:]
            verified = closed_bars(data,period,now)
            if self.candle_store:self.candle_store.candles(symbol,interval,verified)
            with self.cache_lock:
                self.candle_cache[key] = (slot, verified)
            return verified
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


def book_quote(book, now, cfg=CFG):
    """Validate the quote before it is used to size a proposed position."""
    if not 0 <= now-int(book.get('E', 0)) <= cfg.max_book_age_ms:
        raise ValueError("STALE_BOOK")
    bids, asks = book['bids'], book['asks']
    if not bids or not asks:
        raise ValueError('EMPTY_BOOK')
    bid, ask = finite(bids[0][0], True), finite(asks[0][0], True)
    if ask < bid or (ask-bid)/((ask+bid)/2) > cfg.max_spread_fraction:
        raise ValueError("SPREAD_TOO_WIDE")
    return dict(bid=bid, ask=ask, spread=(ask-bid)/((ask+bid)/2),
                observed_ms=now, event_ms=int(book['E']))


def book_check(book, now, direction, notional, cfg=CFG, *, quantity=None):
    evidence = book_quote(book, now, cfg)
    bids, asks = book['bids'], book['asks']
    bid, ask = evidence['bid'], evidence['ask']
    if direction not in ('LONG', 'SHORT'):
        raise ValueError('INVALID_DIRECTION')
    levels = asks if direction == 'LONG' else bids
    by_quantity = quantity is not None
    requested = finite(quantity if by_quantity else notional, True)
    tolerance = max(1e-14,requested*1e-12) if by_quantity else 1e-8
    remaining, filled_quantity, filled_notional = requested, 0.0, 0.0
    evidence.update(planned_notional=finite(notional, True),
                    requested_quantity=quantity, impact_limit=cfg.entry_slippage)
    last_price = None
    for raw_price, raw_qty in levels:
        price, qty = finite(raw_price, True), finite(raw_qty, True)
        if last_price is not None and ((direction == 'LONG' and price < last_price) or
                                      (direction == 'SHORT' and price > last_price)):
            raise ValueError("UNSORTED_BOOK")
        take = min(remaining, qty if by_quantity else price*qty)
        units = take if by_quantity else take/price
        filled_quantity += units
        filled_notional += units*price
        remaining -= take
        last_price = price
        if remaining <= tolerance:
            break
    if remaining > tolerance:
        raise BookRejected("INSUFFICIENT_BOOK_DEPTH",dict(evidence,remaining=remaining))
    vwap = filled_notional/filled_quantity
    best = ask if direction == 'LONG' else bid
    evidence.update(vwap=vwap, impact_fraction=abs(vwap/best-1),
                    checked_quantity=filled_quantity, checked_notional=filled_notional)
    if evidence['impact_fraction'] > cfg.entry_slippage:
        raise BookRejected("BOOK_IMPACT_EXCEEDS_MODEL", evidence)
    return evidence


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class Store:
    def __init__(self, path, cfg=CFG):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.provenance = identity()
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
          CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,symbol TEXT,start_ms INTEGER,
            end_ms INTEGER,cursor_ms INTEGER,status TEXT,next_fetch_ms INTEGER,attempts INTEGER,
            repairs INTEGER,payload TEXT);
          CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
          CREATE INDEX IF NOT EXISTS observation_fetch ON observations(status,next_fetch_ms);
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(outbox)')}
        if 'photo' not in columns:
            self.db.execute('ALTER TABLE outbox ADD COLUMN photo BLOB')
        if 'retry_at_ms' not in columns:
            self.db.execute('ALTER TABLE outbox ADD COLUMN retry_at_ms INTEGER NOT NULL DEFAULT 0')
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
        for k,v in self.provenance.items():
            p.setdefault(k,v)
        key = f"{p['symbol']}:{p['candle_ms']}:{p['config_hash']}"
        cur = self.db.execute('INSERT OR IGNORE INTO assessments VALUES(?,?,?,?)',
                              (key, p['observed_ms'], p['symbol'], encode(p)))
        self.db.commit()
        return cur.rowcount == 1

    def save(self, p):
        for k,v in self.provenance.items():
            p.setdefault(k,v)
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
            sql += " WHERE status IN ('PENDING','OPEN','FUNDING_PENDING','DATA_HOLD','UNRESOLVED_DATA')"
        return [json.loads(r[0]) for r in self.db.execute(sql)]

    def setting(self, key, value=None):
        if value is not None:
            self.db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            (key, str(value)))
            self.db.commit()
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def chart_bars(self, p):
        rows = self.db.execute("SELECT payload FROM candles WHERE symbol=? AND tf='30m' "
                               'AND open_ms<=? ORDER BY open_ms DESC LIMIT 60',
                               (p['symbol'], p['candle_ms'])).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def recent_candles(self, symbol, tf, limit):
        rows = self.db.execute('SELECT payload FROM candles WHERE symbol=? AND tf=? '
                               'ORDER BY open_ms DESC LIMIT ?', (symbol,tf,limit)).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def legacy_cooldown(self):
        # The first release retained the ban event, but not Retry-After. Preserve
        # the documented upper bound rather than resetting it on deployment.
        latest = self.db.execute("SELECT id,ms,payload FROM events WHERE kind='DATA_HEALTH_INVALID' "
                                 'ORDER BY id DESC LIMIT 1').fetchone()
        if not latest or json.loads(latest['payload']).get('reason') not in ('BINANCE_HTTP_418','EXCHANGE_COOLDOWN'):
            return None
        success = self.db.execute("SELECT MAX(id) FROM events WHERE kind='SCAN_HEALTH'").fetchone()[0] or 0
        if success > latest['id']:
            return None
        for row in self.db.execute("SELECT id,ms,payload FROM events WHERE kind='DATA_HEALTH_INVALID' "
                                    'AND id>? ORDER BY id DESC', (success,)):
            if json.loads(row['payload']).get('reason') == 'BINANCE_HTTP_418':
                until = row['ms']+72*3600*1000
                if until > int(time.time()*1000):
                    return dict(until_ms=until, reason='BINANCE_HTTP_418', source='LEGACY_UNKNOWN_UPPER_BOUND')
                break
        return None

    def queue(self, key, text, now, photo=None):
        self.db.execute('INSERT OR IGNORE INTO outbox(key,ms,text,state,photo) VALUES(?,?,?,?,?)',
                        (key, now, text, 'PENDING', photo))
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

    def flush(self, store, now, signals_enabled=True, clock=None):
        if not self.ready:
            return 0
        if now < int(store.setting('telegram_retry_at_ms') or 0):
            return 0
        delivered = 0
        for row in store.db.execute("SELECT * FROM outbox WHERE state='PENDING' AND retry_at_ms<=? "
                                    "AND (? OR key LIKE 'system:%') "
                                    "AND (key LIKE 'system:%' OR EXISTS (SELECT 1 FROM plans WHERE "
                                    "outbox.key IN (plans.id||':PENDING',plans.id||':OPEN',"
                                    "plans.id||':CLOSED',plans.id||':CANCELLED'))) "
                                    "ORDER BY (key LIKE 'system:%'),ms LIMIT 5", (now,signals_enabled)).fetchall():
            # Earlier deliveries can block for seconds; don't send a plan that
            # expired while waiting behind another notification.
            if clock is not None:
                now = clock()
            if not row['key'].startswith('system:'):
                plan_id,_,notice_status=row['key'].rpartition(':')
                saved=store.db.execute('SELECT payload FROM plans WHERE id=?',(plan_id,)).fetchone()
                if not saved:
                    # Only durable, individually admitted plans can be delivered
                    # independently of the global Binance request guard.
                    continue
                if saved:
                    plan=json.loads(saved[0])
                    superseded=(notice_status in ('PENDING','OPEN') and plan['status']!=notice_status)
                    late_entry=(notice_status=='PENDING' and now>=plan['entry_ms'])
                    if superseded or late_entry:
                        store.db.execute("UPDATE outbox SET state='STALE' WHERE key=?",(row['key'],))
                        store.db.commit()
                        continue
            if now-row['ms'] > 10*MINUTE:
                store.db.execute("UPDATE outbox SET state='STALE' WHERE key=?", (row['key'],))
                store.db.commit()
                continue
            # Persist before send: an ambiguous network failure is not retried
            # blindly, which prevents duplicate trade messages after restart.
            store.db.execute("UPDATE outbox SET state='DELIVERY_UNKNOWN' WHERE key=?", (row['key'],))
            store.db.commit()
            method = 'sendPhoto' if row['photo'] else 'sendMessage'
            if row['photo']:
                caption = row['text'] if len(row['text']) <= 1024 else row['text'][:900]+'\nTEST: Gerçek emir yok. Başarı DOĞRULANMADI.'
                body, content_type = photo_body(self.chat, caption, row['photo'])
            else:
                body = encode({'chat_id': self.chat, 'text': row['text'], 'disable_web_page_preview': True}).encode()
                content_type = 'application/json'
            req = urllib.request.Request('https://api.telegram.org/bot'+self.token+'/'+method,
                                         data=body, headers={'Content-Type': content_type}, method='POST')
            try:
                with urllib.request.build_opener(NoRedirect).open(req, timeout=25) as response:
                    result = json.load(response)
                if result.get('ok') and str(result['result']['chat']['id']) == self.chat:
                    store.db.execute("UPDATE outbox SET state='DELIVERED',message_id=? WHERE key=?",
                                     (str(result['result']['message_id']), row['key']))
                    store.db.commit()
                    store.event('TELEGRAM_ACK', row['key'], {'delivered': True})
                    print('TA_PAPER_TELEGRAM_ACK '+encode({'key': row['key'], 'method': method, 'delivered': True}), flush=True)
                    delivered += 1
                else:
                    store.event('TELEGRAM_REJECTED', row['key'], {'method': method})
                    print('TA_PAPER_TELEGRAM_REJECTED '+encode({'key': row['key'], 'method': method}), flush=True)
            except urllib.error.HTTPError as exc:
                # A documented 429 rejection is safe to retry after its deadline.
                if exc.code == 429:
                    try:
                        seconds = max(1, int(json.loads(exc.read(8192)).get('parameters', {}).get('retry_after', 60)))
                    except (ValueError, TypeError):
                        seconds = 60
                    until = now+seconds*1000+2000
                    store.setting('telegram_retry_at_ms', until)
                    store.db.execute("UPDATE outbox SET state='PENDING',retry_at_ms=? WHERE key=?", (until,row['key']))
                    store.db.commit()
                    print('TA_PAPER_TELEGRAM_RATE_LIMIT '+encode({'retry_at_ms': until}), flush=True)
                    break
                store.event('TELEGRAM_ERROR', row['key'], {'http_status': exc.code, 'method': method})
                print('TA_PAPER_TELEGRAM_ERROR '+encode({'http_status': exc.code,'method':method}), flush=True)
            except Exception as exc:
                # Exception text can contain the token-bearing URL.
                store.event('TELEGRAM_ERROR', row['key'], {'type': type(exc).__name__})
                print('TA_PAPER_TELEGRAM_ERROR '+encode({'type':type(exc).__name__,'method':method}), flush=True)
        return delivered


def photo_body(chat, caption, photo):
    boundary = 'ta-paper-'+uuid.uuid4().hex
    parts = []
    for name, value in [('chat_id', chat), ('caption', caption)]:
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                      f'{value}\r\n').encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="plan.png"\r\n'
                  'Content-Type: image/png\r\n\r\n').encode()+photo+b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), 'multipart/form-data; boundary='+boundary

