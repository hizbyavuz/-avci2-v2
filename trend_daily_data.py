"""Daily archive preparation. Reuses verified download/cache code, never old DBs."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import requests
import time
from trend_breakout_data import Archive,EXCLUDED,ms,iso,month,months
from trend_breakout_engine import Candle,DAY
from trend_daily_engine import DailySeries

DEFAULT_END=ms('2026-10-23')

def universe_at(all_bars,at):
    rows=[]
    for symbol,bars in all_bars.items():
        if symbol in EXCLUDED or not symbol.endswith('USDT') or '_' in symbol:continue
        past=[b for b in bars if b.close_ms<=at]
        if len(past)<100 or past[0].open_ms>at-100*DAY:continue
        recent=[b for b in past if at-30*DAY<=b.open_ms<at]
        if len(recent)!=30 or any(b.open_ms!=at-30*DAY+i*DAY for i,b in enumerate(recent)):continue
        rows.append(dict(symbol=symbol,volume_30d=sum(b.quote_volume for b in recent),
                         selection_ms=at,last_used_ms=recent[-1].close_ms,first_observed_ms=past[0].open_ms))
    return sorted(rows,key=lambda r:(-r['volume_30d'],r['symbol']))[:50]


def rest_funding(symbol,start,end,root):
    path=Path(root)/'rest_funding'/f'{symbol}-{start}-{end}.json'
    if path.exists():return json.loads(path.read_text())
    rows=[];at=start;session=requests.Session()
    # Same public Binance futures endpoint, served on Binance's www host.
    while at<end:
        for attempt in range(3):
            try:
                r=session.get('https://www.binance.com/fapi/v1/fundingRate',params=dict(symbol=symbol,startTime=at,endTime=end-1,limit=1000),timeout=30)
                r.raise_for_status();raw=r.json();break
            except (requests.RequestException,ValueError):
                if attempt==2:raise
                time.sleep(.5*(attempt+1))
        if not isinstance(raw,list):raise ValueError('INVALID_FUNDING_RESPONSE')
        if not raw:break
        for x in raw:
            if x['symbol']!=symbol:raise ValueError('FUNDING_SYMBOL_MISMATCH')
            t=int(x['fundingTime'])
            if not start<=t<end:raise ValueError('FUNDING_TIME_MISMATCH')
            rows.append(dict(time=t,rate=float(x['fundingRate'])))
        nxt=int(raw[-1]['fundingTime'])+1
        if nxt<=at:raise ValueError('FUNDING_PAGINATION_STALLED')
        at=nxt
        if len(raw)<1000:break
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(rows))
    return rows


def prepare(archive,root,end=DEFAULT_END,workers=16):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    rawdir=root/'daily';rawdir.mkdir(exist_ok=True)
    catalog=archive.listing('data/futures/um/monthly/klines/')['prefixes']
    symbols=sorted(p.split('/')[-2] for p in catalog if p.split('/')[-2].endswith('USDT') and '_' not in p.split('/')[-2] and p.split('/')[-2] not in EXCLUDED)
    errors=[];all_bars={};last_complete=ms(month(end)+'-01')
    def one(symbol):
        path=rawdir/f'{symbol}.json'
        if path.exists():
            x=json.loads(path.read_text())
            if x['end']==end:return symbol,[Candle(**b) for b in x['bars']]
        keys=archive.listing(f'data/futures/um/monthly/klines/{symbol}/1d/')['keys']
        bars=[]
        for key in keys:
            if key.endswith('.zip') and ms(key[-11:-4]+'-01')<end:
                bars.extend(archive.candles(symbol,'1d',key[-11:-4]))
        bars=sorted({b.open_ms:b for b in bars if b.close_ms<=last_complete}.values(),key=lambda b:b.open_ms)
        DailySeries(bars)
        path.write_text(json.dumps(dict(end=end,bars=[asdict(b) for b in bars]),separators=(',',':')))
        return symbol,bars
    with ThreadPoolExecutor(max_workers=workers) as ex:
        jobs={ex.submit(one,s):s for s in symbols}
        for n,f in enumerate(as_completed(jobs),1):
            try:s,b=f.result();all_bars[s]=b
            except Exception as e:errors.append(dict(symbol=jobs[f],stage='daily',error=type(e).__name__))
            if n%25==0 or n==len(jobs):print(json.dumps(dict(stage='daily_archive',done=n,total=len(jobs),errors=len(errors))),flush=True)
    earliest=min(b[0].open_ms for b in all_bars.values() if b)
    selected={m:universe_at(all_bars,ms(m+'-01')) for m in months(earliest,end)}
    union=sorted({r['symbol'] for rows in selected.values() for r in rows}|{'BTCUSDT'})
    dataout=root/'prepared';dataout.mkdir(exist_ok=True)
    manifest=[]
    def finish(symbol):
        path=dataout/f'{symbol}.json'
        if path.exists():
            x=json.loads(path.read_text())
            if x['end']==end:return dict(symbol=symbol,rows=len(x['bars']),funding_errors=x['funding_errors'],sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        bars=list(all_bars[symbol]);funding=[];coverage=[];ferrors=[]
        # Current month daily files are needed for selected/open historical names.
        if bars and bars[-1].close_ms==last_complete:
            for day in range(last_complete,end,DAY):bars.extend(archive.candles(symbol,'1d',iso(day)[:10],daily=True))
        bars=sorted({b.open_ms:b for b in bars if b.close_ms<=end}.values(),key=lambda b:b.open_ms)
        DailySeries(bars)
        for m in months(bars[0].open_ms,bars[-1].close_ms):
            try:
                rows=archive.funding(symbol,m)
                if not rows:
                    a=ms(m+'-01');z=min(ms(list(months(a,a+32*DAY))[1]+'-01'),end,bars[-1].close_ms)
                    rows=rest_funding(symbol,max(a,bars[0].open_ms),z,root)
                if rows:funding.extend(rows);coverage.append(m)
                else:ferrors.append(dict(month=m,error='NO_ACTUAL_FUNDING'))
            except Exception as e:ferrors.append(dict(month=m,error=type(e).__name__))
        unique={int(f['time']):f for f in funding}
        x=dict(symbol=symbol,source='futures',end=end,bars=[asdict(b) for b in bars],funding=[unique[t] for t in sorted(unique)],funding_coverage=coverage,funding_errors=ferrors)
        path.write_text(json.dumps(x,separators=(',',':')))
        return dict(symbol=symbol,rows=len(bars),funding_errors=ferrors,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with ThreadPoolExecutor(max_workers=workers) as ex:
        jobs={ex.submit(finish,s):s for s in union}
        for n,f in enumerate(as_completed(jobs),1):
            try:manifest.append(f.result())
            except Exception as e:errors.append(dict(symbol=jobs[f],stage='prepared',error=type(e).__name__))
            if n%20==0 or n==len(jobs):print(json.dumps(dict(stage='daily_funding',done=n,total=len(jobs),errors=len(errors))),flush=True)
    universe=dict(start=earliest,end=end,months=selected,excluded=sorted(EXCLUDED),catalog_symbols=len(symbols),errors=errors)
    (root/'universe.json').write_text(json.dumps(universe,indent=2))
    out=dict(start=earliest,end=end,symbols=sorted(manifest,key=lambda x:x['symbol']),errors=errors)
    (root/'manifest.json').write_text(json.dumps(out,indent=2));return out


def repair_funding(archive,root,workers=6):
    """Retry transport failures before sealing; never replace unknown rates by zero."""
    root=Path(root);manifest=json.loads((root/'manifest.json').read_text())
    def one(record):
        path=root/'prepared'/f"{record['symbol']}.json";x=json.loads(path.read_text())
        failures=[];funding=x['funding'];coverage=set(x['funding_coverage']);changed=False
        for error in x['funding_errors']:
            if error['error']=='NO_ACTUAL_FUNDING':failures.append(error);continue
            m=error['month'];changed=True
            try:
                rows=archive.funding(x['symbol'],m)
                if not rows:
                    a=ms(m+'-01');z=min(ms(list(months(a,a+32*DAY))[1]+'-01'),x['end'],x['bars'][-1]['close_ms'])
                    rows=rest_funding(x['symbol'],max(a,x['bars'][0]['open_ms']),z,root)
                if rows:funding.extend(rows);coverage.add(m)
                else:failures.append(dict(month=m,error='NO_ACTUAL_FUNDING'))
            except Exception as e:failures.append(dict(month=m,error=type(e).__name__))
        if changed:
            unique={f['time']:f for f in funding};x.update(funding=[unique[t] for t in sorted(unique)],funding_coverage=sorted(coverage),funding_errors=failures)
            path.write_text(json.dumps(x,separators=(',',':')))
        return dict(symbol=x['symbol'],rows=len(x['bars']),funding_errors=x['funding_errors'],sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with ThreadPoolExecutor(max_workers=workers) as pool:manifest['symbols']=list(pool.map(one,manifest['symbols']))
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(dict(stage='funding_repair',remaining_transport_errors=sum(sum(e['error']!='NO_ACTUAL_FUNDING' for e in r['funding_errors']) for r in manifest['symbols'])),flush=True))
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--archive-cache',required=True);p.add_argument('--data',required=True);p.add_argument('--workers',type=int,default=16)
    p.add_argument('--repair-funding',action='store_true')
    a=p.parse_args()
    if a.repair_funding:repair_funding(Archive(a.archive_cache),a.data,min(a.workers,6))
    else:prepare(Archive(a.archive_cache),a.data,workers=a.workers)
