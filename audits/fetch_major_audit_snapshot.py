"""One-shot public Binance Futures research snapshot. No orders or production writes.

Raw responses are kept with request times; a common CLOSED-candle cutoff is
applied in analysis. A later download is not evidence of availability at cutoff.
"""
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(os.environ.get('MAJOR_AUDIT_DATA_DIR', str(pathlib.Path(__file__).resolve().parent/'major_move_20261016'/'data')))
OUT = ROOT / 'market'
BASE = 'https://www.binance.com/fapi/v1/'
HALT = threading.Event()
LOCK = threading.Lock()
NEXT = 0.


def fetch(job):
    global NEXT
    symbol, interval = job
    target = OUT / (symbol + '_' + interval + '.json')
    if target.exists():
        return {'symbol':symbol, 'interval':interval, 'cached':True}
    if HALT.is_set():
        return {'symbol':symbol, 'interval':interval, 'error':'HALTED'}
    with LOCK:
        slot=max(time.monotonic(),NEXT);NEXT=slot+.4
    time.sleep(max(0,slot-time.monotonic()))
    query=urllib.parse.urlencode({'symbol':symbol,'interval':interval,'limit':300})
    url=BASE+'klines?'+query;started=int(time.time()*1000)
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req,timeout=25) as response:
            raw=response.read(300000)
        bars=json.loads(raw)
        if not isinstance(bars,list) or any(not isinstance(b,list) or len(b)<11 for b in bars):
            raise ValueError('INVALID_KLINE_RESPONSE')
        result={'symbol':symbol,'interval':interval,'request_ms':started,
                'received_ms':int(time.time()*1000),'url':url,
                'sha256':hashlib.sha256(raw).hexdigest(),'bars':bars}
        target.write_text(json.dumps(result),encoding='utf-8')
        return {'symbol':symbol,'interval':interval,'bars':len(bars)}
    except Exception as exc:
        if isinstance(exc,urllib.error.HTTPError) and exc.code in (418,429,451):HALT.set()
        return {'symbol':symbol,'interval':interval,'error':str(exc)[:160]}


def main():
    OUT.mkdir(exist_ok=True)
    tickers=json.loads((ROOT/'tickers.json').read_text())
    exchange=json.loads((ROOT/'exchange.json').read_text())
    allowed={m['symbol'] for m in exchange['symbols'] if m['status']=='TRADING'
             and m['contractType']=='PERPETUAL' and m['quoteAsset']=='USDT' and m['marginAsset']=='USDT'}
    selected=[r for r in tickers if r['symbol'] in allowed and float(r['quoteVolume'])>=8000000]
    selected.sort(key=lambda r:abs(float(r['priceChangePercent'])),reverse=True)
    (ROOT/'universe.json').write_text(json.dumps(selected))
    jobs=[(r['symbol'],tf) for r in selected for tf in ('1h','4h','15m')]
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures=[executor.submit(fetch,j) for j in jobs]
        for f in concurrent.futures.as_completed(futures):
            result=f.result();results.append(result)
            if result.get('error') or len(results)%30==0:
                print(json.dumps({'completed':len(results),'total':len(jobs),'last':result}),flush=True)
    (ROOT/'fetch_results.json').write_text(json.dumps(results))
    print(json.dumps({'done':len(results),'errors':[r for r in results if r.get('error')]}),flush=True)


if __name__=='__main__':main()
