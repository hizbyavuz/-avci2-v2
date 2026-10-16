"""Offline, same-cohort comparison. Never imported by the production worker.

Two predeclared research extensions: directional new-4h-high LONG and
new-4h-low trend-continuation SHORT. Keep <=8% structural risk and <=0.5 ATR
chase. Report the 10% historical-obstacle gate separately, not as proven edge.
This reuses the audit day that motivated the design: NOT out-of-sample evidence.
"""
import argparse
import bisect
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import types

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import long_short_major_move as revised
from audits.compare_major_snapshot import independent

RESEARCH_RULES=dict(version='COVERAGE_RESEARCH_R1_2026_10_16',
    direction='4h and closed-1h 12h, BTC relative, 24h VWAP aligned',
    volume='rolling 1h quote volume >= median prior 24 hourly blocks',
    trigger='closed 15m crosses prior 4h high/low',
    stop='last confirmed 15m pivot plus 0.15 ATR15',max_stop_pct=8.,max_chase_atr15=.5,
    obstacle_gate='reported separately; never an executable recommendation',
    fit_status='DESIGNED_AFTER_SEEING_THIS_DAY_NOT_VALIDATION')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data-dir',type=pathlib.Path,required=True)
    ap.add_argument('--output',type=pathlib.Path,required=True)
    ap.add_argument('--baseline-ref',default='43da26dd9978242c0fe081954ca86b1a90528cb6')
    args=ap.parse_args();root=pathlib.Path(__file__).resolve().parents[1]
    source=subprocess.run(['git','show',args.baseline_ref+':long_short_major_move.py'],cwd=root,
                          check=True,capture_output=True,text=True).stdout
    old=types.ModuleType('frozen_major_move');exec(compile(source,'<frozen-major-move>','exec'),old.__dict__)
    universe=json.loads((args.data_dir/'universe.json').read_text());cache={};ends={}
    for item in universe:
        s=item['symbol'];cache[s]={};ends[s]={}
        for tf in ('15m','1h','4h'):
            bars=json.loads((args.data_dir/'market'/(s+'_'+tf+'.json')).read_text())['bars']
            cache[s][tf]=bars;ends[s][tf]=[int(b[6]) for b in bars]
    def cut(symbol,at):
        raw={};data={}
        for tf,n in [('15m',120),('1h',120),('4h',220)]:
            i=bisect.bisect_left(ends[symbol][tf],at)
            raw[tf]=cache[symbol][tf][max(0,i-n):i]
            data[tf]=[[int(b[0]),*[float(b[j]) for j in (1,2,3,4,5)],int(b[6])] for b in raw[tf]]
        return data,raw
    end=int(dt.datetime.fromisoformat('2026-10-16T17:30:00+00:00').timestamp()*1000)
    transitions=collections.Counter();before=collections.Counter();after=collections.Counter()
    ready_changed=[];watch_removed=[];cases=[]
    for at in range(end-24*3600000,end+1,900000):
        btc,_=cut('BTCUSDT',at)
        for item in universe:
            s=item['symbol'];data,raw=cut(s,at);price=data['15m'][-1][4]
            a=old.assess(s,data,price,float(item['quoteVolume']),at+500,btc)
            b=revised.assess(s,data,price,float(item['quoteVolume']),at+500,btc)
            before[a['reason']]+=1;after[b['reason']]+=1
            transitions[a['stage']+' -> '+b['stage']]+=1
            if a['stage']=='READY' and b['stage']!='READY':ready_changed.append(s)
            if a['stage']=='WATCH' and b['stage']=='REJECTED':
                watch_removed.append(dict(symbol=s,at_ms=at,old_reason=a['reason'],new_reason=b['reason'],
                    price=price,trigger=b.get('trigger'),stop=b.get('stop'),
                    planned_entry=b.get('planned_entry'),planned_risk_pct=b.get('planned_risk_pct'),
                    planned_room_pct=b.get('planned_room_pct')))
            try:revised.validate(data,at+500)
            except ValueError:continue
            i=independent(data,raw,btc)
            if not i.get('side') or i.get('rolling_1h_volume_multiple',0)<1:continue
            sign=1 if i['side']=='LONG' else -1
            crossed=sign*(price-i['trigger'])>0 and sign*(data['15m'][-2][4]-i['trigger'])<=0
            if not crossed or not 0<i['risk_pct']<=8 or i['trigger_distance_atr']>.5:continue
            model='NEW_HIGH_LONG' if sign==1 else 'TREND_CONTINUATION_SHORT'
            room=i['structural_room_pct']
            cases.append(dict(symbol=s,at_ms=at,research_model=model,side=i['side'],price=price,
                trigger=i['trigger'],stop=i['stop'],risk_pct=i['risk_pct'],room_pct=room,
                room_class='UNKNOWN' if room is None else 'BELOW_10' if room<10 else 'AT_LEAST_10',
                original_reason=a['reason'],revised_reason=b['reason']))
    # Count only the first case per coin in this 24h window as an event.
    first={}
    for case in cases:first.setdefault(case['symbol'],case)
    report=dict(research_rules=RESEARCH_RULES,
      research_config_hash=hashlib.sha256(json.dumps(RESEARCH_RULES,sort_keys=True).encode()).hexdigest(),
      baseline_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
      revised_version=revised.VERSION,revised_config_hash=revised.CONFIG_HASH,
      note='Same-day retrospective coverage only. Current liquidity cohort, closed-price reference; no trades, PnL or validated superiority.',
      evaluations=sum(before.values()),before=dict(before),after=dict(after),transitions=dict(transitions),
      old_ready_removed=ready_changed,watch_removed=watch_removed,research_cases=cases,
      distinct_first_events=list(first.values()),
      distinct_counts=dict(collections.Counter(c['research_model'] for c in first.values())),
      distinct_room_counts=dict(collections.Counter(c['room_class'] for c in first.values())))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('research_cases','distinct_first_events','watch_removed')},ensure_ascii=False))
    print('removed_watch_records',len(watch_removed))


if __name__=='__main__':main()
