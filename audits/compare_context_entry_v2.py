"""Reproducible in-sample coverage replay; NOT a profitability backtest."""
import argparse
import bisect
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import long_short_major_move as v2


def run(data_dir):
    universe=json.loads((data_dir/'universe.json').read_text());cache={};ends={};hashes={}
    for item in universe:
        s=item['symbol'];cache[s]={};ends[s]={}
        for tf in ('15m','1h','4h'):
            file=data_dir/'market'/f'{s}_{tf}.json';raw=file.read_bytes()
            hashes[file.name]=hashlib.sha256(raw).hexdigest()
            data=json.loads(raw)['bars']
            cache[s][tf]=[[int(b[0]),*[float(b[j]) for j in (1,2,3,4,5)],int(b[6])] for b in data]
            ends[s][tf]=[b[6] for b in cache[s][tf]]
    def cut(symbol,at):
        result={}
        for tf,n in [('15m',120),('1h',120),('4h',220)]:
            i=bisect.bisect_left(ends[symbol][tf],at)
            result[tf]=cache[symbol][tf][max(0,i-n):i]
        return result
    end=int(dt.datetime.fromisoformat('2026-10-16T17:30:00+00:00').timestamp()*1000)
    counts={'v1_1':Counter(),'v2':Counter()};models=Counter();stages={'v1_1':Counter(),'v2':Counter()}
    first={};cases=[];transition=Counter()
    for at in range(end-24*3600000,end+1,900000):
        btc=cut('BTCUSDT',at)
        for item in universe:
            s=item['symbol'];data=cut(s,at);price=data['15m'][-1][4]
            old=v2.baseline.assess(s,data,price,float(item['quoteVolume']),at+500,btc)
            new=v2.assess(s,data,price,float(item['quoteVolume']),at+500,btc)
            transition[old['stage']+' -> '+new['stage']]+=1
            for name,p in [('v1_1',old),('v2',new)]:
                counts[name][p['reason']]+=1;stages[name][p['stage']]+=1
                if p['stage']=='READY':
                    if name=='v2':models[p['model']]+=1
                    case=dict(strategy=name,symbol=s,at_ms=at,side=p['direction'],model=p['model'],price=p['price'],
                              stop=p['stop'],trigger=p['trigger'],risk_pct=p['risk_pct'],room_pct=p['structural_room_pct'],
                              entry_kind=p.get('entry_kind'),context_4h=p['features'].get('context_4h'),old_reason=old['reason'])
                    cases.append(case);first.setdefault((name,s),case)
    return dict(version=v2.VERSION,config=v2.CONFIG,config_hash=v2.CONFIG_HASH,
        baseline_source_sha256=hashlib.sha256(Path(v2.baseline.__file__).read_bytes()).hexdigest(),
        evidence='IN_SAMPLE_COVERAGE_ONLY_NO_EXECUTIONS_NO_PROFIT_CLAIM',
        limitations=['Current liquidity cohort; survivorship and selection bias.',
                     'Audit day already informed design; not out-of-sample.',
                     '15m closing price replaces contemporaneous executable quote; no spreads, fills or Telegram delay.',
                     'Counts do not demonstrate profitability; forward 1m path observation is separate.'],
        start_ms=end-24*3600000,end_ms=end,universe=len(universe),evaluations=sum(counts['v2'].values()),
        reasons={k:dict(v) for k,v in counts.items()},stages={k:dict(v) for k,v in stages.items()},
        transitions=dict(transition),ready_models=dict(models),distinct_first_events=list(first.values()),
        ready_cases=cases,data_hashes=hashes)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--data-dir',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();result=run(args.data_dir);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('ready_cases','distinct_first_events','data_hashes','config')},ensure_ascii=False))
    print('distinct_first_events',len(result['distinct_first_events']))
