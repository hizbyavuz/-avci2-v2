"""Predeclared four-factor research, isolated from the deployed trading worker."""
import argparse
import asyncio
from bisect import bisect_right
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import time
import zipfile

from ta_paper.core import CFG, MINUTE
from ta_paper.features import funding_percentile, hypothesis_results
from ta_paper.replay import execute
from .data import DAY, MONTHS, ROOT, PROTOCOL, read_rows, discover_symbol, MinutePath
from .download import Download, Blocked, key, atomic


def read_gzip(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:return json.load(f)


def write_gzip(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp')
    with gzip.open(tmp,'wt',encoding='utf-8') as f:json.dump(payload,f,allow_nan=False,ensure_ascii=False,separators=(',',':'))
    tmp.replace(path)


def period(t,fmt='%Y-%m'):return datetime.fromtimestamp(t/1000,timezone.utc).strftime(fmt)


def needed_files(candidates):
    events=candidates['events'];symbol=candidates['symbol'];months=set();dates=set()
    for p in events:
        start=p['observed_ms'];end=p['entry_ms']+72*60*MINUTE
        for day in range(start//DAY,end//DAY+1):months.add(period(day*DAY))
        cutoff=start-5*MINUTE
        for day in range((cutoff-4*60*MINUTE)//DAY,cutoff//DAY+1):dates.add(period(day*DAY,'%Y-%m-%d'))
    keys=[]
    for m in sorted(months):keys.extend((key(symbol,'1m',m),key(symbol,'30m',m,'markPriceKlines')))
    if events:
        for m in MONTHS+['2026-08']:keys.append(key(symbol,'1m',m,'fundingRate'))
    keys.extend(f'data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{d}.zip' for d in sorted(dates))
    return keys,sorted(months),sorted(dates)


def oi_rows(cache,symbol,dates):
    records={};issues=[];duplicates=0
    for date in dates:
        path=Path(cache)/f'data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip'
        if not path.exists():issues.append(dict(date=date,reason='MISSING_METRICS'));continue
        try:
            blob=path.read_bytes()
            if hashlib.sha256(blob).hexdigest()!=path.with_suffix('.sha256').read_text().strip():raise ValueError('CHECKSUM')
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                if len(z.infolist())!=1 or z.infolist()[0].file_size>30_000_000:raise ValueError('ZIP_CONTENT')
                with z.open(z.namelist()[0]) as source:
                    local={}
                    for r in csv.DictReader(io.TextIOWrapper(source,encoding='utf-8-sig')):
                        t=int(datetime.fromisoformat(r['create_time']).replace(tzinfo=timezone.utc).timestamp()*1000)
                        value=float(r['sum_open_interest'])
                        if r['symbol']!=symbol or period(t,'%Y-%m-%d')!=date or t%(5*MINUTE) or not 0<value<float('inf'):
                            raise ValueError('METRICS_SCHEMA')
                        if t in local:
                            if local[t]!=value:raise ValueError('CONFLICTING_METRICS')
                            duplicates+=1
                        local[t]=value
                    records.update(local)
        except Exception as e:issues.append(dict(date=date,reason=type(e).__name__+':'+str(e)[:100]))
    return records,dict(issues=issues,identical_duplicates=duplicates,publication_time_verified=False)


def oi_at(records,times,cutoff):
    i=bisect_right(times,cutoff)-1
    if i<0:return None,'MISSING'
    last=times[i];start=last-240*MINUTE
    if cutoff-last>10*MINUTE or any(t not in records for t in range(start,last+1,5*MINUTE)):
        return None,'STALE_OR_GAP'
    return records[last]/records[start]-1,'OK'


def archived_funding_percentile(rows,cutoff):
    """Preserve availability time; tolerate <=1s archive settlement jitter.

    The declared 1/4/8h interval and rounded hourly slots must both be constant.
    Future raw timestamps are excluded BEFORE slot normalization. Execution
    funding continues to use the original timestamps without any rounding.
    """
    sample=[];intervals=set()
    for t,rate,interval in rows:
        if t>cutoff or t<cutoff-30*DAY-8*60*MINUTE:continue
        offset=t%(60*MINUTE)
        if offset>1000 or interval not in (1,4,8):
            return dict(status='UNSUPPORTED_SETTLEMENT_TIMESTAMP_OR_INTERVAL',percentile=None)
        intervals.add(interval);sample.append(dict(fundingTime=t-offset,fundingRate=rate))
    if len(intervals)>1:return dict(status='INTERVAL_CHANGE_OR_GAP',percentile=None)
    result=funding_percentile(sample,cutoff)
    if result['status']=='OK' and result['interval_ms']!=next(iter(intervals))*60*MINUTE:
        return dict(status='DECLARED_INTERVAL_MISMATCH',percentile=None)
    return result


def stress_same_event(result,path,multiplier=1.5):
    """Reprice costs only: no new sizing, rejection, path or risk denominator."""
    if result['status']!='CLOSED':return None
    sign=1 if result['direction']=='LONG' else -1
    opening=path[result['entry_ms']][1]
    entry_cost=opening*(CFG.entry_slippage+PROTOCOL['spread_fraction']/2)
    exit_slip=CFG.stop_slippage if result['reason']=='STOP' else CFG.target_slippage
    raw_exit=result['exit_price']/(1-sign*exit_slip)
    slip_cost=raw_exit*exit_slip
    base_fees=CFG.fee_each_side*(result['entry']+result['exit_price'])
    extra=result['quantity']*(multiplier-1)*(entry_cost+slip_cost+base_fees)
    value=(result['net_pnl']-extra)/result['planned_loss']
    if value>result['net_r']+1e-10:raise AssertionError('STRESS_IMPROVED_SAME_EVENT')
    return value


def process_symbol(args):
    cache,output,symbol,trim=args;out=Path(output)/'outcomes'/(symbol+'.json.gz')
    if out.exists():return symbol,'CACHED'
    candidates=read_gzip(Path(output)/'candidates'/(symbol+'.json.gz'))
    _,months,dates=needed_files(candidates)
    if not candidates['events']:
        write_gzip(out,dict(symbol=symbol,events=[],links={},data_issues=candidates['data_issues']));return symbol,0
    minute,mi=read_rows(cache,symbol,'1m',months);path=MinutePath(minute)
    rates,ri=read_rows(cache,symbol,'1m',MONTHS+['2026-08'],'fundingRate')
    marks,ki=read_rows(cache,symbol,'30m',months,'markPriceKlines')
    mark_map={int(r[0]):r.tolist() for r in marks}
    rate_rows=[[int(r[0]),float(r[1]),float(r[2])] for r in rates]
    rt=[r[0] for r in rate_rows]
    oi,quality=oi_rows(cache,symbol,dates);ot=sorted(oi)
    results=[];features_cache={}
    for p in candidates['events']:
        now=p['observed_ms']
        if now not in features_cache:
            fields=dict(p['features']);cutoff=now-5*MINUTE
            fields['oi_change_4h'],fields['oi_status']=oi_at(oi,ot,cutoff)
            left=bisect_right(rt,cutoff-30*DAY-8*60*MINUTE-1);right=bisect_right(rt,cutoff)
            rank=archived_funding_percentile(rate_rows[left:right],cutoff)
            fields.update(funding_percentile_30d=rank['percentile'],funding_status=rank['status'])
            features_cache[now]=fields
        fields=dict(features_cache[now])
        result=execute(p,path,rate_rows,mark_map)
        result.update(features=fields,hypotheses=hypothesis_results(fields,p['direction']))
        if result['status']=='CLOSED':result['net_r_1_5']=stress_same_event(result,path)
        results.append(result)
    payload=dict(symbol=symbol,events=results,links=candidates['links'],counts=candidates.get('counts',{}),
                 data_issues=candidates['data_issues']+mi+ri+ki,oi_quality=quality)
    write_gzip(out,payload)
    if trim:
        for m in months:
            p=Path(cache)/key(symbol,'1m',m)
            if p.exists():p.unlink()  # Raw reproducible cache only; checksums/outcomes retained.
    return symbol,len(results)


def identity():
    files=[*ROOT.glob('*.py'),ROOT/'protocol.json',ROOT/'universe_classification.json',ROOT.parents[1]/'ta_paper/core.py',
           ROOT.parents[1]/'ta_paper/replay.py',ROOT.parents[1]/'ta_paper/features.py']
    return dict(protocol_sha256=hashlib.sha256((ROOT/'protocol.json').read_bytes()).hexdigest(),
                code_files={str(p.relative_to(ROOT.parents[1])):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                strategy_hash=CFG.fingerprint)


async def run(args):
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    current=identity();ident=output/'run_identity.json'
    if ident.exists() and json.loads(ident.read_text())!=current:raise RuntimeError('CODE_CHANGED_USE_NEW_RUN_DIRECTORY')
    if current['strategy_hash']!=PROTOCOL['strategy_hash']:raise RuntimeError('FROZEN_CORE_CHANGED')
    atomic(ident,current)
    manifest=json.loads((output/'universe.json').read_text())
    if not manifest.get('complete'):raise RuntimeError('COARSE_DOWNLOAD_NOT_COMPLETE')
    if manifest.get('classification_sha256')!=hashlib.sha256((ROOT/'universe_classification.json').read_bytes()).hexdigest():
        raise RuntimeError('UNIVERSE_CLASSIFICATION_NOT_APPLIED')
    symbols=sorted(s for s,files in manifest['month_files'].items() if any(f[-11:-4]!='2025-09' for f in files))
    if args.symbols:  # Explicit software/data pilot only, never a complete-study claim.
        allowed=set(args.symbols.split(','));symbols=[s for s in symbols if s in allowed]
    status=dict(status='RUNNING',universe_symbols=len(manifest['symbols']),study_symbols=len(symbols),
                explicit_pilot=bool(args.symbols),phase='DISCOVERY',completed=[],errors={})
    atomic(output/'status.json',status)
    loop=asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures=[loop.run_in_executor(pool,discover_symbol,(args.cache,args.output,s)) for s in symbols]
        for f in asyncio.as_completed(futures):
            try:s,count=await f;status['completed'].append(s)
            except Exception as e:raise RuntimeError('DISCOVERY_FAILED:'+repr(e)) from e
            if len(status['completed'])%20==0 or len(status['completed'])==len(symbols):
                print(json.dumps(dict(phase='DISCOVERY',complete=len(status['completed']),total=len(symbols))),flush=True)
                atomic(output/'status.json',status)
        status.update(phase='OUTCOMES',completed=[]);atomic(output/'status.json',status)
        async with Download(args.cache) as downloader:
            throttle=asyncio.Semaphore(8)
            async def one(symbol):
                async with throttle:
                    if (output/'outcomes'/(symbol+'.json.gz')).exists():
                        status['completed'].append(symbol);return
                    try:
                        candidates=read_gzip(output/'candidates'/(symbol+'.json.gz'))
                        keys,_,_=needed_files(candidates)
                        await downloader.batch(keys,'ASSET_DATA:'+symbol)
                        s,count=await loop.run_in_executor(pool,process_symbol,(args.cache,args.output,symbol,True))
                        status['completed'].append(s)
                        print(json.dumps(dict(phase='OUTCOMES',symbol=s,events=count,complete=len(status['completed']),total=len(symbols))),flush=True)
                    except Blocked:raise
                    except Exception as e:status['errors'][symbol]=type(e).__name__+':'+str(e)[:200]
                    finally:atomic(output/'status.json',status)
            await asyncio.gather(*(one(s) for s in symbols))
            atomic(output/'outcome_download_inventory.json',dict(files=downloader.records,errors=downloader.errors,counts=dict(downloader.counts)))
            status['download_errors']=len(downloader.errors)
            status['missing_archives']=sum(r.get('status')=='MISSING_404' for r in downloader.records.values())
        status.update(status='COMPLETE_WITH_GAPS' if status['errors'] or status['download_errors'] or status['missing_archives'] else 'COMPLETE',phase='REPORT',
            completion_note='Processing completion only; parsed-data/feature coverage remains separate in report.json.')
        atomic(output/'status.json',status)
    from .report import build_report
    build_report(output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cache',required=True);p.add_argument('--output',required=True)
    p.add_argument('--workers',type=int,default=4);p.add_argument('--symbols')
    a=p.parse_args()
    if not 1<=a.workers<=6:raise ValueError('WORKER_LIMIT')
    asyncio.run(run(a))
