"""Reproducible official-archive screening; no orders, credentials or Telegram.

Run from repo root. All catalogue pages are required. Hourly forward movement
is NOT executable profit. Optional minute replay uses a fixed hash sample.
"""
import argparse
import concurrent.futures
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from long_short_swing import CONFIG,CONFIG_HASH,VERSION,HOUR,MINUTE,assess,atr,geometry
from long_short_swing_outcomes import initial,advance,result

ARCHIVE='https://data.binance.vision/'
CATALOG='https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'


def fetch(url):
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url,timeout=25) as response:return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code==404:raise
            if attempt:raise
        except (OSError,TimeoutError):
            if attempt:raise


def symbols():
    ns={'s':'http://s3.amazonaws.com/doc/2006-03-01/'};out=[];marker=''
    while True:
        query=urllib.parse.urlencode(dict(delimiter='/',prefix='data/futures/um/monthly/klines/',marker=marker))
        root=ET.fromstring(fetch(CATALOG+'?'+query))
        prefixes=[x.text for x in root.findall('s:CommonPrefixes/s:Prefix',ns)]
        out += [x.split('/')[-2] for x in prefixes]
        if root.findtext('s:IsTruncated',namespaces=ns)!='true':break
        new=root.findtext('s:NextMarker',namespaces=ns) or prefixes[-1]
        if new==marker:raise ValueError('CATALOG_PAGINATION_STALLED')
        marker=new
    return sorted(set(s for s in out if s.endswith('USDT')))


def archive(cache,symbol,month,interval='1h',offline=False):
    name=f'{symbol}-{interval}-{month}.zip'
    relative=f'data/futures/um/monthly/klines/{symbol}/{interval}/{name}'
    path=cache/name
    info=dict(symbol=symbol,month=month,interval=interval,url=ARCHIVE+urllib.parse.quote(relative,safe='/'))
    try:
        if path.exists():body=path.read_bytes()
        elif offline:
            info.update(status='NOT_CACHED');return info,[]
        else:
            body=fetch(info['url']);path.write_bytes(body)
        info.update(status='OK',sha256=hashlib.sha256(body).hexdigest(),bytes=len(body))
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            with z.open(z.namelist()[0]) as f:
                rows=[]
                for r in csv.reader(io.TextIOWrapper(f)):
                    if not r or not r[0].isdigit():continue
                    rows.append(dict(open_ms=int(r[0]),close_ms=int(r[6]),
                        open=float(r[1]),high=float(r[2]),low=float(r[3]),close=float(r[4]),
                        quote_volume=float(r[7]),taker_buy_quote=float(r[10])))
        info['rows']=len(rows);return info,rows
    except urllib.error.HTTPError as exc:
        info.update(status='NOT_PUBLISHED' if exc.code==404 else 'DOWNLOAD_ERROR',error=str(exc));return info,[]
    except (OSError,ValueError,zipfile.BadZipFile) as exc:
        info.update(status='DOWNLOAD_ERROR',error=type(exc).__name__);return info,[]


def raw_forward(rows,i,direction):
    # Screening proxy at the next hour's open. It is never labelled a fill or
    # a paper-plan return, and is separate from strict post-decision minute entry.
    future=rows[i+1:i+169]
    if len(future)<168 or any(b['open_ms']!=rows[i]['open_ms']+(j+1)*HOUR for j,b in enumerate(future)):
        return dict(status='IMMATURE_OR_GAP')
    sign=1 if direction=='LONG' else -1;entry=future[0]['open']
    favorable=[100*sign*((b['high'] if sign==1 else b['low'])/entry-1) for b in future]
    adverse=[-100*sign*((b['low'] if sign==1 else b['high'])/entry-1) for b in future]
    return dict(status='COMPLETE',proxy_entry=entry,raw_mfe_pct=max(0.,max(favorable)),
                raw_mae_pct=max(0.,max(adverse)),
                directional_close_168h_pct=100*sign*(future[-1]['close']/entry-1),
                reached_30=any(x>=30 for x in favorable),
                first_10_hour=next((j+1 for j,x in enumerate(favorable) if x>=10),None))


def screen(data):
    records=[];counts={};panel={};eligible=0;by_time={}
    for symbol,rows in sorted(data.items()):
        active={};inside={}
        for i in range(CONFIG['history_hours']-1,len(rows)):
            b=rows[i];history=rows[i-168:i+1]
            for side,past in list(active.items()):
                sign=1 if side=='LONG' else -1
                inside[side]=inside.get(side,0)+1 if sign*(b['close']-past['trigger'])<=0 else 0
                if inside[side]>=CONFIG['reset_inside_hours']:del active[side]
            p=assess(symbol,history,b['close_ms']+1000)
            counts[p['reason']]=counts.get(p['reason'],0)+1
            if p['reason'] not in ('DATA_GAP','INVALID_BAR_TIME','VOLUME_OR_TAKER_MISSING','ILLIQUID') and p.get('quote_volume_24h',0)>=CONFIG['min_quote_volume_24h']:
                eligible+=1
                key=(symbol,b['close_ms']);volatility=atr(history)/b['close']
                panel[key]=dict(i=i,q=p['quote_volume_24h'],volatility=volatility,
                                direction=p.get('direction') if p['stage']=='READY' else None)
                by_time.setdefault(b['close_ms'],[]).append(symbol)
            if p['stage']!='READY':continue
            if p['direction'] in active:
                counts['EPISODE_SUPPRESSED']=counts.get('EPISODE_SUPPRESSED',0)+1;continue
            active[p['direction']]=p;inside[p['direction']]=0
            records.append(dict(plan=p,raw=raw_forward(rows,i,p['direction'])))
    # Match on contemporaneous log quote volume and hourly ATR/price, never on
    # future returns. Fixed hash chooses one of five nearest available peers.
    for record in records:
        p=record['plan'];stamp=p['confirmation_ms'];own=panel.get((p['symbol'],stamp))
        candidates=[]
        if own:
            for s in by_time.get(stamp,[]):
                x=panel[(s,stamp)]
                if s==p['symbol'] or x['direction']==p['direction'] or min(x['q'],x['volatility'])<=0:continue
                score=abs(math.log(x['q']/own['q']))+abs(math.log(x['volatility']/own['volatility']))
                candidates.append((score,s,x['i']))
        if not candidates:
            record['control']=dict(status='NO_MATCH');continue
        nearest=sorted(candidates)[:5]
        seed=f"swing-v1:{p['symbol']}:{stamp}:{p['direction']}"
        score,s,i=nearest[int(hashlib.sha256(seed.encode()).hexdigest(),16)%len(nearest)]
        record['control']=dict(symbol=s,match_distance=score,raw=raw_forward(data[s],i,p['direction']))
    return dict(records=records,rejections=counts,evaluated_coin_hours=sum(counts.values())-counts.get('EPISODE_SUPPRESSED',0),
                eligible_coin_hours=eligible)


def summarize(records):
    out={}
    for side in ('LONG','SHORT'):
        group=[r for r in records if r['plan']['direction']==side]
        mature=[r for r in group if r['raw']['status']=='COMPLETE']
        paired=[r for r in mature if r['control'].get('raw',{}).get('status')=='COMPLETE']
        out[side]=dict(episodes=len(group),mature_raw=len(mature),immature_or_gap=len(group)-len(mature),matched_mature=len(paired),
            raw_reach_30_pct=100*sum(r['raw']['reached_30'] for r in mature)/len(mature) if mature else None,
            paired_signal_raw_reach_30_pct=100*sum(r['raw']['reached_30'] for r in paired)/len(paired) if paired else None,
            paired_control_raw_reach_30_pct=100*sum(r['control']['raw']['reached_30'] for r in paired)/len(paired) if paired else None,
            median_raw_mfe_pct=statistics.median(r['raw']['raw_mfe_pct'] for r in mature) if mature else None,
            median_raw_mae_pct=statistics.median(r['raw']['raw_mae_pct'] for r in mature) if mature else None)
    return out


def minute_check(records,cache,month,n,offline=False):
    eligible=[r for r in records if r['raw']['status']=='COMPLETE']
    sample=sorted(eligible,key=lambda r:hashlib.sha256(f"{r['plan']['symbol']}:{r['plan']['confirmation_ms']}".encode()).hexdigest())[:n]
    checked=[]
    for record in sample:
        p=record['plan'];info,rows=archive(cache,p['symbol'],month,'1m',offline=offline)
        # assess at close+1000ms; next full minute begins one minute after the
        # new hour, never at the just-finished signal candle's close.
        start=(p['observed_ms']//MINUTE+1)*MINUTE
        path=[b for b in rows if start<=b['open_ms']<start+168*HOUR]
        entry=path[0]['open'] if path and path[0]['open_ms']==start else None
        reason=geometry(p,entry) if entry is not None else 'ENTRY_DATA_MISSING'
        item=dict(symbol=p['symbol'],direction=p['direction'],confirmation_ms=p['confirmation_ms'],archive=info)
        if reason:item.update(status='ENTRY_INVALID_OR_MISSING',reason=reason)
        else:
            g=advance(p,initial(p,start,entry),path)
            if g['cursor_ms']<g['expires_ms']:g['missing_ms']=g['cursor_ms']
            item.update(status='MEASURED',outcome=result(p,g))
        checked.append(item)
    return checked


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--months',nargs='+',default=['2026-08','2026-09'])
    parser.add_argument('--workers',type=int,default=12);parser.add_argument('--minute-sample',type=int,default=8)
    parser.add_argument('--offline',action='store_true',help='No network: use the saved catalogue and cached archives only')
    parser.add_argument('--catalog',type=Path,help='Saved catalogue required in offline mode; defaults to OUT/catalog.json')
    args=parser.parse_args();args.cache.mkdir(parents=True,exist_ok=True);args.out.mkdir(parents=True,exist_ok=True)
    syms=json.loads((args.catalog or args.out/'catalog.json').read_text()) if args.offline else symbols()
    (args.out/'catalog.json').write_text(json.dumps(syms))
    prereg=Path(__file__).with_name('PREREGISTRATION.md').read_bytes()
    manifest=dict(version=VERSION,config_hash=CONFIG_HASH,config=CONFIG,
                  prereg_sha256=hashlib.sha256(prereg).hexdigest(),catalog_symbols=len(syms),
                  source='official Binance USD-M monthly archives',months=args.months,
                  classification='TECHNICAL_REPLAY_NOT_PROFITABILITY_VALIDATION',
                  universe_version='UNCLASSIFIED_USDT_ARCHIVE_V1',
                  crypto_only=False,offline=args.offline,
                  simple_momentum_baseline='NOT_YET_RUN',major_v3_baseline='NOT_YET_RUN',
                  full_cost_net='UNKNOWN_FUNDING',minute_sample_per_month=args.minute_sample)
    (args.out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('FROZEN '+json.dumps(manifest),flush=True)
    for month in args.months:
        data={};coverage=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(archive,args.cache,s,month,offline=args.offline):s for s in syms}
            for count,f in enumerate(concurrent.futures.as_completed(futures),1):
                info,rows=f.result();coverage.append(info)
                if rows:data[info['symbol']]=rows
                if count%100==0:print(f'DOWNLOAD {month} {count}/{len(syms)} available={len(data)}',flush=True)
        (args.out/f'{month}-coverage.json').write_text(json.dumps(sorted(coverage,key=lambda x:x['symbol']),indent=2))
        print(f'SCREEN {month} symbols={len(data)}',flush=True)
        report=screen(data)
        report.update(month=month,version=VERSION,config_hash=CONFIG_HASH,summary=summarize(report['records']),
                      universe_version='UNCLASSIFIED_USDT_ARCHIVE_V1',crypto_only=False,
                      archive_statuses={state:sum(x['status']==state for x in coverage) for state in ('OK','NOT_PUBLISHED','DOWNLOAD_ERROR','NOT_CACHED')})
        (args.out/f'{month}-screen.json').write_text(json.dumps(report,indent=2))
        print('SCREEN_RESULT '+json.dumps(dict(month=month,summary=report['summary'],archive_statuses=report['archive_statuses'])),flush=True)
        checked=minute_check(report['records'],args.cache,month,args.minute_sample,offline=args.offline)
        (args.out/f'{month}-minute-check.json').write_text(json.dumps(checked,indent=2))
        print(f'MINUTE_CHECK {month} n={len(checked)}',flush=True)


if __name__=='__main__':main()
