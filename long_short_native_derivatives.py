"""V2 authoritative native Futures evidence shared by analyst and live gate.

One collector performs I/O. Readers fail closed on stale/unverified evidence.
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

def validate(payload,symbol,now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    if payload.get('source')!=SOURCE or payload.get('symbol')!=symbol or set(payload.get('endpoints',[]))!=ENDPOINTS:
        raise ValueError('native derivative provenance mismatch')
    if not fresh(payload.get('observed_ms'),now_ms,180000):raise ValueError('native snapshot stale')
    m=payload.get('market') or {}
    if any(m.get(k)!=v for k,v in {'symbol':symbol,'status':'TRADING','contractType':'PERPETUAL',
                                  'quoteAsset':'USDT','marginAsset':'USDT'}.items()):
        raise ValueError('native contract mismatch')
    current=payload.get('oi') or {};mark=payload.get('mark') or {}
    if current.get('symbol')!=symbol or mark.get('symbol')!=symbol:raise ValueError('native symbol mismatch')
    if not fresh(current.get('time'),now_ms,180000) or not fresh(mark.get('time'),now_ms,180000):
        raise ValueError('native current fields stale')
    history=payload.get('oi_history')
    if not isinstance(history,list) or len(history)<13:raise ValueError('native OI history missing')
    history=sorted(history,key=lambda r:number(r.get('timestamp')))[-13:]
    for i,r in enumerate(history):
        if r.get('symbol')!=symbol:raise ValueError('OI history symbol mismatch')
        number(r.get('sumOpenInterest'),True)
        if i and number(r['timestamp'])-number(history[i-1]['timestamp'])!=300000:
            raise ValueError('OI history gap')
    if not fresh(history[-1]['timestamp'],now_ms,660000):raise ValueError('OI history stale')
    ls=payload.get('long_short')
    if not isinstance(ls,list) or not ls:raise ValueError('native long/short missing')
    ls=max(ls,key=lambda r:number(r.get('timestamp')))
    if ls.get('symbol')!=symbol or not fresh(ls.get('timestamp'),now_ms,660000):
        raise ValueError('long/short stale or wrong symbol')
    return {'version':VERSION,'source':SOURCE,'symbol':symbol,'market_verified':True,
        'observed_ms':int(payload['observed_ms']),'oi_unit':'BASE_ASSET',
        'oi_now':number(current.get('openInterest'),True),'oi_fresh':True,
        'oi_change_1h':(number(history[-1]['sumOpenInterest'],True)/number(history[0]['sumOpenInterest'],True)-1)*100,
        'long_short_ratio':number(ls.get('longShortRatio'),True),'ls_fresh':True,
        'long_short_definition':'BINANCE_GLOBAL_ACCOUNT_RATIO',
        'funding_pct':number(mark.get('lastFundingRate'))*100,'funding_fresh':True,
        'next_funding_ms':int(number(mark.get('nextFundingTime'),True)),
        'mark_price':number(mark.get('markPrice'),True)}

def database():
    os.makedirs(root(),exist_ok=True)
    c=sqlite3.connect(os.path.join(root(),'native_derivatives_v2.db'),timeout=10)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS snapshots(symbol TEXT PRIMARY KEY, received_ms INTEGER, payload TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS issues(time_ms INTEGER,symbol TEXT,error TEXT)')
    return c

def refresh(symbol):
    payload=relay_get('/derivatives',{'symbol':symbol})
    result=validate(payload,symbol)
    c=database()
    try:
        with c:c.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?)',(symbol,int(time.time()*1000),json.dumps(payload)))
    finally:c.close()
    return result

def snapshot(symbol):
    try:
        c=database()
        try:row=c.execute('SELECT payload FROM snapshots WHERE symbol=?',(symbol,)).fetchone()
        finally:c.close()
        if not row:raise ValueError('native snapshot unavailable')
        return validate(json.loads(row[0]),symbol)
    except (ValueError,TypeError,KeyError,sqlite3.Error) as e:
        return {'version':VERSION,'source':SOURCE,'symbol':symbol,'oi_fresh':False,'ls_fresh':False,
            'funding_fresh':False,'error':str(e),'oi_change_1h':None,'long_short_ratio':None,'funding_pct':None}

def evidence(symbol):
    result=snapshot(symbol)
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

def main():
    from long_short_futures_kline_shadow import requested_symbols,log
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        symbols=requested_symbols(os.path.join(root(),'long_short_analyst.db'))[:90]
        ready=0
        for symbol in symbols:
            if stop.is_set():return 0
            try:refresh(symbol);ready+=1
            except Exception as e:
                c=database()
                try:
                    with c:c.execute('INSERT INTO issues VALUES(?,?,?)',(int(time.time()*1000),symbol,str(e)[:200]))
                finally:c.close()
                log('NATIVE_DERIVATIVE_DATA_ISSUE',{'symbol':symbol,'error':str(e)[:160]})
                if any(x in str(e) for x in ('418','429','451','COOLDOWN')):break
            stop.wait(0.1)
        log('NATIVE_DERIVATIVES_COVERAGE',{'version':VERSION,'requested':len(symbols),'updated':ready})
        stop.wait(30)
    return 0

if __name__=='__main__':raise SystemExit(main())
