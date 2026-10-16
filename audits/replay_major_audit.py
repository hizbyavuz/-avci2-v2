"""Bounded retrospective RULE-COVERAGE check, not a strategy backtest.

Current liquidity cohort creates survivorship/selection bias. Uses idealized
closed-15m price, with no historical delivery delay, spread or fresh-quote test.
Do not call reconstructed READY a delivered signal or subsequent gain profit.
"""
import bisect
import collections
import datetime as dt
import json
import os
import pathlib
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import long_short_major_move as motor

ROOT=pathlib.Path(os.environ.get('MAJOR_AUDIT_DATA_DIR', str(pathlib.Path(__file__).resolve().parent/'major_move_20261016'/'data')))


def main():
    universe=json.loads((ROOT/'universe.json').read_text());cache={};errors=[]
    for t in universe:
        s=t['symbol']
        try:
            fs={}
            for tf in ('15m','1h','4h'):
                rows=json.loads((ROOT/'market'/(s+'_'+tf+'.json')).read_text())['bars']
                fs[tf]=[[int(b[0]),*[float(b[i]) for i in (1,2,3,4,5)],int(b[6])] for b in rows]
            cache[s]=fs
        except Exception:errors.append(s)
    ends={s:{tf:[b[6] for b in bars] for tf,bars in fs.items()} for s,fs in cache.items()}
    def cut(s,at):
        out={}
        for tf,n in [('15m',120),('1h',120),('4h',220)]:
            i=bisect.bisect_left(ends[s][tf],at)
            out[tf]=cache[s][tf][max(0,i-n):i]
        return out
    end=int(dt.datetime.fromisoformat('2026-10-16T17:30:00+00:00').timestamp()*1000)
    counts=collections.Counter();ready=[];hourly=[];near=[]
    for at in range(end-24*3600000,end+1,900000):
        btc=cut('BTCUSDT',at);cc=collections.Counter()
        for t in universe:
            s=t['symbol']
            if s not in cache:continue
            fs=cut(s,at)
            if not fs['15m']:continue
            p=motor.assess(s,fs,fs['15m'][-1][4],float(t['quoteVolume']),at+500,btc)
            counts[p['reason']]+=1;cc[p['reason']]+=1
            if p['stage']=='READY':ready.append(p)
            if p['stage']=='WATCH':near.append({k:p.get(k) for k in ('symbol','direction','model','observed_ms','price','trigger','stop','entry_limit')})
        hourly.append({'at_ms':at,'reasons':dict(cc)})
    result=dict(note=__doc__,symbols=len(cache),missing=errors,cutoffs=len(hourly),
                reason_counts=dict(counts),ready=ready,watch=near,per_cutoff=hourly)
    (ROOT/'replay.json').write_text(json.dumps(result,ensure_ascii=False))
    print(json.dumps({'symbols':len(cache),'missing':len(errors),'cutoffs':len(hourly),
                      'counts':dict(counts),'ready_count':len(ready),'watch_count':len(near),
                      'ready_symbols':dict(collections.Counter(x['symbol'] for x in ready))}))
    for p in ready:
        print(json.dumps({k:p.get(k) for k in ('symbol','direction','model','observed_ms','price','trigger','stop','risk_pct','structural_room_pct')}))


if __name__=='__main__':main()
