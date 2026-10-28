"""Official USD-M market streams and durable closed bars; no account or order API."""
from collections import deque
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import quote
import zipfile

from .archive import Archive
from .core import CFG, MINUTE, closed_bars, finite

PERIODS = {'1m': MINUTE, '30m': 30*MINUTE, '4h': 240*MINUTE}
METADATA_TTL = 6*3600*1000
URL = 'wss://fstream.binance.com/market/stream'
# Keep subscription frames and per-socket traffic small. The exchange's 1024
# stream ceiling is not a target batch size for one SUBSCRIBE frame.
SYMBOLS_PER_CONNECTION = 60
CONTROL_GROUP = 'market-control'
SUBSCRIPTION_ID = 1


def log(kind, **data):
    print(kind+' '+json.dumps(data, ensure_ascii=False, allow_nan=False), flush=True)


class StreamMissing(ValueError): pass


class LiveFeed:
    def __init__(self, directory, gate, history):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.gate, self.history = gate, history
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.threads = []
        self.groups = {}
        self.symbol_groups = {}
        self.metadata = None
        self.metadata_ms = 0
        self.tickers = {}
        self.invalid_contracts = {}
        self.closed_count = self.cache_hits = 0
        self.archive_requests = deque()
        self.archive_queued = set()
        self.archive_retry = {}
        self.next_archive_prime = 0
        # Snapshot the engine's existing tails on its owning thread. Background
        # archive work must never use the engine SQLite connection directly.
        self.history_tails = {(r[0],r[1]):r[2] for r in history.db.execute(
            "SELECT symbol,tf,MAX(open_ms) FROM candles WHERE tf IN ('30m','4h') GROUP BY symbol,tf")}
        self.connect_lock = threading.Lock()
        self.next_connect = 0
        self.db = sqlite3.connect(self.directory/'market.sqlite3', timeout=10, check_same_thread=False)
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS bars(symbol TEXT,tf TEXT,ms INTEGER,payload TEXT,source TEXT,
            PRIMARY KEY(symbol,tf,ms));
          CREATE TABLE IF NOT EXISTS issues(symbol TEXT,tf TEXT,ms INTEGER,reason TEXT);
          CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,payload TEXT);
        ''')
        from .features import init
        init(self.db)
        row = self.db.execute("SELECT payload FROM state WHERE key='invalid_contracts'").fetchone()
        if row: self.invalid_contracts = json.loads(row[0])
        row = self.db.execute("SELECT payload FROM state WHERE key='metadata'").fetchone()
        if row:
            value = json.loads(row[0]); self.metadata_ms = value['ms']; self.metadata = value['data']
        else:
            # Read old universe evidence without changing its timestamp.
            row = history.db.execute("SELECT ms,payload FROM snapshots WHERE kind='UNIVERSE' ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                import zlib
                self.metadata = json.loads(zlib.decompress(row['payload']))['metadata']
                self.metadata_ms = self.metadata.get('_ta_metadata_ms',row['ms'])

    def start(self):
        self._spawn(self._manager)
        self._spawn(self._archive_worker)

    def _spawn(self, fn, *args):
        thread = threading.Thread(target=fn, args=args, daemon=True)
        self.threads.append(thread); thread.start()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=2)
        # In-flight archive reads may take 30s. They check stop before any write;
        # leave this connection alive until process exit if a reader is winding down.
        if not any(t.is_alive() for t in self.threads):
            self.db.close()

    def set_metadata(self, payload, now):
        if not isinstance(payload, dict) or not isinstance(payload.get('symbols'), list):
            raise StreamMissing('METADATA_INVALID')
        with self.lock:
            self.metadata, self.metadata_ms = payload, now
            self.invalid_contracts = {s:t for s,t in self.invalid_contracts.items() if t > now}
            self.db.execute("INSERT OR REPLACE INTO state VALUES('metadata',?)",
                            (json.dumps({'ms': now, 'data': payload}),))
            self.db.commit()

    def _fresh(self, group, now):
        value = self.groups.get(group, {})
        return bool(value.get('connected') and value.get('acked') and
                    0 <= now-value.get('last_event', 0) <= 30_000)

    def ready(self, now):
        with self.lock:
            return self._fresh(CONTROL_GROUP, now) and bool(self.metadata) and 0 <= now-self.metadata_ms <= METADATA_TTL

    def cached_metadata(self, now):
        if not self.ready(now): raise StreamMissing('STREAM_METADATA_NOT_READY')
        with self.lock:
            return dict(self.metadata, _ta_metadata_ms=self.metadata_ms,
                        symbols=[m for m in self.metadata['symbols']
                        if self.invalid_contracts.get(m['symbol'], 0) <= self.metadata_ms])

    def cached_tickers(self, now):
        if not self.ready(now): raise StreamMissing('STREAM_TICKERS_NOT_READY')
        with self.lock:
            result = [dict(v) for v in self.tickers.values() if 0 <= now-v['closeTime'] <= 120_000]
        if not result: raise StreamMissing('STREAM_TICKERS_EMPTY')
        return result

    def status(self, now):
        with self.lock:
            return dict(mode='BINANCE_MARKET_STREAM', ready=self.ready(now),
                        connections=len(self.groups), healthy_connections=sum(self._fresh(g,now) for g in self.groups),
                        subscribed_symbols=len(self.symbol_groups), metadata_age_seconds=max(0,(now-self.metadata_ms)//1000),
                        control_healthy=self._fresh(CONTROL_GROUP,now), symbols_per_connection=SYMBOLS_PER_CONNECTION,
                        closed_bars_received=self.closed_count, cache_hits=self.cache_hits,
                        archive_pending=len(self.archive_requests), invalid_contracts=len(self.invalid_contracts))

    def put_bars(self, symbol, tf, rows, source, now):
        with self.lock:
            from .features import capture_klines
            # Trigger/context factors need 30m/4h. Keep the existing full 1m
            # price path without duplicating every universe minute's aux data.
            if tf!='1m':
                capture_klines(self.db,symbol,tf,rows,now,source)
            for raw in rows:
                # Validate geometry/closure but not sequence: gaps remain gaps.
                row = closed_bars([raw], PERIODS[tf], now, minimum=1, require_current=False)[0]
                payload = json.dumps(row, separators=(',', ':'), allow_nan=False)
                old = self.db.execute('SELECT payload FROM bars WHERE symbol=? AND tf=? AND ms=?',
                                      (symbol,tf,row[0])).fetchone()
                if old and old[0] != payload:
                    self.db.execute('INSERT INTO issues VALUES(?,?,?,?)',
                                    (symbol,tf,row[0],'CLOSED_CANDLE_CHANGED'))
                    self.db.commit()
                    raise StreamMissing('CLOSED_CANDLE_CHANGED')
                self.db.execute('INSERT OR IGNORE INTO bars VALUES(?,?,?,?,?)', (symbol,tf,row[0],payload,source))
            self.db.commit()

    def copy_aux(self, destination, symbol, tf, rows):
        """Copy optional fields on the caller's SQLite thread; core bars stay 7-wide."""
        with self.lock:
            for row in rows:
                aux = self.db.execute('SELECT * FROM candle_aux WHERE symbol=? AND tf=? AND ms=?',
                                      (symbol,tf,row[0])).fetchone()
                if aux:
                    old=destination.execute('SELECT payload FROM candle_aux WHERE symbol=? AND tf=? AND ms=?',
                                            (symbol,tf,row[0])).fetchone()
                    if old and old[0]!=aux[3]:
                        destination.execute('INSERT INTO aux_issues(ms,symbol,reason) VALUES(?,?,?)',
                                            (aux[4],symbol,'REST_STREAM_AUX_CONFLICT'))
                    destination.execute('INSERT OR IGNORE INTO candle_aux VALUES(?,?,?,?,?,?)',tuple(aux))
            destination.commit()

    def ingest(self, group, payload, now):
        data = payload.get('data', payload) if isinstance(payload, dict) else payload
        values = data if isinstance(data, list) else [data]
        good = False
        with self.lock:
            for event in values:
                if not isinstance(event, dict): raise StreamMissing('STREAM_MESSAGE_INVALID')
                if 'result' in event:
                    if type(event.get('id')) is not int or event['id'] != SUBSCRIPTION_ID:
                        raise StreamMissing('STREAM_SUBSCRIPTION_ACK_MISMATCH')
                    if event['result'] is not None: raise StreamMissing('STREAM_SUBSCRIPTION_REJECTED')
                    self.groups[group]['acked'] = True
                    continue
                if 'code' in event: raise StreamMissing('STREAM_SUBSCRIPTION_REJECTED')
                if event.get('e') == 'serverShutdown': raise StreamMissing('STREAM_SHUTDOWN')
                if event.get('st', 1) != 1: continue  # Never mix COIN-M into USD-M.
                stamp = event.get('E')
                if type(stamp) is not int or abs(now-stamp) > 5000:
                    raise StreamMissing('STREAM_CLOCK_OR_LATENCY')
                good = True
                symbol = event.get('s', '')
                if event.get('e') == '24hrTicker':
                    volume = finite(event['q'])
                    if volume < 0: raise StreamMissing('STREAM_VOLUME_INVALID')
                    close_time = int(event['C'])
                    if not 0 <= now-close_time <= 120_000: continue
                    self.tickers[symbol] = dict(symbol=symbol,quoteVolume=volume,closeTime=close_time)
                elif event.get('e') == 'contractInfo':
                    # Changed contract/filter data must be re-fetched before admission.
                    self.invalid_contracts[symbol] = now
                    self.db.execute("INSERT OR REPLACE INTO state VALUES('invalid_contracts',?)",
                                    (json.dumps(self.invalid_contracts),))
                    self.db.commit()
                elif event.get('e') == 'kline':
                    bar = event['k']; tf = bar['i']
                    if (tf not in PERIODS or bar['s'] != symbol or
                            self.symbol_groups.get(symbol) != group):
                        raise StreamMissing('STREAM_SYMBOL_MISMATCH')
                    if bar.get('x') is not True: continue
                    start, end = int(bar['t']), int(bar['T'])
                    if end > now+1000: raise StreamMissing('STREAM_FUTURE_BAR')
                    row = [start,bar['o'],bar['h'],bar['l'],bar['c'],bar['v'],end]
                    if all(k in bar for k in ('q','n','V','Q')):
                        row += [bar['q'],bar['n'],bar['V'],bar['Q']]
                    # Save final exchange bars immediately; readers still enforce
                    # the frozen 2s publication delay before using them.
                    self.put_bars(symbol,tf,[row],'BINANCE_WS',max(now,end+CFG.publication_delay_ms+1))
                    self.closed_count += 1
            if good: self.groups[group]['last_event'] = now

    def candles(self, symbol, tf, now, limit=120, **params):
        if tf not in PERIODS: raise StreamMissing('STREAM_TIMEFRAME_UNSUPPORTED')
        period = PERIODS[tf]
        latest = ((now-CFG.publication_delay_ms)//period-1)*period
        start = ((int(params['startTime'])+period-1)//period)*period if 'startTime' in params else latest-(limit-2)*period
        end = min(latest, int(params.get('endTime',latest))//period*period)
        end = min(end,start+(limit-1)*period) if params else end
        if end < start: return []
        # Existing immutable engine evidence seeds history across deploys.
        known = {int(r[0]):r for r in self.history.recent_candles(symbol,tf,max(limit,120))}
        with self.lock:
            if self.db.execute('SELECT 1 FROM issues WHERE symbol=? AND tf=? AND ms BETWEEN ? AND ? LIMIT 1',
                               (symbol,tf,start,end)).fetchone():
                raise StreamMissing('CLOSED_CANDLE_CHANGED')
            for (payload,) in self.db.execute('SELECT payload FROM bars WHERE symbol=? AND tf=? AND ms BETWEEN ? AND ?',
                                              (symbol,tf,start,end)):
                row = json.loads(payload)
                if row[0] in known and row != known[row[0]]: raise StreamMissing('CLOSED_CANDLE_CHANGED')
                known[row[0]] = row
            missing = [at for at in range(start,end+1,period) if at not in known]
            if missing:
                self._queue_archive(symbol,tf,min(missing),max(missing),now)
                raise StreamMissing('STREAM_HISTORY_GAP')
            rows = [known[at] for at in range(start,end+1,period)]
            closed_bars(rows,period,now,minimum=1 if params else limit-1,require_current=not bool(params))
            self.cache_hits += 1
            return rows

    def _queue_archive(self, symbol, tf, start, end, now):
        # Public daily archives cannot supply the unfinished UTC day.
        end = min(end,(now//86400000)*86400000-PERIODS[tf])
        key = (symbol,tf)
        if start <= end and key not in self.archive_queued and now >= self.archive_retry.get(key,0):
            self.archive_requests.append((symbol,tf,start,end))
            self.archive_queued.add(key)

    def _prime_archive(self, symbols, now):
        with self.lock:
            if now < self.next_archive_prime: return
            self.next_archive_prime = now+5*MINUTE
            tails = dict(self.history_tails)
            for s,tf,stamp in self.db.execute("SELECT symbol,tf,MAX(ms) FROM bars WHERE tf IN ('30m','4h') GROUP BY symbol,tf"):
                tails[(s,tf)] = max(tails.get((s,tf),0),stamp)
            for symbol in symbols:
                for tf in ('30m','4h'):
                    period = PERIODS[tf]
                    latest = ((now-CFG.publication_delay_ms)//period-1)*period
                    start = max(latest-(CFG.history_bars-2)*period,tails.get((symbol,tf),0)+period)
                    self._queue_archive(symbol,tf,start,latest,now)

    def _manager(self):
        from .io import market_eligible
        while not self.stop.wait(1):
            now = int(time.time()*1000)
            with self.lock:
                metadata = self.metadata
                wanted = sorted(m['symbol'] for m in metadata['symbols'] if market_eligible(m,now)) if metadata else []
                # Prepare completed days even while a known REST/connection
                # deadline prevents scanning. Current-day gaps remain explicit.
                self._prime_archive(wanted,now)
                new = [s for s in wanted if s not in self.symbol_groups]
                # Ticker/contract updates must survive a failed candle shard;
                # otherwise one busy socket forces the scanner back onto REST.
                if CONTROL_GROUP not in self.groups:
                    self.groups[CONTROL_GROUP] = dict(connected=False,acked=False,last_event=0)
                    self._spawn(self._connection,CONTROL_GROUP,[])
                # Append shards; never silently cap the universe or reconnect all
                # healthy streams because a newly listed contract was added.
                for offset in range(0,len(new),SYMBOLS_PER_CONNECTION):
                    group = 'market-'+str(len(self.groups)-1)
                    symbols = new[offset:offset+SYMBOLS_PER_CONNECTION]
                    self.symbol_groups.update({s:group for s in symbols})
                    self.groups[group] = dict(connected=False,acked=False,last_event=0)
                    self._spawn(self._connection,group,symbols)

    def _connection(self, group, symbols):
        from websockets.sync.client import connect
        channels = [s.lower()+'@kline_'+tf for s in symbols for tf in PERIODS]
        if group == CONTROL_GROUP: channels = ['!ticker@arr','!contractInfo']
        subscription = json.dumps({'method':'SUBSCRIBE','params':channels,'id':SUBSCRIPTION_ID})
        backoff = 60
        while not self.stop.is_set():
            # New connections honor the existing server ban; an already-open
            # market stream isn't a REST retry and can continue delivering bars.
            gate = self.gate.state()
            if gate['remaining_seconds']:
                self.stop.wait(min(15,gate['remaining_seconds'])); continue
            with self.connect_lock:
                delay = self.next_connect-time.monotonic()
                if delay > 0 and self.stop.wait(delay): return
                self.next_connect = time.monotonic()+2
            # Another shard or REST may have received a block while this
            # connection was waiting for its turn.
            if self.gate.state()['remaining_seconds']: continue
            began = time.monotonic()
            try:
                with connect(URL, proxy=None, open_timeout=10, close_timeout=2,
                             ping_interval=None, max_size=2_000_000, max_queue=64) as ws:
                    with self.lock: self.groups[group].update(connected=True,acked=False,last_event=0)
                    ws.send(subscription)
                    log('TA_STREAM_CONNECTED',group=group,symbols=len(symbols),streams=len(channels),
                        subscription_bytes=len(subscription.encode()),subscription_acked=False)
                    while not self.stop.is_set() and time.monotonic()-began < 23*3600+50*60:
                        try: raw = ws.recv(timeout=2)
                        except TimeoutError:
                            if time.monotonic()-began > 30:
                                with self.lock:
                                    if not self._fresh(group,int(time.time()*1000)): raise StreamMissing('STREAM_STALE')
                            continue
                        with self.lock: was_acked = self.groups[group]['acked']
                        self.ingest(group,json.loads(raw),int(time.time()*1000))
                        with self.lock: acked = self.groups[group]['acked']
                        if acked and not was_acked:
                            log('TA_STREAM_SUBSCRIBED',group=group,streams=len(channels))
                        # A stream that keeps sending events but never confirms
                        # our subscription must not run forever as unready.
                        if not acked and time.monotonic()-began > 30:
                            raise StreamMissing('STREAM_SUBSCRIPTION_ACK_TIMEOUT')
            except Exception as exc:
                # Never print response bodies/IP addresses. Keep handshake blocks
                # durable just like REST and obey explicit Retry-After timing.
                response = getattr(exc,'response',None)
                code = getattr(response,'status_code',None)
                if code in (418,429,403,451):
                    from .rate_limit import retry_deadline
                    until,source = retry_deadline(code,response.headers,
                        bytes(getattr(response,'body',b'')).decode(errors='replace'),int(time.time()*1000))
                    self.gate.block(until,'BINANCE_HTTP_'+str(code),source)
                # Close codes distinguish oversized frames, policy failures and
                # network loss without exposing exchange response text/IPs.
                received, sent = getattr(exc,'rcvd',None), getattr(exc,'sent',None)
                log('TA_STREAM_DISCONNECTED',group=group,
                    reason=str(exc) if isinstance(exc,StreamMissing) else type(exc).__name__,
                    http_status=code,close_received=getattr(received,'code',None),
                    close_sent=getattr(sent,'code',None),
                    subscription_acked=self.groups[group].get('acked',False),
                    streams=len(channels),uptime_seconds=round(time.monotonic()-began,1))
            finally:
                with self.lock: self.groups[group].update(connected=False,acked=False)
            backoff = 60 if time.monotonic()-began > 300 else min(backoff*2,900)
            if self.stop.wait(backoff): return

    def _archive_worker(self):
        archive = Archive(self.directory/'archive')
        while not self.stop.wait(1):
            with self.lock:
                if not self.archive_requests: continue
                symbol,tf,start,end = self.archive_requests.popleft()
            try:
                for day in range(start//86400000,end//86400000+1):
                    if self.stop.is_set(): return
                    # Files for the current UTC day do not exist yet.
                    if day >= int(time.time()*1000)//86400000: continue
                    rows = daily_rows(archive,symbol,tf,day)
                    if self.stop.is_set(): return
                    self.put_bars(symbol,tf,rows,'BINANCE_ARCHIVE',int(time.time()*1000))
                    self.stop.wait(.5)
            except Exception as exc:
                log('TA_STREAM_ARCHIVE_WAIT',symbol=symbol,tf=tf,reason=type(exc).__name__)
            finally:
                with self.lock:
                    self.archive_queued.discard((symbol,tf))
                    self.archive_retry[(symbol,tf)] = int(time.time()*1000)+30*MINUTE


def daily_rows(archive, symbol, tf, day):
    if (tf not in PERIODS or not symbol.endswith('USDT') or
            not all(c.isalnum() or c=='_' for c in symbol)):
        raise ValueError('ARCHIVE_KEY_INVALID')
    date = datetime.fromtimestamp(day*86400,timezone.utc).strftime('%Y-%m-%d')
    key = f'data/futures/um/daily/klines/{symbol}/{tf}/{symbol}-{tf}-{date}.zip'
    path = archive.cache/key; checksum = path.with_suffix('.sha256')
    if path.exists() and checksum.exists():
        raw = path.read_bytes(); expected = checksum.read_text().strip()
    else:
        from .archive import BASE
        url = BASE+quote(key,safe='/')
        expected = archive.request(url+'.CHECKSUM').decode().split()[0].lower()
        raw = archive.request(url)
    if hashlib.sha256(raw).hexdigest() != expected: raise ValueError('ARCHIVE_CHECKSUM_MISMATCH')
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix('.tmp'); temporary.write_bytes(raw); temporary.replace(path)
        checksum.write_text(expected)
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        if len(zipped.infolist()) != 1 or zipped.infolist()[0].file_size > 300_000_000:
            raise ValueError('ARCHIVE_CONTENT_INVALID')
        with zipped.open(zipped.namelist()[0]) as file:
            result = []
            for rawrow in csv.reader(io.TextIOWrapper(file,encoding='utf-8-sig')):
                if not rawrow or not rawrow[0].isdigit(): continue
                row = [int(rawrow[0]),*map(float,rawrow[1:6]),int(rawrow[6])]
                if len(rawrow)>=11:
                    row += [float(rawrow[7]),int(rawrow[8]),float(rawrow[9]),float(rawrow[10])]
                if row[0]//86400000 != day: raise ValueError('ARCHIVE_DATE_MISMATCH')
                result.append(row)
    return result
