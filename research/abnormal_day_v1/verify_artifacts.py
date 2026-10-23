"""Independent read-only output audit; never reruns the strategy."""
import csv
import hashlib
import json
import math
from pathlib import Path


def verify(repository):
    repository=Path(repository);root=repository/'research/abnormal_day_v1';out=root/'results'
    seal=json.loads((out/'sealed_run.json').read_text());summary=json.loads((out/'summary.json').read_text())
    checks=0
    def check(condition,label):
        nonlocal checks
        if not condition:raise AssertionError(label)
        checks+=1
    near=lambda a,b:math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-8)
    check(summary['identity']==seal,'sealed result identity')
    for p,sha in seal['code'].items():check(hashlib.sha256((repository/p).read_bytes()).hexdigest()==sha,'source changed: '+p)
    check(hashlib.sha256((root/'universe.json').read_bytes()).hexdigest()==seal['universe_sha256'],'universe changed')
    universe=json.loads((root/'universe.json').read_text())
    for month,rows in universe['months'].items():
        check(len(rows)<=20,'universe size '+month)
        check(all(r['last_used_ms']<=r['selection_ms'] and r['first_observed_ms']<=r['selection_ms']-120*86400000 for r in rows),'universe age/causality '+month)
    rows=list(csv.DictReader((out/'trades.csv').open()))
    for mode in ('limited','unlimited'):
        main=[r for r in rows if r['portfolio']==mode and r['variant']=='main_24h']
        closed=[r for r in main if r.get('net_r')]
        s=summary['results'][mode]['groups']['ALL']['stats']
        check(len(closed)==s['trades'],'CSV/report count '+mode)
        check(near(sum(float(r['net_r']) for r in closed),s['total_net_r']),'CSV/report R '+mode)
        entries={}
        for r in main:
            t=int(r['entry_ms']);entries.setdefault(t,[]).append(r)
            check(near(float(r['risk']),2*float(r['atr'])),'risk2ATR')
            check(near(float(r['risk_usdt']),.01*float(r['entry_equity'])),'capital1percent')
            check(int(r['deadline_ms'])-t==86400000,'24h deadline')
            if not r.get('net_r'):continue
            gross=(1 if r['side']=='LONG' else -1)*100*(float(r['exit'])/float(r['entry'])-1)
            net=gross-.30-float(r['funding_pct'])
            check(near(net,float(r['net_return_pct'])),'net percentage')
            check(near(net/float(r['risk_pct']),float(r['net_r'])),'netR')
            check(near(net-.30,float(r['stress_net_return_pct'])),'cost060')
            check(int(r['exit_ms'])<=int(r['deadline_ms']),'main24h max lifetime')
            if r['exit_reason']=='TIME_24H':check(int(r['exit_ms'])==int(r['deadline_ms']),'exact timed exit')
        for t,group in entries.items():
            check(len({r['symbol'] for r in group})==len(group),'same coin '+str(t))
            if mode=='limited':
                check(len(group)<=5,'5 maximum '+str(t))
                check(max(sum(r['side']==side for r in group) for side in ('LONG','SHORT'))<=3,'3 direction '+str(t))
        control=[r for r in rows if r['portfolio']==mode and r['variant']=='control']
        check(len(control)==len(main),'one matched control per accepted signal')
        by_id={r['id']:r for r in main}
        for r in control:
            m=by_id[r['matched_id']]
            if not r.get('z'):continue
            check(r['symbol']==m['symbol'] and r['side']==m['side'],'matched coin/direction')
            check(abs(float(r['z']))<1,'normal-day control')
    check(json.loads((root/'live_approval.json').read_text())['approved_by_user'] is False,'user approval locked')
    check('schedule:' not in (repository/'.github/workflows/abnormal-day-live.yml').read_text(),'live cron absent')
    return dict(status='PASS',checks=checks,strategy_replayed=False,
                note='Accounting and causality checks do not certify missing market/funding data.')


if __name__=='__main__':
    print(json.dumps(verify(Path(__file__).resolve().parents[2]),indent=2))
