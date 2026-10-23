"""Read-only checks of sealed outcomes; no strategy rerun or parameter choice."""
import argparse,gzip,hashlib,json,statistics,sys
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from long_short_swing import assess,geometry,HOUR,MINUTE,CONFIG_HASH
from swing_validation_engine import candle
from swing_validation_data import read,save,iso

def main(root,out):
    root=Path(root);out=Path(out);seal=read(out/'sealed_run.json')
    assert seal['status']=='COMPLETE'
    for name,digest in seal['seal'].items():
        p=root/'manifest.json' if name=='data_manifest' else Path(name)
        assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,('SOURCE_CHANGED',name)
    signals=read(out/'signals.json.gz');entries=[json.loads(l) for l in (out/'entries.jsonl').read_text().splitlines()];rows=[json.loads(l) for l in (out/'outcomes.jsonl').read_text().splitlines()]
    # Stable sample selected by hash rather than winners or convenient symbols.
    sample=sorted(signals,key=lambda p:hashlib.sha256(p['id'].encode()).hexdigest())[:1000];by=defaultdict(list)
    for p in sample:by[p['symbol']].append(p)
    checked=0
    for s,pp in by.items():
        arr=np.load(root/'prepared'/(s+'.npz'))['bars'];times=arr[:,0]
        for p in pp:
            i=np.searchsorted(times,p['signal_time']-HOUR)
            assert int(times[i])==p['signal_time']-HOUR
            hist=[candle(r) for r in arr[i-168:i+1]];q=assess(s,hist,p['observed_ms'])
            for k in ['stage','stop','trigger','atr_1h','origin_price','volume_multiple','obstacle','quote_volume_24h','config_hash']:
                assert q[k]==p[k],('SIGNAL_MISMATCH',s,k)
            assert all(b['close_ms']<p['observed_ms'] for b in hist)
            checked+=1
    for p in entries:
        assert p['entry_ms']==p['signal_time']+MINUTE
        if p['status']=='PLAN':assert geometry(p,p['entry']) is None
    main={r['id']:r for r in rows if r['group']=='MAIN'};plan={p['id']:p for p in entries if p['status']=='PLAN'}
    net_count=0
    for r in rows:
        if r['status']=='CLOSED':
            assert abs(r['gross_pct']-100*(r['exit']['price']/r['entry']-1))<1e-9
            assert r['exit']['at_ms']>=r['entry_ms']
            assert r['exit']['time_upper_ms']<r['expires_ms']
            st=r['stop']
            for update in r['updates']:assert update['stop']>=st;st=update['stop']
        if r.get('net_r') is not None:
            assert abs(r['net_r']-(r['gross_pct']-.3-r['funding']['cost_pct'])/r['risk_pct'])<1e-9
            assert abs(r['stress_r']-(r['gross_pct']-.6-r['funding']['cost_pct'])/r['risk_pct'])<1e-9
            assert abs(r['cost100_r']-(r['gross_pct']-1.-r['funding']['cost_pct'])/r['risk_pct'])<1e-9
            net_count+=1
    sel=read(out/'selection.json')['limited'];events=[]
    for i in sel['selected']:
        p=plan[i];r=main[i];end=r['exit']['time_upper_ms']+1 if r.get('exit') else p['entry_ms']+168*HOUR
        events.extend([(p['entry_ms'],1),(end,-1)])
    active=peak=0
    for t,n in sorted(events):active+=n;peak=max(peak,active);assert 0<=active<=5
    ds=defaultdict(list)
    for i in sel['selected']:
        r=main[i]
        if r.get('net_r') is not None:ds[iso(r['signal_time'])[:10]].append(r['net_r'])
    means=[statistics.mean(v) for v in ds.values()];reported=read(out/'summary.json')['groups']['limited']['all']['daily']
    assert len(means)==reported['days']
    if means:assert abs(statistics.mean(means)-reported['mean'])<1e-10
    result=dict(status='PASS',real_signal_checks=checked,entries=len(entries),outcomes=len(rows),net_accounting_checks=net_count,limited_peak=peak,signal_days=len(means),config_hash=CONFIG_HASH)
    save(out/'verification.json',result);print(json.dumps(result),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--out',required=True);a=p.parse_args();main(a.data,a.out)
