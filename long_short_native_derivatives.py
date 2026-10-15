"""V2 authoritative native Futures evidence shared by analyst and live gate.

Deadline collector and bounded demand refresh share per-symbol leases.
Readers fail closed on stale/unverified evidence after recovery is attempted.
No orders. Global account ratio is explicitly distinguished from vendor series.
"""
import json
import math
import os
import signal
import sqlite3
import threading
import time
from urllib.parse import urlencode,urlsplit
from urllib.request import urlopen

SOURCE='BINANCE_UM_FUTURES_REST'
VERSION='LS_NATIVE_CHAIN_V2_2026-10-13'
REFRESH_VERSION='NATIVE_REFRESH_DEADLINE_V3_2026_10_15'
REFRESH_AFTER_MS=60000
CYCLE_BUDGET_SECONDS=45
ENDPOINTS={'/fapi/v1/exchangeInfo','/fapi/v1/openInterest','/futures/data/openInterestHist',
           '/futures/data/globalLongShortAccountRatio','/fapi/v1/premiumIndex'}

def enabled():
    return os.getenv('LS_NATIVE_CHAIN_V2','0')=='1'

def root():
    return os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state'

def relay_get(path,params):
    base=os.getenv('LS_FUTURES_HISTORY_RELAY_URL','').rstrip('/')
    u=urlsplit(base)
    if u.scheme!='http' or not (u.hostname or '').endswith('.railway.internal') or u.username or u.password or u.path or u.query:
        raise ValueError('unverified private relay')
    with urlopen(base+path+'?'+urlencode(params),timeout=25) as response:
        return json.loads(response.read(1000000))

def number(value,positive=False):
    if value is None or isinstance(value,bool):raise ValueError('missing numeric field')
    v=float(value)
    if not math.isfinite(v) or (positive and v<=0):raise ValueError('invalid numeric field')
    return v

def fresh(stamp,now_ms,age_ms):
    delta=now_ms-number(stamp)
    return -2000<=delta<=age_ms

def validate(payload,symbol,now_ms=None,allow_partial=False):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    if payload.get('source')!=SOURCE or payload.get('symbol')!=symbol or set(payload.get('endpoints',[]))!=ENDPOINTS:
        raise ValueError('native derivative provenance mismatch')
    if not fresh(payload.get('observed_ms'),now_ms,180000):raise ValueError('native snapshot stale')
    m=payload.get('market') or {}
    if any(m.get(k)!=v for k,v in {'symbol':symbol,'status':'TRADING','contractType':'PERPETUAL',
                                  'quoteAsset':'USDT','marginAsset':'USDT'}.items()):
        raise ValueError('native contract mismatch')
    result={'version':VERSION,'source':SOURCE,'symbol':symbol,'market_verified':True,
        'observed_ms':int(payload['observed_ms']),'oi_unit':'BASE_ASSET',
        'oi_fresh':False,'ls_fresh':False,'funding_fresh':False,
        'oi_change_1h':None,'oi_now':None,'long_short_ratio':None,'funding_pct':None,
        'long_short_definition':'BINANCE_GLOBAL_ACCOUNT_RATIO','field_errors':{}}
    def oi_fields():
        current=payload.get('oi') or {}
        if current.get('symbol')!=symbol:raise ValueError('native OI symbol mismatch')
        if not fresh(current.get('time'),now_ms,180000):raise ValueError('native OI stale')
        history=payload.get('oi_history')
        if not isinstance(history,list) or len(history)<13:raise ValueError('native OI history missing')
        history=sorted(history,key=lambda r:number(r.get('timestamp')))[-13:]
        for i,r in enumerate(history):
            if r.get('symbol')!=symbol:raise ValueError('OI history symbol mismatch')
            number(r.get('sumOpenInterest'),True)
            if i and number(r['timestamp'])-number(history[i-1]['timestamp'])!=300000:raise ValueError('OI history gap')
        if not fresh(history[-1]['timestamp'],now_ms,660000):raise ValueError('OI history stale')
        return dict(oi_now=number(current.get('openInterest'),True),oi_fresh=True,
            oi_change_1h=(number(history[-1]['sumOpenInterest'],True)/number(history[0]['sumOpenInterest'],True)-1)*100)
    def ls_fields():
        ls=payload.get('long_short')
        if not isinstance(ls,list) or not ls:raise ValueError('native long/short missing')
        ls=max(ls,key=lambda r:number(r.get('timestamp')))
        if ls.get('symbol')!=symbol or not fresh(ls.get('timestamp'),now_ms,660000):raise ValueError('long/short stale or wrong symbol')
        return dict(long_short_ratio=number(ls.get('longShortRatio'),True),ls_fresh=True)
    def funding_fields():
        mark=payload.get('mark') or {}
        if mark.get('symbol')!=symbol:raise ValueError('native mark symbol mismatch')
        if not fresh(mark.get('time'),now_ms,180000):raise ValueError('native mark stale')
        return dict(funding_pct=number(mark.get('lastFundingRate'))*100,funding_fresh=True,
            next_funding_ms=int(number(mark.get('nextFundingTime'),True)),mark_price=number(mark.get('markPrice'),True))
    for field,reader in [('oi_change_1h',oi_fields),('long_short_ratio',ls_fields),('funding_pct',funding_fields)]:
        try:result.update(reader())
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            if not allow_partial:raise
            result['field_errors'][field]=str(exc)
    if result['field_errors']:result['error']='; '.join(result['field_errors'].values())
    return result

def database():
    os.makedirs(root(),exist_ok=True)
    c=sqlite3.connect(os.path.join(root(),'native_derivatives_v2.db'),timeout=10)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS snapshots(symbol TEXT PRIMARY KEY, received_ms INTEGER, payload TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS issues(time_ms INTEGER,symbol TEXT,error TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS refresh_leases(symbol TEXT PRIMARY KEY, until_ms INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS markets(id INTEGER PRIMARY KEY,observed_ms INTEGER,payload TEXT)')
    return c

def refresh(symbol):
    # Across analyst/live/collector processes, only one request per symbol.
    now=int(time.time()*1000)
    c=database()
    try:
        with c:
            claimed=c.execute('''INSERT INTO refresh_leases VALUES(?,?)
                ON CONFLICT(symbol) DO UPDATE SET until_ms=excluded.until_ms
                WHERE refresh_leases.until_ms<=?''',(symbol,now+30000,now)).rowcount
    finally:c.close()
    if not claimed:
        existing=snapshot(symbol)
        if not existing.get('error'):return existing
        raise ValueError('NATIVE_REFRESH_PENDING')
    # An error keeps the short lease as retry backoff; it never stamps old data fresh.
    payload=relay_get('/derivatives',{'symbol':symbol})
    result=validate(payload,symbol,allow_partial=True)
    c=database()
    try:
        with c:
            c.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?)',(symbol,int(time.time()*1000),json.dumps(payload)))
            c.execute('DELETE FROM refresh_leases WHERE symbol=?',(symbol,))
    finally:c.close()
    return result

def snapshot(symbol):
    try:
        c=database()
        try:row=c.execute('SELECT payload FROM snapshots WHERE symbol=?',(symbol,)).fetchone()
        finally:c.close()
        if not row:raise ValueError('native snapshot unavailable')
        return validate(json.loads(row[0]),symbol,allow_partial=True)
    except (ValueError,TypeError,KeyError,sqlite3.Error) as e:
        return {'version':VERSION,'source':SOURCE,'symbol':symbol,'oi_fresh':False,'ls_fresh':False,
            'funding_fresh':False,'error':str(e),'oi_change_1h':None,'long_short_ratio':None,'funding_pct':None}

def evidence(symbol, refresh_stale=False):
    result=snapshot(symbol)
    if refresh_stale and result.get('error'):
        before=result['error']
        try:
            result=refresh(symbol)
            event='NATIVE_DATA_PARTIAL' if result.get('error') else 'NATIVE_DATA_RECOVERED'
            print(event+' '+json.dumps({'symbol':symbol,'previous_error':before,'field_errors':result.get('field_errors',{}),'version':REFRESH_VERSION}),flush=True)
        except Exception as exc:
            result=snapshot(symbol)
            result['refresh_error']=str(exc)[:160]
            print('NATIVE_DATA_DEFERRED '+json.dumps({'symbol':symbol,'reason':before,'refresh_error':result['refresh_error']}),flush=True)
    from long_short_taker_continuous_shadow import window,window_from_verified_futures_klines
    try:taker=window(os.path.join(root(),'long_short_taker_minute_shadow.db'),symbol)
    except (sqlite3.Error,OSError):taker={'ready':False}
    if not taker.get('ready'):
        taker=window_from_verified_futures_klines(os.path.join(root(),'binance_futures_kline_shadow.db'),symbol)
    result.update(taker_ratio=taker.get('taker_ratio'),taker_fresh=bool(taker.get('ready')),
                  taker_coverage_ready=bool(taker.get('ready')),taker_source=taker.get('source'))
    from long_short_binance_derivative_gate import assess
    gate=assess(result,funding_pct=result.get('funding_pct'),funding_source=result['source'],
                taker_ratio=result.get('taker_ratio'),taker_source=result.get('taker_source'))
    result.update(ready=gate['ready'],missing=gate['missing'])
    return result

def refresh_order(requested, urgent, observed, now_ms, retry_after=None, limit=90):
    """Order the whole demand set BEFORE the cap; never relax validation ages."""
    retry_after=retry_after or {}
    unique=list(dict.fromkeys(urgent+requested))
    rank={s:i for i,s in enumerate(urgent)}
    due=[s for s in unique if now_ms-observed.get(s,0)>=REFRESH_AFTER_MS
         and retry_after.get(s,0)<=now_ms]
    due.sort(key=lambda s:(s not in rank,observed.get(s,0),rank.get(s,len(rank)),s))
    return due[:limit]


def demand(analyst):
    from long_short_futures_kline_shadow import requested_symbols,live_watch_symbols
    from long_short_analyst import native_futures_websocket_universe
    current=[r[0] for r in native_futures_websocket_universe(quiet=True)]
    upcoming=[r[0] for r in native_futures_websocket_universe(slot_offsets=(1,),quiet=True)]
    live=live_watch_symbols(analyst)
    from long_short_chart_first import enabled as chart_first_enabled
    chart=[]
    if chart_first_enabled():
        from long_short_chart_first_worker import active_symbols
        chart=active_symbols()
    return list(dict.fromkeys(chart+current+upcoming+live+requested_symbols(analyst))),list(dict.fromkeys(chart+live+current))


def main():
    from long_short_futures_kline_shadow import log
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    retry_after={}
    while not stop.is_set():
        began=time.monotonic()
        try:
            p=relay_get('/markets',{})
            if p.get('source')!=SOURCE or not isinstance(p.get('markets'),list):raise ValueError('invalid market catalogue')
            allowed={m['symbol'] for m in p['markets'] if m.get('status')=='TRADING' and m.get('contractType')=='PERPETUAL'
                and m.get('quoteAsset')=='USDT' and m.get('marginAsset')=='USDT'}
            c=database()
            try:
                with c:c.execute('INSERT OR REPLACE INTO markets VALUES(1,?,?)',(int(time.time()*1000),json.dumps(sorted(allowed))))
                # Provider timestamp, not the timestamp of a cached DB write.
                observed=dict(c.execute("SELECT symbol,json_extract(payload,'$.observed_ms') FROM snapshots"))
            finally:c.close()
            analyst=os.getenv('LS_ANALYST_DB') or os.getenv('LS_DB') or os.path.join(root(),'long_short_analyst.db')
            requested,urgent=demand(analyst)
            requested=[s for s in requested if s in allowed];urgent=[s for s in urgent if s in allowed]
        except Exception as e:
            log('NATIVE_REFRESH_PLAN_UNAVAILABLE',{'error':str(e)[:160]});stop.wait(5);continue
        now=int(time.time()*1000)
        observed={s:int(t or 0) for s,t in observed.items()}
        symbols=refresh_order(requested,urgent,observed,now,retry_after)
        known=[now-observed[s] for s in requested if observed.get(s)]
        log('NATIVE_DERIVATIVES_REFRESH_PLAN',{'version':REFRESH_VERSION,'requested':len(requested),
            'urgent':len(urgent),'due':len(symbols),'missing':sum(not observed.get(s) for s in requested),
            'oldest_known_age_seconds':round(max(known,default=0)/1000),'first_symbols':symbols[:5]})
        ready=0;cooldown=False
        for symbol in symbols:
            if stop.is_set():return 0
            if time.monotonic()-began>=CYCLE_BUDGET_SECONDS:break
            try:
                result=refresh(symbol);observed[symbol]=result['observed_ms'];ready+=1
                retry_after.pop(symbol,None)
            except Exception as e:
                retry_after[symbol]=int(time.time()*1000)+30000
                c=database()
                try:
                    with c:c.execute('INSERT INTO issues VALUES(?,?,?)',(int(time.time()*1000),symbol,str(e)[:200]))
                finally:c.close()
                log('NATIVE_DERIVATIVE_DATA_ISSUE',{'symbol':symbol,'error':str(e)[:160]})
                if any(x in str(e) for x in ('418','429','451','COOLDOWN')):cooldown=True;break
            stop.wait(0.1)
        now=int(time.time()*1000)
        log('NATIVE_DERIVATIVES_COVERAGE',{'version':REFRESH_VERSION,'requested':len(requested),'updated':ready,
            'urgent':len(urgent),'urgent_snapshot_fresh':sum(0<=now-observed.get(s,0)<=180000 for s in urgent),
            'elapsed_seconds':round(time.monotonic()-began,2),'validation_age_ms':180000})
        stop.wait(30 if cooldown else 2 if symbols else 5)
    return 0

if __name__=='__main__':raise SystemExit(main())
