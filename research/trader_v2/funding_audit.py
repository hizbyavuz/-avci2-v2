"""Attach actual-settlement funding intervals to all August plans, offline."""
import argparse
from collections import Counter
import csv
from datetime import datetime,timezone
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
from long_short_funding_bounds import measure,VERSION,MINUTE


class Archives:
    def __init__(self,cache,records):
        self.cache=cache;self.records={(r['symbol'],r['interval'],r['month']):r for r in records}

    @functools.lru_cache(maxsize=8)
    def read(self,symbol,kind,month):
        info=self.records.get((symbol,kind,month))
        if not info or info['status']!='OK':raise ValueError('SOURCE_ARCHIVE_MISSING')
        expected='/markPriceKlines/' if kind=='1m' else '/fundingRate/'
        if expected not in info['url']:raise ValueError('WRONG_PRICE_SOURCE')
        body=(self.cache/f'{symbol}-{kind}-{month}.zip').read_bytes()
        if hashlib.sha256(body).hexdigest()!=info['sha256']:raise ValueError('SOURCE_HASH_CHANGED')
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            if len(z.namelist())!=1:raise ValueError('ARCHIVE_MEMBER_COUNT')
            with z.open(z.namelist()[0]) as f:
                csvrows=csv.reader(io.TextIOWrapper(f));header=next(csvrows)
                if kind=='fundingRate':
                    if header!=['calc_time','funding_interval_hours','last_funding_rate']:
                        raise ValueError('INVALID_FUNDING_HEADER')
                    return [dict(time_ms=int(r[0]),interval_hours=float(r[1]),rate=float(r[2])) for r in csvrows]
                result={}
                for r in csvrows:
                    t=int(r[0]);value=(float(r[3]),float(r[2]))
                    if t%MINUTE or int(r[6])!=t+MINUTE-1:raise ValueError('INVALID_MARK_MINUTE')
                    if t in result and result[t]!=value:raise ValueError('CONFLICTING_MARK_MINUTE')
                    result[t]=value
                return result


def next_month(dt):return dt.replace(year=dt.year+(dt.month==12),month=dt.month%12+1)


def attach(row,reader):
    out={k:row[k] for k in ('symbol','direction','confirmation_ms','status')}
    if row['status']!='MEASURED':return dict(out,funding_status='ENTRY_NOT_MEASURED')
    try:
        begin=datetime.fromtimestamp(row['reference_ms']/1000,timezone.utc)
        end=datetime.fromtimestamp(row['outcome']['exit']['time_upper_ms']/1000,timezone.utc)
        month=begin.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
        coverage_start=int(month.timestamp()*1000);rates=[];marks={}
        while month<=end:
            stamp=month.strftime('%Y-%m')
            rates+=reader.read(row['symbol'],'fundingRate',stamp)
            month=next_month(month)
        # A fully covered hold with no settlement does not require any mark.
        mark_months={datetime.fromtimestamp(r['time_ms']/1000,timezone.utc).strftime('%Y-%m') for r in rates
                     if row['reference_ms']<=r['time_ms']<=row['outcome']['exit']['time_upper_ms']}
        for stamp in sorted(mark_months):marks.update(reader.read(row['symbol'],'1m',stamp))
        result=measure(row['direction'],row['entry_price'],row['reference_ms'],row['outcome']['exit'],
                       row['outcome']['fee_slippage_only_pct'],rates,marks,(coverage_start,int(month.timestamp()*1000)-1))
        out.update(funding_status='BOUNDED',result=result,
                   net_low_r=result['fee_slippage_and_funding_net_low_pct']/row['initial_risk_pct'],
                   net_high_r=result['fee_slippage_and_funding_net_high_pct']/row['initial_risk_pct'])
    except (ValueError,KeyError,OSError,TypeError) as exc:out.update(funding_status='UNKNOWN',reason=str(exc))
    return out


def summary(records):
    known=[r for r in records if r['funding_status']=='BOUNDED']
    return dict(total=len(records),states=dict(Counter(r['funding_status'] for r in records)),
        unknown_reasons=dict(Counter(r.get('reason') for r in records if r['funding_status']=='UNKNOWN')),
        bounded_positive=sum(r['result']['sign_under_cost_bounds']=='POSITIVE' for r in known),
        bounded_negative=sum(r['result']['sign_under_cost_bounds']=='NEGATIVE' for r in known),
        bounded_uncertain_or_zero=sum(r['result']['sign_under_cost_bounds']=='UNCERTAIN_OR_ZERO' for r in known),
        positive_pct_conditional=100*sum(r['result']['sign_under_cost_bounds']=='POSITIVE' for r in known)/len(known) if known else None,
        mean_net_low_pct=statistics.mean(r['result']['fee_slippage_and_funding_net_low_pct'] for r in known) if known else None,
        mean_net_high_pct=statistics.mean(r['result']['fee_slippage_and_funding_net_high_pct'] for r in known) if known else None,
        mean_net_low_r=statistics.mean(r['net_low_r'] for r in known) if known else None,
        mean_net_high_r=statistics.mean(r['net_high_r'] for r in known) if known else None,
        median_net_low_r=statistics.median(r['net_low_r'] for r in known) if known else None,
        exact_full_net=False,crypto_only=False,portfolio_simulated=False)


def validate_report(manifest,report,allow_partial):
    key=lambda r:(r['symbol'],r['interval'],r['month'])
    expected={key(r):r for r in manifest['archives']}
    actual={key(r):r for r in report['archives']}
    if len(expected)!=len(manifest['archives']) or len(actual)!=len(report['archives']):
        raise ValueError('DUPLICATE_ARCHIVE_KEY')
    if expected.keys()!=actual.keys():raise ValueError('INCOMPLETE_DOWNLOAD_REPORT')
    if any(actual[k]['url']!=v['url'] for k,v in expected.items()):raise ValueError('ARCHIVE_URL_CHANGED')
    unresolved=sum(r['status'] not in ('OK','NOT_PUBLISHED') for r in report['archives'])
    if unresolved and not allow_partial:raise ValueError('DOWNLOAD_ERRORS_REQUIRE_EXPLICIT_PARTIAL_MODE')
    return unresolved


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--download-report',type=Path,help='Default: FUNDING_COST_DOWNLOAD.json; may use a provenance-preserving offline recovery report.')
    parser.add_argument('--allow-partial',action='store_true',help='Keep every candidate; missing costs remain UNKNOWN.')
    args=parser.parse_args()
    here=Path(__file__).parent
    request=(here/'FUNDING_COST_REQUEST.json').read_bytes();manifest=json.loads(request)
    report_path=args.download_report or here/'FUNDING_COST_DOWNLOAD.json'
    report=json.loads(report_path.read_text())
    if report['manifest_sha256']!=hashlib.sha256(request).hexdigest():raise ValueError('WRONG_MANIFEST')
    unresolved=validate_report(manifest,report,args.allow_partial)
    source=ROOT/'research/swing_v1/results/2026-08-full-minute-audit.json';body=source.read_bytes()
    if hashlib.sha256(body).hexdigest()!=manifest['source_sha256']:raise ValueError('OUTCOMES_CHANGED')
    reader=Archives(args.cache,report['archives']);rows=json.loads(body)['records'];results=[]
    for i,row in enumerate(sorted(rows,key=lambda r:(r['symbol'],r['reference_ms']))):
        results.append(attach(row,reader))
        if (i+1)%100==0:print(json.dumps(dict(done=i+1,total=len(rows))),flush=True)
    keyed={(r['symbol'],r['confirmation_ms'],r['direction']):r for r in results}
    if len(keyed)!=len(results):raise ValueError('DUPLICATE_OUTCOME_KEY')
    with gzip.open(here/'EPISODES.json.gz','rt',encoding='utf-8') as f:episodes=json.load(f)['records']
    summary_={}
    for side in ('LONG','SHORT'):
        summary_[side]={}
        for variant in ('BASE','STRUCTURE','PARTICIPATION','CONTEXT'):
            keys=[(r['symbol'],r['confirmation_ms'],r['direction']) for r in episodes
                  if r['month']=='2026-08' and r['direction']==side and r['decision']['variants'][variant]['pass_candidate']]
            summary_[side][variant]=summary([keyed[k] for k in keys])
        common=[(r['symbol'],r['confirmation_ms'],r['direction']) for r in episodes
                if r['month']=='2026-08' and r['direction']==side and r['context_complete']]
        summary_[side]['BASE_COMMON_CONTEXT']=summary([keyed[k] for k in common])
    out=dict(version=VERSION,summary=summary_,source_sha256=hashlib.sha256(body).hexdigest(),
             cost_coverage='PARTIAL' if unresolved or any(r['funding_status']=='UNKNOWN' for r in results) else 'COMPLETE_BOUNDED',
             unresolved_archive_count=unresolved,download_counts=report['counts'],
             source_download_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
             partial_mode=args.allow_partial,all_candidate_count=len(results),
             assumptions=['0.30% total fee/slippage remains unchanged.','Mark-minute bounds, not exact settlement marks.',
                          'Funding archive coverage and interval consistency checked; historical contract lifecycle unresolved.',
                          'No retuning, no new exit rule, no portfolio or Telegram execution simulation.'],records=results)
    path=here/'FUNDED_COMPARISON.json'
    if path.exists():raise ValueError('OUTPUT_EXISTS')
    path.write_text(json.dumps({k:v for k,v in out.items() if k!='records'},indent=2)+'\n')
    with gzip.open(here/'FUNDING_EPISODES.json.gz','wt',encoding='utf-8') as f:json.dump(out,f)
    print(json.dumps(summary_))


if __name__=='__main__':main()
