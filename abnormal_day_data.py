"""Independent data preparation; existing Turtle caches are read-only inputs."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import requests
from trend_breakout_data import Archive,EXCLUDED,ms,iso,months,month
from abnormal_day_engine import Candle,DAY,DailySeries

END=ms('2026-10-23')


def universe_at(all_bars,at):
    rows=[]
    for symbol,bars in all_bars.items():
        if symbol in EXCLUDED or not symbol.endswith('USDT') or '_' in symbol:continue
        past=[b for b in bars if b.close_ms<=at]
        if len(past)<120 or past[0].open_ms>at-120*DAY:continue
        recent=[b for b in past if at-30*DAY<=b.open_ms<at]
        if len(recent)!=30 or any(b.open_ms!=at-30*DAY+i*DAY for i,b in enumerate(recent)):continue
        rows.append(dict(symbol=symbol,volume_30d=sum(b.quote_volume for b in recent),selection_ms=at,
                         last_used_ms=recent[-1].close_ms,first_observed_ms=past[0].open_ms))
    return sorted(rows,key=lambda r:(-r['volume_30d'],r['symbol']))[:20]


def actual_funding(symbol,start,end,root):
    path=Path(root)/'rest_funding'/f'{symbol}-{start}-{end}.json'
    if path.exists():return json.loads(path.read_text())
    rows=[];cursor=start
    while cursor<end:
        r=requests.get('https://www.binance.com/fapi/v1/fundingRate',params=dict(symbol=symbol,startTime=cursor,endTime=end-1,limit=1000),timeout=30)
        r.raise_for_status();raw=r.json()
        if not isinstance(raw,list):raise ValueError('INVALID_FUNDING_RESPONSE')
        if not raw:break
        for v in raw:
            if v['symbol']!=symbol or not start<=int(v['fundingTime'])<end:raise ValueError('FUNDING_MISMATCH')
            rows.append(dict(time=int(v['fundingTime']),rate=float(v['fundingRate'])))
        nxt=int(raw[-1]['fundingTime'])+1
        if nxt<=cursor:raise ValueError('PAGINATION_STALLED')
        cursor=nxt
        if len(raw)<1000:break
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(rows));return rows


def prepare(source,root,archive_root,end=END,workers=16):
    source=Path(source);root=Path(root);root.mkdir(parents=True,exist_ok=True)
    archive=Archive(archive_root);out=root/'daily';out.mkdir(exist_ok=True)
    errors=[];repairs=[];all_bars={}
    inputs=sorted((source/'daily').glob('*.json'))
    if not inputs:
        raw=root/'downloaded_daily';raw.mkdir(exist_ok=True)
        prefixes=archive.listing('data/futures/um/monthly/klines/')['prefixes']
        symbols=sorted(p.split('/')[-2] for p in prefixes if p.split('/')[-2].endswith('USDT') and '_' not in p.split('/')[-2] and p.split('/')[-2] not in EXCLUDED)
        def download(s):
            path=raw/(s+'.json')
            if path.exists():return path
            keys=archive.listing(f'data/futures/um/monthly/klines/{s}/1d/')['keys'];bars=[]
            for key in keys:
                if key.endswith('.zip') and ms(key[-11:-4]+'-01')<end:bars.extend(archive.candles(s,'1d',key[-11:-4]))
            bars=sorted({b.open_ms:b for b in bars if b.close_ms<=ms(month(end)+'-01')}.values(),key=lambda b:b.open_ms)
            path.write_text(json.dumps(dict(bars=[asdict(b) for b in bars])));return path
        with ThreadPoolExecutor(max_workers=workers) as pool:inputs=list(pool.map(download,symbols))
    def one(path):
        symbol=path.stem;dest=out/path.name
        if dest.exists():
            x=json.loads(dest.read_text());return symbol,[Candle(**b) for b in x['bars']],x['repairs'],x['errors']
        x=json.loads(path.read_text());bars=[Candle(**b) for b in x['bars'] if b['close_ms']<=end]
        fixes=[];failures=[]
        if bars:
            known={b.open_ms:b for b in bars}
            # Interior gaps only; do not manufacture candles after delisting.
            missing=[t for t in range(bars[0].open_ms,bars[-1].close_ms,DAY) if t not in known]
            for t in missing:
                try:
                    extra=archive.candles(symbol,'1d',iso(t)[:10],daily=True)
                    extra=[b for b in extra if b.open_ms==t and b.close_ms==t+DAY]
                    if extra:known[t]=extra[0];fixes.append(dict(symbol=symbol,time=t,source='official_daily_archive'))
                    else:
                        hours=[b for b in archive.candles(symbol,'1h',month(t)) if t<=b.open_ms<t+DAY]
                        if not hours:hours=archive.candles(symbol,'1h',iso(t)[:10],daily=True)
                        if len(hours)==24 and all(b.open_ms==t+i*3600000 and b.close_ms==b.open_ms+3600000 for i,b in enumerate(hours)):
                            known[t]=Candle(t,t+DAY,hours[0].open,max(b.high for b in hours),min(b.low for b in hours),hours[-1].close,sum(b.volume for b in hours),sum(b.quote_volume for b in hours))
                            fixes.append(dict(symbol=symbol,time=t,source='aggregated_24_official_1h_candles'))
                        else:failures.append(dict(symbol=symbol,time=t,reason='DAILY_AND_HOURLY_GAP_NOT_PUBLISHED'))
                except Exception as e:failures.append(dict(symbol=symbol,time=t,reason=type(e).__name__))
            bars=sorted(known.values(),key=lambda b:b.open_ms)
        DailySeries(bars)
        dest.write_text(json.dumps(dict(bars=[asdict(b) for b in bars],repairs=fixes,errors=failures),separators=(',',':')))
        return symbol,bars,fixes,failures
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(one,p):p.stem for p in inputs}
        for n,f in enumerate(as_completed(jobs),1):
            try:s,b,r,e=f.result();all_bars[s]=b;repairs.extend(r);errors.extend(e)
            except Exception as e:errors.append(dict(symbol=jobs[f],reason=type(e).__name__))
            if n%100==0 or n==len(jobs):print(json.dumps(dict(stage='daily_validation',done=n,total=len(jobs),repairs=len(repairs),errors=len(errors))),flush=True)
    start=max(ms('2020-01-01'),min(b[0].open_ms for b in all_bars.values() if b))
    selected={m:universe_at(all_bars,ms(m+'-01')) for m in months(start,end)}
    union=sorted({r['symbol'] for v in selected.values() for r in v})
    prepared=root/'prepared';prepared.mkdir(exist_ok=True);manifest=[]
    def finish(s):
        dest=prepared/(s+'.json')
        if dest.exists():return dict(symbol=s,sha256=hashlib.sha256(dest.read_bytes()).hexdigest())
        bars=all_bars[s];prior=source/'prepared'/(s+'.json')
        funding=[];coverage=[];ferrors=[]
        if prior.exists():
            p=json.loads(prior.read_text());funding=p['funding'];coverage=p['funding_coverage'];ferrors=p['funding_errors']
            extra=[Candle(**b) for b in p['bars'] if b['open_ms']>=ms(month(end)+'-01') and b['close_ms']<=end]
            bars=sorted({b.open_ms:b for b in bars+extra}.values(),key=lambda b:b.open_ms)
        if bars and bars[-1].close_ms==ms(month(end)+'-01'):
            for t in range(bars[-1].close_ms,end,DAY):bars.extend(archive.candles(s,'1d',iso(t)[:10],daily=True))
        if not prior.exists():
            for m in months(bars[0].open_ms,end):
                a=ms(m+'-01');z=min(a+32*DAY,end);z=min(ms(next(x for x in months(a,a+33*DAY) if x!=m)+'-01'),end)
                try:
                    rows=archive.funding(s,m) or actual_funding(s,max(a,bars[0].open_ms),z,root)
                    if rows:funding.extend(rows);coverage.append(m)
                    else:ferrors.append(dict(month=m,error='NO_ACTUAL_FUNDING'))
                except Exception as e:ferrors.append(dict(month=m,error=type(e).__name__))
        DailySeries(bars)
        payload=dict(symbol=s,source='futures',bars=[asdict(b) for b in bars],funding=funding,funding_coverage=coverage,funding_errors=ferrors)
        dest.write_text(json.dumps(payload,separators=(',',':')))
        return dict(symbol=s,sha256=hashlib.sha256(dest.read_bytes()).hexdigest())
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(finish,s):s for s in union}
        for n,f in enumerate(as_completed(jobs),1):
            try:manifest.append(f.result())
            except Exception as e:errors.append(dict(symbol=jobs[f],stage='prepared',reason=type(e).__name__))
            if n%20==0 or n==len(jobs):print(json.dumps(dict(stage='selected_data',done=n,total=len(jobs))),flush=True)
    universe=dict(start=start,end=end,months=selected,catalog_symbols=len(inputs),excluded=sorted(EXCLUDED))
    (root/'universe.json').write_text(json.dumps(universe,indent=2))
    (root/'manifest.json').write_text(json.dumps(dict(start=start,end=end,symbols=sorted(manifest,key=lambda x:x['symbol']),repairs=repairs,errors=errors),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--data',required=True);p.add_argument('--archive-cache',required=True)
    a=p.parse_args();prepare(a.source,a.data,a.archive_cache)
