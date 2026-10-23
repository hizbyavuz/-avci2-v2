"""Post-run arithmetic and causality audit; never changes or reruns outcomes."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from hourly_motor_engine import Candle,Series,HOUR,DAY,daily_direction,evaluate
from hourly_motor_backtest import load_symbol


def verify(root,out):
    root=Path(root);out=Path(out);records=json.loads((out/'candidates.json').read_text())
    rows=[json.loads(line) for line in (out/'outcomes.jsonl').read_text().splitlines()]
    per=defaultdict(list)
    for r in records:per[r['symbol']].append(r)
    btc=Series([Candle(**b) for b in load_symbol(root,'BTCUSDT')['daily']],DAY)
    counts=dict(signals=0,plans=0,closed_outcomes=0,real_prefix_checks=0,quota_checks=0);quota=set()
    def same(a,b):
        if not math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-10):raise AssertionError((a,b))
    for s,rr in per.items():
        x=load_symbol(root,s);h=Series([Candle(*b) for b in x['hourly']],HOUR);d=Series([Candle(**b) for b in x['daily']],DAY)
        for n,r in enumerate(rr):
            i=h.index(r['time']);b=h.bars[i];side=r['side'];sign=1 if side=='LONG' else -1
            level=max(v.high for v in h.bars[i-24:i]) if sign==1 else min(v.low for v in h.bars[i-24:i])
            mean=sum(v.volume for v in h.bars[i-20:i])/20
            same(level,r['level']);same(b.volume/mean,r['volume_ratio']);assert sign*(b.close-level)>0 and b.volume>=1.5*mean
            same(b.low-.5*h.atr[i] if sign==1 else b.high+.5*h.atr[i],r['stop']);counts['signals']+=1
            if r['status']=='PLAN':
                same(r['entry'],h.bars[i+1].open);same(r['target'],r['entry']+sign*1.5*r['risk'])
                key=(r['group'],s,r['time']//DAY);assert key not in quota;quota.add(key)
                counts['plans']+=1;counts['quota_checks']+=1
            if n==0 or n%100==0:
                at=r['time'];direction=daily_direction(s,d,btc,at)
                prefix=daily_direction(s,[v for v in d.bars if v.close_ms<=at],[v for v in btc.bars if v.close_ms<=at],at)
                assert direction==prefix
                full=evaluate(s,h,direction,at,r['group']);short=evaluate(s,h.bars[:i+1],prefix,at,r['group'])
                assert full==short;counts['real_prefix_checks']+=1
    for r in rows:
        if r['status']!='CLOSED':continue
        sign=1 if r['side']=='LONG' else -1
        gross=sign*100*(r['exit']/r['entry']-1)
        same(r['gross_pct'],gross);same(r['net_pct'],gross-.30-r['funding_pct']);same(r['net_r'],r['net_pct']/r['risk_pct'])
        assert r['entry_ms']<=r['exit_ms']<=r['deadline_ms'];counts['closed_outcomes']+=1
    result=dict(status='PASS',counts=counts,notes='Independent prior-window arithmetic, plan symmetry arithmetic, per-day quota, prefix invariance on real candles, all outcome net accounting. No outcomes recalculated or thresholds changed.')
    (out/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--out',required=True);a=p.parse_args();verify(a.data,a.out)
