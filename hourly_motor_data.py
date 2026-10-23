"""Isolated official futures data preparation. Older caches are read-only inputs."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import asdict,astuple
import gzip
import hashlib
import json
from pathlib import Path
import requests
from hourly_motor_engine import Candle,DAY,HOUR,Series,STABLECOINS,universe_at
from trend_breakout_data import Archive,ms,iso,month,months


def rest_funding(symbol,start,end,root):
    path=Path(root)/'rest_funding'/f'{symbol}-{start}-{end}.json'
    if path.exists():return json.loads(path.read_text())
    rows=[];cursor=start
    while cursor<end:
        r=requests.get('https://www.binance.com/fapi/v1/fundingRate',params=dict(symbol=symbol,startTime=cursor,endTime=end-1,limit=1000),timeout=30)
        r.raise_for_status();raw=r.json()
        if not isinstance(raw,list):raise ValueError('INVALID_FUNDING_RESPONSE')
        if not raw:break
        for v in raw:
            if v['symbol']!=symbol:raise ValueError('WRONG_FUNDING_SYMBOL')
            rows.append(dict(time=int(v['fundingTime']),rate=float(v['fundingRate'])))
        nxt=int(raw[-1]['fundingTime'])+1
        if nxt<=cursor:raise ValueError('PAGINATION_STALLED')
        cursor=nxt
        if len(raw)<1000:break
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(rows));return rows


def prepare(root,archive_root,sources,start,end,workers=12):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);archive=Archive(archive_root)
    sources=[Path(p) for p in sources];daily=root/'daily';daily.mkdir(exist_ok=True)
    prefixes=archive.listing('data/futures/um/monthly/klines/')['prefixes']
    symbols=sorted(p.split('/')[-2] for p in prefixes if p.split('/')[-2].endswith('USDT') and '_' not in p.split('/')[-2] and p.split('/')[-2] not in STABLECOINS)
    errors=[];repairs=[];all_bars={}
    def daily_one(s):
        dest=daily/(s+'.json')
        if dest.exists():return s,[Candle(**b) for b in json.loads(dest.read_text())['bars']]
        bars=[]
        for source in sources:
            p=source/'daily'/(s+'.json')
            if p.exists():bars=[Candle(**b) for b in json.loads(p.read_text())['bars']];break
        if not bars:
            keys=archive.listing(f'data/futures/um/monthly/klines/{s}/1d/')['keys']
            for k in keys:
                if k.endswith('.zip') and ms(k[-11:-4]+'-01')<end:bars.extend(archive.candles(s,'1d',k[-11:-4]))
        bars=sorted({b.open_ms:b for b in bars if b.close_ms<=end}.values(),key=lambda b:b.open_ms)
        Series(bars,DAY);dest.write_text(json.dumps(dict(bars=[asdict(b) for b in bars]),separators=(',',':')))
        return s,bars
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(daily_one,s):s for s in symbols}
        for n,f in enumerate(as_completed(jobs),1):
            try:s,b=f.result();all_bars[s]=b
            except Exception as e:errors.append(dict(symbol=jobs[f],stage='daily',error=str(e)[:150]))
            if n%100==0 or n==len(jobs):print(json.dumps(dict(stage='daily',done=n,total=len(jobs),errors=len(errors))),flush=True)
    selected={m:universe_at(all_bars,ms(m+'-01')) for m in months(start,end)}
    union=sorted({r['symbol'] for rows in selected.values() for r in rows}|{'BTCUSDT'})
    target=root/'prepared';target.mkdir(exist_ok=True)
    def finish(s):
        p=target/(s+'.json.gz')
        if p.exists():return dict(symbol=s,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        bars=all_bars[s];funding=[];coverage=set();ferrors=[]
        for source in sources:
            old=source/'prepared'/(s+'.json')
            if old.exists():
                x=json.loads(old.read_text());funding=x.get('funding',[]);coverage=set(x.get('funding_coverage',[]));ferrors=x.get('funding_errors',[])
                extra=[Candle(**b) for b in x['bars'] if b['open_ms']>=ms(month(end)+'-01') and b['close_ms']<=end]
                bars=sorted({b.open_ms:b for b in bars+extra}.values(),key=lambda b:b.open_ms);break
        if bars and bars[-1].close_ms==ms(month(end)+'-01'):
            for t in range(bars[-1].close_ms,end,DAY):bars.extend(archive.candles(s,'1d',iso(t)[:10],daily=True))
        # Download only months present in the historical catalogue, including delisted contracts.
        keys=archive.listing(f'data/futures/um/monthly/klines/{s}/1h/')['keys'];hours=[]
        for k in keys:
            if k.endswith('.zip') and ms(k[-11:-4]+'-01')<end:hours.extend(archive.candles(s,'1h',k[-11:-4]))
        if bars:
            last_month=ms(month(end)+'-01')
            if bars[-1].close_ms>last_month:
                for t in range(last_month,end+HOUR,DAY):hours.extend(archive.candles(s,'1h',iso(t)[:10],daily=True))
        known={b.open_ms:b for b in hours if b.close_ms<=end+HOUR}
        # The last complete day's timeout needs the next boundary opening.
        # Today's daily ZIP is not published until tomorrow; use original REST.
        if bars and bars[-1].close_ms==end and bars[-1].volume>0 and end not in known:
            rr=requests.get('https://www.binance.com/fapi/v1/klines',params=dict(symbol=s,interval='1h',startTime=end,endTime=end+HOUR-1,limit=1),timeout=30)
            rr.raise_for_status();raw=rr.json()
            if not isinstance(raw,list):raise ValueError('INVALID_BOUNDARY_PRICE_RESPONSE')
            for v in raw:
                b=Candle(int(v[0]),int(v[6])+1,*map(float,v[1:6]),float(v[7]))
                if b.open_ms==end and b.close_ms==end+HOUR:known[end]=b
        if known:
            missing_days=sorted({t//DAY*DAY for t in range(min(known),max(known)+HOUR,HOUR) if t not in known})
            for d in missing_days:
                try:
                    extra=archive.candles(s,'1h',iso(d)[:10],daily=True)
                    for b in extra:
                        if b.open_ms not in known and b.close_ms<=end+HOUR:known[b.open_ms]=b;repairs.append(dict(symbol=s,time=b.open_ms))
                except Exception as e:errors.append(dict(symbol=s,time=d,stage='hourly_repair',error=str(e)[:100]))
        hours=sorted(known.values(),key=lambda b:b.open_ms)
        for m in months(bars[0].open_ms,end+8*HOUR):
            a=ms(m+'-01');z=min(ms(list(months(a,a+33*DAY))[1]+'-01'),end+8*HOUR)
            if m in coverage and m!=month(end):continue
            try:
                rows=archive.funding(s,m) or rest_funding(s,max(a,bars[0].open_ms),z,root)
                if rows:funding.extend(rows);coverage.add(m)
                else:ferrors.append(dict(month=m,error='NO_ACTUAL_FUNDING'))
            except Exception as e:ferrors.append(dict(month=m,error=type(e).__name__))
        funding=sorted({int(v['time'])//1000*1000:dict(v,time=int(v['time'])//1000*1000) for v in funding}.values(),key=lambda v:v['time'])
        Series(bars,DAY);Series(hours,HOUR)
        gaps=[dict(before=a.close_ms,after=b.open_ms) for a,b in zip(hours,hours[1:]) if a.close_ms!=b.open_ms]
        payload=dict(symbol=s,source='futures',daily=[asdict(b) for b in bars],hourly=[astuple(b) for b in hours],
                     funding=funding,funding_coverage=sorted(coverage),funding_errors=ferrors,hourly_gaps=gaps)
        with gzip.open(p,'wt') as f:json.dump(payload,f,separators=(',',':'))
        return dict(symbol=s,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),daily=len(bars),hourly=len(hours),hourly_gaps=gaps)
    records=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(finish,s):s for s in union}
        for n,f in enumerate(as_completed(jobs),1):
            try:records.append(f.result())
            except Exception as e:
                error=dict(symbol=jobs[f],stage='prepared',error=type(e).__name__+':'+str(e)[:150]);errors.append(error)
                print(json.dumps(error),flush=True)
            if n%10==0 or n==len(jobs):print(json.dumps(dict(stage='hourly_funding',done=n,total=len(jobs),errors=len(errors))),flush=True)
    universe=dict(start=start,end=end,months=selected,excluded=sorted(STABLECOINS),catalog_symbols=len(symbols))
    (root/'universe.json').write_text(json.dumps(universe,indent=2))
    (root/'manifest.json').write_text(json.dumps(dict(start=start,end=end,symbols=sorted(records,key=lambda r:r['symbol']),errors=errors,repairs=repairs),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--archive-cache',required=True)
    p.add_argument('--source',action='append',default=[]);p.add_argument('--start',default='2020-01-01');p.add_argument('--end',default='2026-10-23')
    p.add_argument('--workers',type=int,default=12);a=p.parse_args();prepare(a.data,a.archive_cache,a.source,ms(a.start),ms(a.end),a.workers)
