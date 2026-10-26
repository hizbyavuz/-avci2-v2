"""Independent, venue-labelled Bybit WATCH observations. Never creates a trade.

The Binance worker and its cooldown are not inputs to this request gate. The
last saved Binance contract list is only a symbol reference, never live proof
that a Binance order could be placed. All prices/volumes here come from Bybit.
"""
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from zoneinfo import ZoneInfo

from .core import CFG, MINUTE, assess, closed_bars, finite
from .io import DataUnavailable, NoRedirect, Store, encode, market_eligible, tradable
from .rate_limit import CooldownError, RequestGate
from .watch import publish_watches

REVISION = 'EXTERNAL_BYBIT_WATCH_20261026_1'
PERIODS = {'30m': CFG.entry_ms, '4h': CFG.context_ms}
HOST = 'https://api.bybit.com'
ALLOWED = frozenset(('/v5/market/time', '/v5/market/instruments-info',
                     '/v5/market/tickers', '/v5/market/kline', '/v5/market/open-interest',
                     '/v5/market/funding/history', '/v5/market/orderbook', '/v5/market/mark-price-kline'))


def log(kind, **fields):
    print(kind+' '+encode(dict(source_venue='BYBIT',revision=REVISION,**fields)),flush=True)


def contract(raw, reference, now):
    """Exact symbol/base matching; no heuristic 1000-token or name conversion."""
    symbol=raw.get('symbol',''); old=reference.get(symbol)
    if not old or old.get('baseAsset')!=raw.get('baseCoin') or not market_eligible(old,now):
        return None
    if (raw.get('status')!='Trading' or raw.get('contractType')!='LinearPerpetual' or
            raw.get('quoteCoin')!='USDT' or raw.get('settleCoin')!='USDT' or
            raw.get('isPreListing') is not False or raw.get('marketRegion') or raw.get('underlyingTicker') or
            'ST' in raw.get('tags',[]) or raw.get('symbolType','').lower() in ('xstocks','tradfi')):
        return None
    market=dict(symbol=symbol,baseAsset=raw['baseCoin'],quoteAsset='USDT',marginAsset='USDT',
                underlyingType='COIN',underlyingSubType=[],status='TRADING',contractType='PERPETUAL',
                onboardDate=int(raw['launchTime']),source_venue='BYBIT',
                funding_interval_minutes=raw.get('fundingInterval'),
                bybit_price_filter=raw.get('priceFilter'),bybit_lot_filter=raw.get('lotSizeFilter'))
    return market if market_eligible(market,now) else None


class BybitPublic:
    def __init__(self, store, stop):
        self.store=store;self.stop=stop
        self.gate=RequestGate(Path(store_path(store)).parent/'bybit_cooldown.json')
        self.offset_ms=0;self.requests=0;self.cache_hits=0

    def now(self):return int(time.time()*1000)+self.offset_ms

    def _block(self, code, headers=None):
        headers=headers or {}; now=int(time.time()*1000)
        delay=600 if code==403 else 86400 if code in (418,451) else 60
        until=now+delay*1000; source='CONSERVATIVE_FALLBACK'
        value=headers.get('Retry-After')
        if value:
            try: retry=now+int(finite(value)*1000)
            except ValueError:
                try:retry=int(parsedate_to_datetime(value).timestamp()*1000)
                except (ValueError,TypeError,OverflowError):retry=0
            if retry>now:until=max(until,retry+2000);source='EXCHANGE_RETRY_TIME'
        if code==10006:
            try:until=max(until,int(headers.get('X-Bapi-Limit-Reset-Timestamp',0))+2000)
            except (ValueError,TypeError):pass
        self.gate.block(until,'BYBIT_'+str(code),source)

    def request(self, path, **params):
        if path not in ALLOWED:raise ValueError('NON_PUBLIC_ENDPOINT_REFUSED')
        if self.stop.is_set():raise DataUnavailable('WORKER_STOPPING')
        try:self.gate.acquire(3)  # <=3.34 requests/sec, separate from Binance.
        except CooldownError:raise DataUnavailable('BYBIT_COOLDOWN') from None
        url=HOST+path+'?'+urllib.parse.urlencode(params)
        req=urllib.request.Request(url,headers={'User-Agent':'TA-Paper-External-Watch/1'},method='GET')
        begin=int(time.time()*1000);self.requests+=1
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=8) as response:
                raw=response.read(8_000_001);headers=response.headers
                if len(raw)>8_000_000:raise ValueError('RESPONSE_TOO_LARGE')
                payload=json.loads(raw)
            end=int(time.time()*1000)
            if not isinstance(payload,dict) or type(payload.get('retCode')) is not int:
                raise ValueError('RESPONSE_INVALID')
            code=payload['retCode']
            if code:
                self._block(code,headers)
                raise DataUnavailable('BYBIT_RETCODE_'+str(code))
            if type(payload.get('time')) is not int:raise ValueError('RESPONSE_TIME_MISSING')
            offset=payload['time']-(begin+end)//2
            if end-begin>3000 or abs(offset)>5000:raise ValueError('CLOCK_OR_LATENCY_INVALID')
            self.offset_ms=offset
            self.store.snapshot('BYBIT_RAW:'+path,dict(params=params,response=payload),self.now())
            return payload
        except urllib.error.HTTPError as exc:
            self._block(exc.code,exc.headers)
            raise DataUnavailable('BYBIT_HTTP_'+str(exc.code)) from None
        except DataUnavailable:
            if not self.gate.state()['remaining_seconds']:self._block('RESPONSE_UNAVAILABLE')
            raise
        except (OSError,ValueError,TypeError) as exc:
            self._block(type(exc).__name__)
            raise DataUnavailable('BYBIT_'+(str(exc) if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError)
                                           else type(exc).__name__)) from None

    def universe(self, reference):
        records=[];cursor=None;seen=set()
        while True:
            params=dict(category='linear',status='Trading',limit=1000)
            if cursor:params['cursor']=cursor
            payload=self.request('/v5/market/instruments-info',**params)
            result=payload['result']
            if result.get('category')!='linear':raise DataUnavailable('BYBIT_WRONG_CATEGORY')
            records.extend(result['list']);cursor=result.get('nextPageCursor')
            if not cursor:break
            if cursor in seen or len(seen)>=20:raise DataUnavailable('BYBIT_PAGINATION_INVALID')
            seen.add(cursor)
        tick_payload=self.request('/v5/market/tickers',category='linear')
        if tick_payload['result'].get('category')!='linear':raise DataUnavailable('BYBIT_WRONG_CATEGORY')
        stamp=tick_payload['time'];now=self.now()
        tickers={r['symbol']:dict(symbol=r['symbol'],quoteVolume=finite(r['turnover24h']),closeTime=stamp)
                 for r in tick_payload['result']['list']}
        markets={};seen_symbols=set()
        for raw in records:
            if raw['symbol'] in seen_symbols:raise DataUnavailable('BYBIT_DUPLICATE_SYMBOL')
            seen_symbols.add(raw['symbol'])
            market=contract(raw,reference,now)
            if market and tradable(market,tickers.get(market['symbol']),now):markets[market['symbol']]=market
        if not markets:raise DataUnavailable('BYBIT_NO_VERIFIED_COMMON_UNIVERSE')
        return markets,{s:tickers[s] for s in markets},dict(bybit_contracts=len(records),binance_reference=len(reference))

    def candles(self, symbol, tf, limit=1000, **params):
        if tf=='1m':
            payload=self.request('/v5/market/kline',category='linear',symbol=symbol,interval='1',
                                 start=params['startTime'],end=params['endTime'],limit=limit)
            result=payload['result']
            if result.get('category')!='linear' or result.get('symbol')!=symbol:
                raise DataUnavailable('BYBIT_CANDLE_MARKET_MISMATCH')
            rows=[[int(r[0]),*r[1:6],int(r[0])+MINUTE-1,r[6]] for r in reversed(result['list'])]
            from .features import capture_klines
            capture_klines(self.store.db,symbol,tf,rows,self.now(),'BYBIT_REST')
            return rows
        period=PERIODS[tf];now=self.now()
        latest=((now-CFG.publication_delay_ms)//period-1)*period
        old=self.store.recent_candles(symbol,tf,CFG.history_bars-1)
        if old and old[-1][0]==latest:
            closed_bars(old,period,now,minimum=CFG.history_bars-1)
            self.cache_hits+=1;return old
        count=CFG.history_bars-1
        if old and 0<(latest-old[-1][0])//period<count:
            count=(latest-old[-1][0])//period+1  # Include one immutable overlap.
        payload=self.request('/v5/market/kline',category='linear',symbol=symbol,
                             interval=str(period//MINUTE),end=latest+period-1,limit=count)
        result=payload['result']
        if result.get('category')!='linear' or result.get('symbol')!=symbol:
            raise DataUnavailable('BYBIT_CANDLE_MARKET_MISMATCH')
        rows=[]
        for raw in reversed(result['list']):
            if len(raw)!=7:raise DataUnavailable('BYBIT_CANDLE_FORMAT')
            start=int(raw[0])
            # Bybit field 6 is quote turnover, NOT a closing timestamp.
            rows.append([start,*raw[1:6],start+period-1,raw[6]])
        verified=closed_bars(rows,period,self.now(),minimum=count)
        if len(verified)!=count:raise DataUnavailable('BYBIT_INCOMPLETE_CANDLES')
        self.store.candles(symbol,tf,verified)  # Refuses immutable overlaps that changed.
        from .features import capture_klines
        capture_klines(self.store.db,symbol,tf,rows,self.now(),'BYBIT_REST')
        combined=self.store.recent_candles(symbol,tf,CFG.history_bars-1)
        return closed_bars(combined,period,self.now(),minimum=CFG.history_bars-1)


def store_path(store):
    return store.db.execute('PRAGMA database_list').fetchone()[2]


def reference_universe(path):
    with sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True,timeout=10) as db:
        row=db.execute("SELECT ms,payload FROM snapshots WHERE kind='UNIVERSE' ORDER BY id DESC LIMIT 1").fetchone()
    if not row:raise DataUnavailable('BINANCE_REFERENCE_UNIVERSE_MISSING')
    metadata=json.loads(zlib.decompress(row[1]))['metadata']
    return {m['symbol']:m for m in metadata['symbols']},metadata.get('_ta_metadata_ms',row[0])


class WatchScanner:
    def __init__(self, store, client, reference_path, stop):
        self.store=store;self.client=client;self.reference_path=reference_path;self.stop=stop
        self.state=json.loads(store.setting('bybit_watch_scan_v1') or '{}')

    def save(self):self.store.setting('bybit_watch_scan_v1',encode(self.state))

    def prepare(self, slot):
        reference,stamp=reference_universe(self.reference_path)
        markets,tickers,coverage=self.client.universe(reference)
        self.state=dict(slot=slot,deadline_ms=slot*CFG.entry_ms+CFG.max_scan_delay_ms-1,
                        markets=markets,tickers=tickers,done={},retry_at={},handled={},errors={},
                        status='PENDING',reference_ms=stamp,coverage=coverage,warm_cursor=0)
        self.store.snapshot('BYBIT_UNIVERSE',self.state,self.client.now());self.save()
        log('TA_EXTERNAL_UNIVERSE',common_eligible=len(markets),reference_ms=stamp,**coverage)

    def step(self):
        if self.client.gate.state()['remaining_seconds']:
            raise DataUnavailable('BYBIT_COOLDOWN')
        now=self.client.now();slot=(now-CFG.publication_delay_ms)//CFG.entry_ms
        if self.state.get('slot')!=slot:self.prepare(slot)
        state=self.state;began=time.monotonic()
        if now>state['deadline_ms']:
            state['status']='EXPIRED' if len(state['done'])<len(state['markets']) else 'COMPLETE'
            self.report();self.warm();return
        if state['status']=='COMPLETE':return
        for symbol in sorted(state['markets'],key=lambda s:(-state['tickers'][s]['quoteVolume'],s)):
            if symbol in state['done'] or now<state['retry_at'].get(symbol,0):continue
            if self.stop.is_set() or self.client.now()>state['deadline_ms'] or time.monotonic()-began>10:break
            try:
                bars=self.client.candles(symbol,'30m');context=self.client.candles(symbol,'4h')
                stamp=self.client.now()
                if stamp>state['deadline_ms']:break
                p=assess(symbol,bars,context,stamp)
                p.update(source_venue='BYBIT',source_market='USDT_LINEAR_PERPETUAL',
                         watch_revision=REVISION,binance_entry_verified=False,
                         binance_reference_ms=state['reference_ms'])
                self.store.assessment(p);state['done'][symbol]=p
                from .features import record_context
                record_context(self.store,p,bars,context,'BYBIT')
                if p['status']=='SETUP':
                    state['handled'][symbol]='EXTERNAL_VENUE_WATCH_ONLY'
                    publish_watches(self.store,self.client,state)
                    from .shadow import enqueue
                    enqueue(self.store,p,state['markets'][symbol])
                state['retry_at'].pop(symbol,None)
            except (DataUnavailable,ValueError,KeyError,TypeError) as exc:
                reason=str(exc)[:100] if isinstance(exc,(DataUnavailable,ValueError)) else type(exc).__name__
                state['errors'][reason]=state['errors'].get(reason,0)+1
                state['retry_at'][symbol]=self.client.now()+MINUTE
                self.store.event('EXTERNAL_DATA_ISSUE',symbol,dict(reason=reason,source_venue='BYBIT'),self.client.now())
                if self.client.gate.state()['remaining_seconds']:break
            self.save();now=self.client.now()
        if len(state['done'])==len(state['markets']):state['status']='COMPLETE'
        elif self.client.now()>state['deadline_ms']:state['status']='EXPIRED'
        self.save()
        if state['status']!='PENDING':self.report()

    def warm(self):
        state=self.state;now=self.client.now();next_close=(state['slot']+1)*CFG.entry_ms
        if next_close-now<20_000 or self.client.gate.state()['remaining_seconds']:return
        symbols=sorted(state['markets'],key=lambda s:(-state['tickers'][s]['quoteVolume'],s))
        began=time.monotonic();cursor=state['warm_cursor']
        while cursor<len(symbols) and time.monotonic()-began<10 and not self.stop.is_set():
            if next_close-self.client.now()<20_000:break
            try:
                self.client.candles(symbols[cursor],'4h');self.client.candles(symbols[cursor],'30m')
            except (DataUnavailable,ValueError,KeyError,TypeError):
                if self.client.gate.state()['remaining_seconds']:break
            cursor+=1
        state['warm_cursor']=cursor;self.save()

    def report(self):
        state=self.state
        if state.get('reported'):return
        counts=Counter(p['reason'] for p in state['done'].values())
        setups=sum(p['status']=='SETUP' for p in state['done'].values())
        summary=dict(status=state['status'],assessed=len(state['done']),universe=len(state['markets']),
                     candidates=setups,reasons=dict(counts),errors=state['errors'],slot=state['slot'])
        self.store.event('EXTERNAL_SCAN',str(state['slot']),summary,self.client.now());log('TA_EXTERNAL_SCAN',**summary)
        next_time=datetime.fromtimestamp((state['slot']+1)*CFG.entry_ms/1000,timezone.utc).astimezone(
            ZoneInfo('Europe/Istanbul')).strftime('%d.%m %H:%M')
        text=('⚪ BYBIT · İzleme taraması\n'
              f"Değerlendirilen: {summary['assessed']}/{summary['universe']} ortak coin.\n"
              f"Teknik aday: {setups}.\n"+
              ('Eksik veri/tarama süresi nedeniyle kapsam tamamlanmadı.\n'
               f'Geçmiş veri hazırlanıyor; sonraki kapanış: {next_time} TSİ.\n' if state['status']!='COMPLETE' else '')+
              'Kaynak: Bybit USDT vadeli. Binance girişi doğrulanmadı.\n'
              'Binance eşlemesi son kayıtlı listeye dayanır. Gerçek emir yok.')
        self.store.queue('system:bybit:scan:'+str(state['slot']),text,self.client.now())
        state['reported']=True;self.save()


class ExternalWatch:
    """Own collector thread/database; main runner delivers its durable outbox."""
    def __init__(self, directory, reference_path):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'research.sqlite3';self.reference_path=reference_path
        self.delivery=Store(self.path);self.stop=threading.Event();self.offset_ms=0
        self.thread=threading.Thread(target=self._run,name='bybit-watch',daemon=True)

    def start(self):self.thread.start();return self
    def now(self):return int(time.time()*1000)+self.offset_ms
    def close(self):
        self.stop.set();self.thread.join(timeout=12);self.delivery.db.close()

    def _run(self):
        store=Store(self.path);client=BybitPublic(store,self.stop)
        scanner=WatchScanner(store,client,self.reference_path,self.stop)
        heartbeat=0;last_error=None
        log('TA_EXTERNAL_BOOT',automatic_orders=False,config_hash=CFG.fingerprint,provenance=store.provenance)
        try:
            while not self.stop.is_set():
                try:
                    scanner.step();last_error=None
                    if scanner.state.get('status') in ('COMPLETE','EXPIRED'):
                        from .shadow import tick as shadow_tick
                        from .features import tick as feature_tick
                        from .observations import collect
                        next_close=(client.now()//CFG.entry_ms+1)*CFG.entry_ms
                        if next_close-client.now()>30_000 and not client.gate.state()['remaining_seconds']:
                            shadow_tick(store,client,next_close-30_000)
                            collect(store,client,max_requests=2,stop_before_ms=next_close-30_000)
                            feature_tick(store,client,'BYBIT',next_close-30_000)
                except (DataUnavailable,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
                    reason=str(exc)[:100] if isinstance(exc,(DataUnavailable,ValueError)) else type(exc).__name__
                    if reason!=last_error:
                        log('TA_EXTERNAL_WAIT',reason=reason,cooldown=client.gate.state())
                        store.event('EXTERNAL_WAIT',reason,dict(source_venue='BYBIT',cooldown=client.gate.state()),client.now())
                        last_error=reason
                    self.stop.wait(15)
                self.offset_ms=client.offset_ms
                if client.now()-heartbeat>=5*MINUTE:
                    from .shadow import health
                    log('TA_EXTERNAL_HEARTBEAT',requests=client.requests,cache_hits=client.cache_hits,
                        status=scanner.state.get('status','PREPARING'),
                        assessed=len(scanner.state.get('done',{})),universe=len(scanner.state.get('markets',{})),
                        cooldown=client.gate.state(),observations=health(store))
                    heartbeat=client.now()
                self.stop.wait(1 if scanner.state.get('status')=='PENDING' else 5)
        finally:store.db.close()
