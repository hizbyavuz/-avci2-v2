"""Join every frozen episode with as-of context; report all fixed variants.

Offline only. Requires the complete download log and verifies cached SHA256.
Does not search thresholds, discard failures, or reinterpret raw reach as P&L.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone, timedelta
import functools
import gzip
import hashlib
import io
import json
from pathlib import Path
import statistics
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import long_short_trader_context as engine
from research.swing_v1.replay import archive


class ContextArchive:
    def __init__(self,cache,records):
        self.cache=cache
        self.manifest={(r['symbol'],r['interval'],r['month']):r for r in records}

    @functools.lru_cache(maxsize=64)
    def read(self,symbol,kind,date):
        info=self.manifest.get((symbol,kind,date))
        if not info or info['status']!='OK':raise ValueError('ARCHIVE_UNAVAILABLE_'+kind)
        body=(self.cache/f'{symbol}-{kind}-{date}.zip').read_bytes()
        if hashlib.sha256(body).hexdigest()!=info['sha256']:raise ValueError('ARCHIVE_HASH_CHANGED')
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            if len(z.namelist())!=1:raise ValueError('ARCHIVE_MEMBER_COUNT')
            with z.open(z.namelist()[0]) as f:rows=list(csv.DictReader(io.TextIOWrapper(f)))
        out=[]
        for r in rows:
            if kind=='metrics':
                if r['symbol']!=symbol:raise ValueError('METRICS_SYMBOL_MISMATCH')
                t=int(datetime.fromisoformat(r['create_time']).replace(tzinfo=timezone.utc).timestamp()*1000)
                out.append(dict(time_ms=t,available_ms=t+engine.CONFIG['metrics_delay_ms'],
                    oi_base=float(r['sum_open_interest']),account_ratio=float(r['count_long_short_ratio'])))
            elif kind=='fundingRate':
                t=int(r['calc_time'])
                out.append(dict(time_ms=t,available_ms=t+engine.MINUTE,
                    rate=float(r['last_funding_rate']),interval_hours=float(r['funding_interval_hours'])))
            else:raise ValueError('UNSUPPORTED_ARCHIVE_KIND')
        return out

    def at(self,symbol,decision_ms):
        metrics=[];funding=[];errors={}
        # Request dates were frozen at the confirmation close, not next-hour open.
        close_dt=datetime.fromtimestamp((decision_ms-1000)/1000,timezone.utc)
        for day in (close_dt.date()-timedelta(days=1),close_dt.date()):
            try:metrics+=self.read(symbol,'metrics',str(day))
            except (ValueError,OSError,KeyError) as exc:errors[str(day)]=str(exc)
        try:funding=self.read(symbol,'fundingRate',close_dt.strftime('%Y-%m'))
        except (ValueError,OSError,KeyError) as exc:errors['funding']=str(exc)
        return metrics,funding,errors


def conditional_summary(records):
    result=dict(total_candidates=len(records),missing_context=sum(not r['context_complete'] for r in records),
                crypto_only=False,full_net_known=0,variants={})
    for variant in ('BASE','STRUCTURE','PARTICIPATION','CONTEXT'):
        selected=[r for r in records if r['decision']['variants'][variant]['pass_candidate']]
        measured=[r for r in selected if r.get('plan_outcome',{}).get('status')=='MEASURED']
        fee=[r['plan_outcome']['outcome']['fee_slippage_only_pct'] for r in measured]
        ratios=[r['plan_outcome']['fee_slippage_only_r'] for r in measured]
        raw=[r['raw'] for r in selected if r['raw'].get('status')=='COMPLETE']
        days=Counter(datetime.fromtimestamp(r['confirmation_ms']/1000,timezone.utc).date().isoformat() for r in selected)
        rejected=Counter(reason for r in records for reason in r['decision']['variants'][variant]['reasons'])
        common=[r for r in selected if r['context_complete']]
        common_measured=[r['plan_outcome']['fee_slippage_only_r'] for r in common
                         if r.get('plan_outcome',{}).get('status')=='MEASURED']
        result['variants'][variant]=dict(selected=len(selected),rejection_reasons=dict(rejected),
            days=len(days),max_per_day=max(days.values(),default=0),measured_plans=len(measured),
            invalid_entry=sum(r.get('plan_outcome',{}).get('status')=='ENTRY_INVALID' for r in selected),
            unmeasured_or_unknown=len(selected)-len(measured)-sum(r.get('plan_outcome',{}).get('status')=='ENTRY_INVALID' for r in selected),
            fee_only_positive=sum(x>0 for x in fee),fee_only_negative=sum(x<0 for x in fee),
            fee_only_positive_pct=100*sum(x>0 for x in fee)/len(fee) if fee else None,
            fee_only_mean_pct=statistics.mean(fee) if fee else None,
            fee_only_mean_r=statistics.mean(ratios) if ratios else None,
            fee_only_median_r=statistics.median(ratios) if ratios else None,
            fee_only_profit_factor=sum(x for x in ratios if x>0)/-sum(x for x in ratios if x<0) if any(x<0 for x in ratios) else None,
            common_context_selected=len(common),common_context_measured=len(common_measured),
            common_context_fee_only_mean_r=statistics.mean(common_measured) if common_measured else None,
            raw_complete=len(raw),raw_30_count=sum(x['reached_30'] for x in raw),
            raw_30_pct=100*sum(x['reached_30'] for x in raw)/len(raw) if raw else None,
            actual_portfolio_or_notice_budget=False,full_net_known=0)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--hourly-cache',type=Path,required=True);args=parser.parse_args()
    here=Path(__file__).parent;report=json.loads((here/'DOWNLOAD.json').read_text())
    request_body=(here/'DATA_REQUEST.json').read_bytes();request=json.loads(request_body)
    if report['manifest_sha256']!=hashlib.sha256(request_body).hexdigest():raise ValueError('WRONG_DOWNLOAD_MANIFEST')
    if request['protocol_sha256']!=hashlib.sha256((here/'PROTOCOL.md').read_bytes()).hexdigest():
        raise ValueError('PROTOCOL_CHANGED')
    for source in request['sources']:
        if hashlib.sha256((ROOT/source['path']).read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('FROZEN_EPISODES_CHANGED')
    # The frozen downloader output uses records; retain the exact source report.
    downloaded=report.get('records',report.get('archives',[]))
    if len(downloaded)!=len(request['archives']):raise ValueError('INCOMPLETE_DOWNLOAD_MANIFEST')
    if any(r['status'] not in ('OK','NOT_PUBLISHED') for r in downloaded):raise ValueError('UNRESOLVED_DOWNLOAD_ERRORS')
    reader=ContextArchive(args.cache,downloaded)
    measured=json.loads((ROOT/'research/swing_v1/results/2026-08-full-minute-audit.json').read_text())
    measured={(r['symbol'],r['confirmation_ms'],r['direction']):r for r in measured['records']}
    records=[];hourly={};source_hashes={}
    for filename in ('2026-08-screen.json','2026-09-screen-complete.json'):
        source=ROOT/'research/swing_v1/results'/filename;body=source.read_bytes();data=json.loads(body)
        source_hashes[filename]=hashlib.sha256(body).hexdigest()
        month=filename[:7]
        _,btc=archive(args.hourly_cache,'BTCUSDT',month,offline=True)
        for r in data['records']:
            p=r['plan'];symbol=p['symbol'];key=(symbol,month)
            if key not in hourly:
                info,rows=archive(args.hourly_cache,symbol,month,offline=True)
                if info['status']!='OK':raise ValueError('HOUR_ARCHIVE_UNAVAILABLE')
                hourly[key]=rows
            m,f,errors=reader.at(symbol,p['observed_ms'])
            decision=engine.assess(p,hourly[key],btc,m,f,p['observed_ms'])
            price=decision['price_evidence'];deriv=decision['derivative_evidence']
            critical=(price.get('return_1h_pct'),price.get('closed_4h_return_pct'),price.get('btc_residual_4h_log_pct'),
                      price.get('flow_4h'),deriv.get('oi_change_1h_pct'),deriv.get('oi_change_4h_pct'),
                      deriv.get('funding_rate'),deriv.get('funding_interval_hours'),deriv.get('account_ratio'))
            records.append(dict(symbol=symbol,direction=p['direction'],month=month,
                confirmation_ms=p['confirmation_ms'],decision=decision,source_errors=errors,
                context_complete=all(engine.finite(x) for x in critical),raw=r['raw'],
                plan_outcome=measured.get((symbol,p['confirmation_ms'],p['direction']),{})))
    payload=dict(version=engine.VERSION,config_hash=engine.CONFIG_HASH,
        protocol_sha256=hashlib.sha256((here/'PROTOCOL.md').read_bytes()).hexdigest(),
        source_hashes=source_hashes,records=records,summary={})
    for month in ('2026-08','2026-09'):
        payload['summary'][month]={side:conditional_summary([r for r in records if r['month']==month and r['direction']==side])
                                  for side in ('LONG','SHORT')}
    path=here/'COMPARISON.json'
    if path.exists():raise ValueError('OUTPUT_EXISTS')
    path.write_text(json.dumps({k:v for k,v in payload.items() if k!='records'},indent=2)+'\n')
    with gzip.open(here/'EPISODES.json.gz','wt',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False)
    print(json.dumps(payload['summary']))


if __name__=='__main__':main()
