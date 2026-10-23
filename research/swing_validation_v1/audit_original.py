"""Original August cohort replication; never rewrite original evidence."""
import argparse,gzip,json,hashlib,statistics,sys
from collections import Counter,defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from swing_validation_data import HOUR,DAY,MINUTE,ms,iso,read,save,bars
from swing_validation_backtest import Data,write_csv
from swing_validation_engine import screen,select_positions
from long_short_swing import geometry,CONFIG_HASH
from long_short_swing_outcomes import initial,advance,result

def main(root,out,sources,workers):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);data=Data(root,sources)
    evidence=Path(__file__).resolve().parents[1]/'swing_v1/evidence'
    original=read(evidence/'2026-08-screen.json.gz');old=read(evidence/'2026-08-full-minute-audit.json.gz')
    pp=[r['plan'] for r in original['records'] if r['plan']['direction']=='LONG'];by_symbol=defaultdict(list)
    for p in pp:by_symbol[p['symbol']].append(p)
    old_map={(r['symbol'],r['confirmation_ms']):r for r in old['records'] if r['direction']=='LONG'}
    journal=out/'original_replay.jsonl';done={}
    if journal.exists():
        for l in journal.read_text().splitlines():r=json.loads(l);done[r['id']]=r
    def one(s,plans):
        rr=data.archive.klines(s,'2026-08');arr=bars(rr)
        got=screen(s,arr,ms('2026-08-01'),ms('2026-09-01')+1)[0]
        expected={(p['symbol'],p['confirmation_ms']) for p in plans}
        actual={(p['symbol'],p['confirmation_ms']) for p in got}
        if actual!=expected:raise ValueError('ORIGINAL_CANDIDATE_MISMATCH '+s)
        result_rows=[]
        for p in plans:
            key=f"{s}:{p['confirmation_ms']+1}"
            if key in done:continue
            start=(p['observed_ms']//MINUTE+1)*MINUTE;end=start+168*HOUR;rows=[]
            for day in range(start//DAY*DAY,(end-1)//DAY*DAY+DAY,DAY):rows.extend(data.minute_day(s,day))
            path=[r for r in rows if start<=r['open_ms']<end];entry=path[0]['open'] if path and path[0]['open_ms']==start else None
            r=dict(id=key,symbol=s,signal_time=p['confirmation_ms']+1,entry_ms=start,quote_volume_24h=p['quote_volume_24h'],old_status=old_map[(s,p['confirmation_ms'])]['status'])
            if entry is None:r.update(status='ENTRY_DATA_MISSING')
            else:
                reason=geometry(p,entry)
                if reason:r.update(status='ENTRY_INVALID',reason=reason)
                else:
                    state=advance(p,initial(p,start,entry),path)
                    if state['cursor_ms']<end:state['missing_ms']=state['cursor_ms']
                    res=result(p,state);risk_pct=100*state['initial_risk']/entry
                    r.update(status='MEASURED',outcome=res,entry=entry,risk_pct=risk_pct,fee_only_pct=res['fee_slippage_only_pct'],fee_only_r=res['fee_slippage_only_pct']/risk_pct if res['fee_slippage_only_pct'] is not None else None,exit=res['exit'])
            oldr=old_map[(s,p['confirmation_ms'])]
            r['matches_old_status']=r['status']==oldr['status']
            r['matches_old_result']=r['status']=='ENTRY_INVALID' and oldr['status']=='ENTRY_INVALID' or (r.get('outcome')==oldr.get('outcome') and r.get('entry')==oldr.get('entry_price'))
            result_rows.append(r)
        return result_rows
    with journal.open('a') as f,ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(one,s,ps):s for s,ps in by_symbol.items()}
        errors=[]
        for i,j in enumerate(as_completed(jobs),1):
            try:rr=j.result()
            except Exception as e:errors.append(dict(symbol=jobs[j],error=str(e)));print(json.dumps(errors[-1]),flush=True);continue
            for r in rr:done[r['id']]=r;f.write(json.dumps(r,separators=(',',':'))+'\n')
            f.flush()
            if i%10==0 or i==len(jobs):print(json.dumps(dict(stage='original_reproduction',symbols_done=i,total=len(jobs),records=len(done))),flush=True)
    if len(done)!=len(pp):save(out/'original_errors.json',errors);raise ValueError('REPLICATION_INCOMPLETE')
    rows=list(done.values());valid=[r for r in rows if r['status']=='MEASURED'];outcomes={r['id']:r for r in valid}
    groups={}
    for name,limit in [('unlimited',None),('limited',5)]:
        chosen=select_positions(valid,outcomes,limit);rr=[outcomes[i] for i in chosen['selected']];measured=[r for r in rr if r['outcome']['status']=='COMPLETE' and r['fee_only_r'] is not None]
        groups[name]=dict(n=len(measured),positive=sum(r['fee_only_pct']>0 for r in measured),mean_fee_only_pct=statistics.mean(r['fee_only_pct'] for r in measured) if measured else None,mean_fee_only_r=statistics.mean(r['fee_only_r'] for r in measured) if measured else None,mean_pct_cost060=statistics.mean(r['fee_only_pct']-.3 for r in measured) if measured else None,mean_pct_cost100=statistics.mean(r['fee_only_pct']-.7 for r in measured) if measured else None,peak_open=chosen['peak'],skipped=len(chosen['skipped']),funding='NOT_INCLUDED_ORIGINAL_REPLICATION_ONLY')
    summary=dict(config_hash=CONFIG_HASH,candidates=len(pp),matching_status=sum(r['matches_old_status'] for r in rows),matching_result=sum(r['matches_old_result'] for r in rows),groups=groups,states=dict(Counter(r['status'] for r in rows)),scope='Original unclassified August cohort; not independent proof; fee-only replication',source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [evidence/'2026-08-screen.json.gz',evidence/'2026-08-full-minute-audit.json.gz']})
    save(out/'original_summary.json',summary);write_csv(out/'original_trades.csv',rows)
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--out',required=True);p.add_argument('--source',action='append',default=[]);p.add_argument('--workers',type=int,default=12);a=p.parse_args();main(a.data,a.out,a.source,a.workers)
