"""All-symbol offline SHORT V2 replay. No downloading, tuning, or live delivery."""
import argparse
import bisect
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import long_short_short_retest as engine
from long_short_swing import HOUR,MINUTE,check_bars
from long_short_swing_outcomes import initial,advance,result
from research.swing_v1.replay import archive


def load_evidence(name):
    p=ROOT/'research/swing_v1/evidence'
    meta=next(r for r in json.loads((p/'MANIFEST.json').read_text()) if r['path']=='evidence/'+name)
    body=(p/name).read_bytes()
    if hashlib.sha256(body).hexdigest()!=meta['compressed_sha256']:raise ValueError('EVIDENCE_CHANGED')
    raw=gzip.decompress(body)
    if hashlib.sha256(raw).hexdigest()!=meta['uncompressed_sha256']:raise ValueError('EVIDENCE_CHANGED')
    return json.loads(raw)


def months_between(start,end):
    a=datetime.fromtimestamp(start/1000,timezone.utc).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    values=[]
    while int(a.timestamp()*1000)<end:
        values.append(a.strftime('%Y-%m'))
        a=a.replace(year=a.year+(a.month==12),month=a.month%12+1)
    return values


def audit_one(p,rows,stamps):
    start=(p['observed_ms']//MINUTE+1)*MINUTE;end=start+168*HOUR
    item=dict(symbol=p['symbol'],direction='SHORT',model=p['model'],confirmation_ms=p['confirmation_ms'],
              observed_ms=p['observed_ms'],reference_ms=start,entry_price=None,
              initial_stop=p['stop'],outcome=None,funding_status='UNKNOWN',full_net_pct=None)
    path=rows[bisect.bisect_left(stamps,start):bisect.bisect_left(stamps,end)]
    if not path or path[0]['open_ms']!=start:return dict(item,status='ENTRY_DATA_MISSING')
    try:check_bars(path[:1],MINUTE)
    except (ValueError,KeyError,TypeError) as exc:return dict(item,status='DATA_INVALID',reason=str(exc))
    entry=path[0]['open'];item['entry_price']=entry
    reason=engine.geometry(p,entry)
    if reason:return dict(item,status='ENTRY_INVALID',reason=reason)
    g=advance(p,initial(p,start,entry),path)
    if g['cursor_ms']<end:g['missing_ms']=g['cursor_ms']
    out=result(p,g);risk=100*(p['stop']-entry)/entry
    return dict(item,status='MEASURED',outcome=out,initial_risk_pct=risk,
                fee_slippage_only_r=out['fee_slippage_only_pct']/risk if out['fee_slippage_only_pct'] is not None else None,
                holding_hours=(out['exit']['at_ms']-start)/HOUR if out['exit'] else None,
                first_missing_ms=g['missing_ms'])


def run_symbol(job):
    cache,hour_info,minutes=job;cache=Path(cache);symbol=hour_info['symbol'];month=hour_info['month']
    info,rows=archive(cache,symbol,month,'1h',offline=True)
    if info['status']!='OK' or info['sha256']!=hour_info['sha256']:raise ValueError('HOUR_HASH_CHANGED:'+symbol)
    counts={m:Counter() for m in engine.MODELS};active={};inside={};plans=[]
    for i in range(168,len(rows)):
        bar=rows[i];hist=rows[i-168:i+1]
        for model in engine.MODELS:
            if model in active:
                # A missing hour does not count as a consecutive reset hour.
                if i and rows[i-1]['open_ms']+HOUR!=bar['open_ms']:inside[model]=0
                inside[model]=inside.get(model,0)+1 if bar['close']>=active[model]['trigger'] else 0
                if inside[model]>=engine.CONFIG['reset_inside_hours']:del active[model]
            p=engine.assess(symbol,hist,bar['close_ms']+1000,model)
            counts[model][p['reason']]+=1
            if p['stage']!='READY':continue
            if model in active:counts[model]['EPISODE_SUPPRESSED']+=1;continue
            active[model]=p;inside[model]=0;plans.append(p)
    requested=sorted({m for p in plans for m in months_between((p['observed_ms']//MINUTE+1)*MINUTE,
                                    (p['observed_ms']//MINUTE+1)*MINUTE+168*HOUR)})
    path=[];sources=[]
    for m in requested:
        expected=minutes.get((symbol,m))
        if not expected:
            sources.append(dict(symbol=symbol,month=m,status='NO_VERIFIED_MINUTE_ARCHIVE'));continue
        meta,part=archive(cache,symbol,m,'1m',offline=True)
        if meta['status']!='OK' or meta['sha256']!=expected:raise ValueError('MINUTE_HASH_CHANGED:'+symbol)
        path+=part;sources.append(dict(symbol=symbol,month=m,status='OK',sha256=expected))
    stamps=[b['open_ms'] for b in path]
    if any(b<=a for a,b in zip(stamps,stamps[1:])):raise ValueError('MINUTE_ORDER_OR_DUPLICATE')
    records=[dict(plan=p,audit=audit_one(p,path,stamps)) for p in plans]
    return dict(symbol=symbol,month=month,records=records,counts={m:dict(c) for m,c in counts.items()},sources=sources)


def trade_stats(records):
    ratios=[r['fee_slippage_only_r'] for r in records if r['fee_slippage_only_r'] is not None]
    return dict(count=len(ratios),positive=sum(x>0 for x in ratios),
                positive_pct=100*sum(x>0 for x in ratios)/len(ratios) if ratios else None,
                mean_fee_only_r=statistics.mean(ratios) if ratios else None,
                median_fee_only_r=statistics.median(ratios) if ratios else None,
                full_net_known=0)


def summarize(records):
    out={}
    for model in engine.MODELS:
        group=[r for r in records if r['plan']['model']==model];a=[r['audit'] for r in group]
        measured=[r for r in a if r['status']=='MEASURED']
        complete=[r for r in measured if r['outcome']['status']=='COMPLETE']
        closed_incomplete=[r for r in measured if r['outcome']['plan_closed'] and r['outcome']['status']!='COMPLETE']
        out[model]=dict(candidates=len(group),symbols=len({r['symbol'] for r in a}),
             statuses=dict(Counter(r['status'] for r in a)),
             path_states=dict(Counter(r['outcome']['status'] for r in measured)),
             complete_path_conditional=trade_stats(complete),
             closed_incomplete_path_conditional=trade_stats(closed_incomplete),
             in_plan_2r_complete=sum(r['outcome']['in_plan_mfe_lower_pct']>=2*r['initial_risk_pct'] for r in complete),
             unknown_or_open_plan_count=sum(r['status'] not in ('ENTRY_INVALID',) and
                                           (not r.get('outcome') or not r['outcome']['plan_closed']) for r in a),
             activation_eligible=False,crypto_only=False,independent_validation=False)
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--month',choices=['2026-08','2026-09'],required=True);p.add_argument('--workers',type=int,default=4)
    args=p.parse_args();here=Path(__file__).parent
    out=here/(args.month+'-SUMMARY.json')
    if out.exists():raise ValueError('OUTPUT_EXISTS')
    aug=load_evidence('2026-08-coverage.json.gz')
    sep=load_evidence('2026-10-22-hourly-download-confirmed.json.gz')['archives']
    minute=load_evidence('2026-10-22-minute-download-complete.json.gz')['archives']
    minutes={(r['symbol'],r['month']):r['sha256'] for r in minute if r['status']=='OK'}
    hours=aug if args.month=='2026-08' else sep
    jobs=[(str(args.cache),r,minutes) for r in hours if r['status']=='OK']
    all_records=[];sources=[];counts={m:Counter() for m in engine.MODELS}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run_symbol,j) for j in jobs]
        for i,future in enumerate(as_completed(futures),1):
            x=future.result();all_records+=x['records'];sources+=x['sources']
            for m,c in x['counts'].items():counts[m].update(c)
            if i%100==0:print(json.dumps(dict(symbols_done=i,total=len(jobs),candidates=len(all_records))),flush=True)
    all_records.sort(key=lambda r:(r['plan']['confirmation_ms'],r['plan']['symbol'],r['plan']['model']))
    report=dict(version=engine.VERSION,config_hash=engine.CONFIG_HASH,month=args.month,
         code_sha256=hashlib.sha256((ROOT/'long_short_short_retest.py').read_bytes()).hexdigest(),
         runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         protocol_sha256=hashlib.sha256((here/'PROTOCOL.md').read_bytes()).hexdigest(),
         hour_source_symbols=len(jobs),hour_official_checksums_verified=args.month=='2026-09',
         preserved_hour_hashes_verified=True,minute_official_checksums_verified=True,
         network_requests=0,crypto_only=False,independent_validation=False,live_deployed=False,
         activation_eligible=False,summary=summarize(all_records),counts={m:dict(c) for m,c in counts.items()},
         minute_source_states=dict(Counter(r['status'] for r in sources)),records=all_records,minute_sources=sources)
    out.write_text(json.dumps({k:v for k,v in report.items() if k not in ('records','minute_sources')},indent=2)+'\n')
    (here/(args.month+'-EPISODES.json.gz')).write_bytes(gzip.compress(json.dumps(report).encode(),mtime=0))
    print(json.dumps(report['summary']),flush=True)


if __name__=='__main__':main()
