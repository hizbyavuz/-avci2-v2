"""One frozen validation run, with resumable journals and no live publishing."""
import argparse,bisect,csv,gzip,hashlib,json,math,statistics,threading
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from functools import lru_cache
from pathlib import Path
import numpy as np
from long_short_swing import CONFIG,CONFIG_HASH,geometry
from swing_validation_data import Archive,START,END,HOUR,DAY,MINUTE,iso,ms,months,read,save,get
from swing_validation_engine import candle,screen,control_symbol,control_plan,replay,select_positions,day_stats,comparison

class Data:
    def __init__(self,root,sources):
        self.root=Path(root);self.archive=Archive(self.root/'archive',sources)
    def array(self,s):return np.load(self.root/'prepared'/(s+'.npz'))['bars']
    @lru_cache(maxsize=128)
    def minute_day(self,s,day):
        raw=self.archive.klines(s,iso(day)[:10],interval='1m',daily=True)
        return [dict(open_ms=int(r[0]),close_ms=int(r[6]),open=float(r[1]),high=float(r[2]),low=float(r[3]),close=float(r[4])) for r in raw]
    def minute_hour(self,s,t):return [r for r in self.minute_day(s,t//DAY*DAY) if t<=r['open_ms']<t+HOUR]
    def entry(self,s,t):
        rows=self.minute_day(s,t//DAY*DAY);i=bisect.bisect_left([r['open_ms'] for r in rows],t)
        return rows[i]['open'] if i<len(rows) and rows[i]['open_ms']==t else None
    @lru_cache(maxsize=128)
    def funding_month(self,s,m):
        p=self.root/'funding_mark'/(s+'-'+m+'.json.gz')
        if p.exists():return read(p)
        start=ms(m+'-01');end=min(ms(list(months(start,start+33*DAY))[1]+'-01'),END)
        raw=self.archive.funding(s,m);source='archive'
        events=[dict(time=int(r[0]),rate=float(r[2]),interval_hours=float(r[1])) for r in raw]
        if not events:
            source='official_REST';cursor=start
            while cursor<end:
                body=get('https://www.binance.com/fapi/v1/fundingRate',dict(symbol=s,startTime=cursor,endTime=end-1,limit=1000))
                rr=json.loads(body)
                if not isinstance(rr,list):raise ValueError('BAD_FUNDING')
                if not rr:break
                for r in rr:
                    if r['symbol']!=s:raise ValueError('FUNDING_SYMBOL_MISMATCH')
                    events.append(dict(time=int(r['fundingTime']),rate=float(r['fundingRate']),mark=float(r['markPrice']) if r.get('markPrice') else None))
                nxt=int(rr[-1]['fundingTime'])+1
                if nxt<=cursor:raise ValueError('FUNDING_PAGINATION')
                cursor=nxt
                if len(rr)<1000:break
        if not events:
            out=dict(status='NO_ACTUAL_FUNDING',events=[],source=source);save(p,out);return out
        need=[f for f in events if not f.get('mark')]
        marks={}
        if need:
            rr=self.archive.klines(s,m,kind='markPriceKlines')
            if not rr and m==iso(END)[:7]:
                for d in sorted({f['time']//DAY*DAY for f in need}):rr+=self.archive.klines(s,iso(d)[:10],daily=True,kind='markPriceKlines')
            marks={int(r[0]):float(r[1]) for r in rr}
        for f in events:
            if f.get('mark'):f['mark_source']='REST_SETTLEMENT'
            else:f['mark']=marks.get(f['time']//HOUR*HOUR);f['mark_source']='ARCHIVE_MARK_HOURLY_OPEN'
        events=sorted(events,key=lambda f:f['time'])
        if len({f['time'] for f in events})!=len(events):raise ValueError('DUPLICATE_FUNDING')
        out=dict(status='OBSERVED_HISTORY',events=events,source=source)
        save(p,out);return out
    def funding(self,s,entry,exit_):
        lo=entry;hi=exit_['time_upper_ms'];events=[];missing=[]
        # Neighboring settlement establishes coverage around a short holding.
        for m in months(max(START,lo-8*HOUR),min(END,hi+8*HOUR+1)):
            data=self.funding_month(s,m);events+=data['events']
            if not data['events'] and ms(m+'-01')<=hi and lo<ms(list(months(ms(m+'-01'),ms(m+'-01')+33*DAY))[1]+'-01'):missing.append(m)
        events=sorted({f['time']:f for f in events}.values(),key=lambda f:f['time'])
        between=[f for f in events if lo<=f['time']<=hi]
        issues=[]
        if missing:issues+=['FUNDING_MONTH_MISSING:'+m for m in missing]
        # Do not pretend a sparse export certifies missing payments. The
        # archive records the applicable interval; REST gaps above8h are suspect.
        for a,b in zip(events,events[1:]):
            if a['time']<hi and b['time']>lo and b['time']-a['time']>b.get('interval_hours',8)*HOUR+2000:issues.append('FUNDING_GAP')
        before=[f for f in events if f['time']<=lo];after=[f for f in events if f['time']>=hi]
        if not before and (not events or events[0]['time']-lo>8*HOUR):issues.append('FUNDING_START_UNCOVERED')
        if not after and (not events or hi-events[-1]['time']>8*HOUR):issues.append('FUNDING_END_UNCOVERED')
        cost=0.;sources=Counter()
        for f in between:
            if not f.get('mark') or f['mark']<=0:issues.append('SETTLEMENT_MARK_MISSING');continue
            v=100*f['rate']*f['mark']/exit_['entry_reference'];sources[f['mark_source']]+=1
            ambiguous=(exit_['kind']=='STOP' and exit_['at_ms']<=f['time']<=hi) or lo<=f['time']<lo+1000
            cost+=max(0.,v) if ambiguous else v
        return dict(cost_pct=cost if not issues else None,observed_cost_pct=cost,issues=sorted(set(issues)),events=len(between),sources=dict(sources))

def write_csv(path,rows):
    keys=sorted({k for r in rows for k in r})
    with Path(path).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        for r in rows:w.writerow({k:json.dumps(v,separators=(',',':')) if isinstance(v,(dict,list)) else v for k,v in r.items()})

def discover(root,out):
    data=Data(root,[]);manifest=read(Path(root)/'manifest.json')
    if manifest['errors'] or manifest['unknown']:raise ValueError('DATA_PREPARATION_INCOMPLETE')
    path=Path(out)/'signals.json.gz'
    if path.exists():return read(path)
    syms=sorted(r['symbol'] for r in manifest['symbols'] if r.get('rows',0)>0)
    presence=np.zeros((len(syms),(END-START)//HOUR+1),dtype=bool);all_records=[];ready=defaultdict(set);counts={}
    for i,s in enumerate(syms):
        arr=data.array(s);rr,rt,cc=screen(s,arr,START,END)
        all_records+=rr;counts[s]=cc
        for t in rt:ready[t].add(s)
        ids=((arr[:,0]+HOUR-START)//HOUR).astype(int);valid=(ids>=0)&(ids<presence.shape[1])&(arr[:,7]>0)
        presence[i,ids[valid]]=True
        if (i+1)%25==0 or i+1==len(syms):print(json.dumps(dict(stage='signals',done=i+1,total=len(syms),signals=len(all_records))),flush=True)
    all_records.sort(key=lambda r:(r['signal_time'],r['symbol']))
    for p in all_records:
        ti=(p['signal_time']-START)//HOUR
        eligible=[syms[i] for i in np.flatnonzero(presence[:,ti])]
        p['k1_symbol']=control_symbol(p['symbol'],p['signal_time'],eligible,ready[p['signal_time']]);p['k2_symbol']='BTCUSDT'
    save(path,all_records);save(Path(out)/'signal_counts.json',counts)
    save(Path(out)/'control_manifest.json',dict(seed='SWING_V1_INDEPENDENT_20261023',signal_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),population='all_crypto_currently_observed_closed_hour_positive_volume',uses_future=False))
    return all_records

def plan_entries(root,out,sources,signals,workers):
    data=Data(root,sources);path=Path(out)/'entries.jsonl';done={}
    if path.exists():
        for l in path.read_text().splitlines():r=json.loads(l);done[r['id']]=r
    def one(p):
        p=dict(p);price=data.entry(p['symbol'],p['entry_ms'])
        if price is None:return dict(p,status='ENTRY_DATA_MISSING')
        reason=geometry(p,price)
        if reason:return dict(p,status='ENTRY_INVALID',entry=price,reason=reason)
        p.update(status='PLAN',entry=price,risk_pct=100*(price-p['stop'])/price)
        return p
    todo=[p for p in signals if p['id'] not in done]
    with path.open('a') as f,ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(one,p):p for p in todo}
        for i,j in enumerate(as_completed(jobs),1):
            p=jobs[j]
            try:r=j.result()
            except Exception as e:print(json.dumps(dict(stage='entry_error',id=p['id'],error=str(e)[:140])),flush=True);continue
            done[r['id']]=r;f.write(json.dumps(r,separators=(',',':'))+'\n');f.flush()
            if i%100==0 or i==len(jobs):print(json.dumps(dict(stage='entries',done=len(done),total=len(signals))),flush=True)
    if len(done)!=len(signals):raise ValueError('ENTRY_DOWNLOADS_INCOMPLETE_RETRY_SAME_RUN')
    return [done[p['id']] for p in signals]

def simulate(root,out,sources,entries,workers):
    path=Path(out)/'outcomes.jsonl';done={};data=Data(root,sources)
    if path.exists():
        for l in path.read_text().splitlines():r=json.loads(l);done[(r['group'],r['id'])]=r
    jobs_by_symbol=defaultdict(list)
    for p in entries:
        if p['status']!='PLAN':continue
        for group,s in [('MAIN',p['symbol']),('K1',p['k1_symbol']),('K2','BTCUSDT')]:
            if (group,p['id']) not in done:jobs_by_symbol[s].append((group,p))
    def one(s,jobs):
        if not s:return [dict(id=p['id'],group=g,status='NO_CONTROL',signal_time=p['signal_time']) for g,p in jobs]
        arr=data.array(s);hours={int(r[0]):candle(r) for r in arr};results=[]
        for group,original in jobs:
            p=dict(original)
            if group!='MAIN':
                price=data.entry(s,p['entry_ms'])
                if price is None:results.append(dict(id=p['id'],group=group,symbol=s,status='ENTRY_DATA_MISSING',signal_time=p['signal_time']));continue
                p=control_plan(p,s,price)
            r=replay(p,hours,data.minute_hour,END)
            r.update(id=original['id'],symbol=s,group=group,signal_time=p['signal_time'],stop=p['stop'],atr_1h=p['atr_1h'],quote_volume_24h=p['quote_volume_24h'])
            if r['status']=='CLOSED':
                ee=dict(r['exit'],entry_reference=p['entry'])
                funding=data.funding(s,p['entry_ms'],ee);r['funding']=funding;r['fee_only_pct']=r['gross_pct']-.30
                if funding['cost_pct'] is not None:
                    r.update(net_pct=r['gross_pct']-.30-funding['cost_pct'],stress_pct=r['gross_pct']-.60-funding['cost_pct'],cost100_pct=r['gross_pct']-1.-funding['cost_pct'])
                    r.update(net_r=r['net_pct']/r['risk_pct'],stress_r=r['stress_pct']/r['risk_pct'],cost100_r=r['cost100_pct']/r['risk_pct'])
                else:r['net_status']='UNVERIFIED_FUNDING'
            results.append(r)
        return results
    with path.open('a') as f,ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(one,s,pp):s for s,pp in jobs_by_symbol.items()}
        for i,j in enumerate(as_completed(jobs),1):
            try:rr=j.result()
            except Exception as e:print(json.dumps(dict(stage='outcome_error',symbol=jobs[j],error=type(e).__name__+':'+str(e)[:160])),flush=True);continue
            for r in rr:done[(r['group'],r['id'])]=r;f.write(json.dumps(r,separators=(',',':'))+'\n')
            f.flush();print(json.dumps(dict(stage='outcomes',symbols_done=i,total=len(jobs),records=len(done))),flush=True)
    expected=sum(p['status']=='PLAN' for p in entries)*3
    if len(done)!=expected:raise ValueError('OUTCOME_DOWNLOADS_INCOMPLETE_RETRY_SAME_RUN')
    return list(done.values())

def metrics(rows):
    measured=[r for r in rows if r.get('net_r') is not None]
    n=len(measured)
    if not n:return dict(n=0,records=len(rows),unverified=len(rows),mean_net_r=None,mean_net_pct=None,daily=day_stats([]))
    ordered=sorted(measured,key=lambda r:(r['exit']['time_upper_ms'],r['id']));equity=peak=dd=0.;streak=longest=0
    for r in ordered:
        equity+=r['net_r'];peak=max(peak,equity);dd=max(dd,peak-equity);streak=streak+1 if r['net_r']<0 else 0;longest=max(longest,streak)
    return dict(n=n,records=len(rows),unverified=len(rows)-n,profitable_pct=100*sum(r['net_r']>0 for r in measured)/n,
        mean_net_r=statistics.mean(r['net_r'] for r in measured),mean_net_pct=statistics.mean(r['net_pct'] for r in measured),total_r=sum(r['net_r'] for r in measured),max_drawdown_r=dd,longest_loss_streak=longest,
        median_holding_hours=statistics.median(r['holding_hours'] for r in measured),mean_r_cost060=statistics.mean(r['stress_r'] for r in measured),mean_r_cost100=statistics.mean(r['cost100_r'] for r in measured),mean_pct_cost060=statistics.mean(r['stress_pct'] for r in measured),mean_pct_cost100=statistics.mean(r['cost100_pct'] for r in measured),daily=day_stats(measured),states=dict(Counter(r['status'] for r in rows)))

def summarize(rows,selection):
    main={r['id']:r for r in rows if r['group']=='MAIN'};kk={g:{r['id']:r for r in rows if r['group']==g} for g in ['K1','K2']}
    chosen=[main[i] for i in selection['selected']];skipped=[main[i] for i in selection['skipped']];m=metrics(chosen)
    years={str(y):metrics([r for r in chosen if iso(r['signal_time'])[:4]==str(y)]) for y in range(2020,2027)}
    monthly={month:metrics([r for r in chosen if iso(r['signal_time']).startswith(month)]) for month in months(START,END)}
    score=defaultdict(float)
    for r in chosen:
        if r.get('net_r') is not None:score[r['symbol']]+=r['net_r']
    best3=sorted(score,key=lambda s:(-score[s],s))[:3];best10={r['id'] for r in sorted([r for r in chosen if r.get('net_r') is not None],key=lambda r:-r['net_r'])[:10]}
    comparisons={g:comparison(chosen,kk[g]) for g in kk}
    return dict(all=m,peak_open=selection['peak'],years=years,months=monthly,last12=metrics([r for r in chosen if r['signal_time']>=ms('2025-10-23')]),august=metrics([r for r in chosen if iso(r['signal_time']).startswith('2026-08')]),excluding_august=metrics([r for r in chosen if not iso(r['signal_time']).startswith('2026-08')]),best3=best3,without_best3=metrics([r for r in chosen if r['symbol'] not in best3]),without_best10=metrics([r for r in chosen if r['id'] not in best10]),skipped=metrics(skipped),controls={g:metrics([kk[g][i] for i in selection['selected'] if i in kk[g]]) for g in kk},comparisons=comparisons)

def report(out,entries,rows,manifest):
    out=Path(out);plans=[p for p in entries if p['status']=='PLAN'];main={r['id']:r for r in rows if r['group']=='MAIN'}
    selections={name:select_positions(plans,main,limit) for name,limit in [('unlimited',None),('limited',5)]}
    groups={k:summarize(rows,v) for k,v in selections.items()};g=groups['limited'];a=g['all']
    positive=lambda x:x is not None and x>0
    checks=dict(at_least_200=a['n']>=200,mean_net_r_positive=positive(a.get('mean_net_r')),cost060_positive=positive(a.get('mean_r_cost060')),without_best3_positive=positive(g['without_best3'].get('mean_net_r')),beats_k1=g['comparisons']['K1']['significant'],beats_k2=g['comparisons']['K2']['significant'],positive_year_majority=sum(positive(v.get('total_r')) for v in g['years'].values())>len(g['years'])/2)
    summary=dict(status='GEÇTİ' if all(checks.values()) else 'BAŞARISIZ',checks=checks,groups=groups,entry_states=dict(Counter(r['status'] for r in entries)),parameter_trials=0,config_hash=CONFIG_HASH,period=[iso(START),iso(END)],data_manifest=manifest)
    if a['unverified'] or any(g['comparisons'][k]['unpaired'] for k in ['K1','K2']):summary['data_warning']='Eksik/pending sonuçlar vardır; tam net kapsam doğrulanmadı.';summary['status']='BAŞARISIZ' if not all(checks.values()) else 'DOĞRULANMADI'
    save(out/'summary.json',summary);save(out/'selection.json',selections)
    selected_ids=set(selections['limited']['selected']);skipped_ids=set(selections['limited']['skipped'])
    for r in rows:
        r['limited_selected']=r['id'] in selected_ids;r['limited_skip_reason']='LİMİT NEDENİYLE ATLANDI' if r['id'] in skipped_ids else ''
    write_csv(out/'trades.csv',rows);write_csv(out/'signals.csv',entries)
    lines=['# Swing V1 bağımsız doğrulama',f"\n**{summary['status']}**",'\nKurallar değişmedi; parametre denemesi0. Futures verisi. Canlı/Telegram kapalı.',f"\nDönem: {iso(START)} – {iso(END)} (son sınır hariç).",'\n| Kohort | n | Kârlı% | Ortalama getiri% | Ortalama R | Toplam R | Maks.DD R | Kayıp serisi | Ortanca saat | Gün | Gün ort.R | Gün SE |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    def fmt(x):return f'{x:.6f}' if isinstance(x,(int,float)) else 'doğrulanmadı'
    for name,gg in groups.items():
        for sub in ['all','skipped','last12','without_best3','without_best10','august','excluding_august']:
            z=gg[sub];d=z['daily'];values=[z.get(k) for k in ['n','profitable_pct','mean_net_pct','mean_net_r','total_r','max_drawdown_r','longest_loss_streak','median_holding_hours']]+[d['days'],d['mean'],d['se']]
            lines.append('|'+name+'/'+sub+'|'+'|'.join(fmt(v) for v in values)+'|')
    lines+=['\n## Masraf ve kontroller','\n| Kohort | %0,30 ort.R | %0,60 ort.R | %1,00 ort.R |','|---|---:|---:|---:|']
    for name,gg in groups.items():lines.append('|'+name+'|'+'|'.join(fmt(gg['all'].get(k)) for k in ['mean_net_r','mean_r_cost060','mean_r_cost100'])+'|')
    for name,gg in groups.items():
        lines+=[f'\n### {name} kontroller','\n| Kontrol | n | Ort.R | Eş | Gün | Günlük farkR | SE | 2SE üstü? |','|---|---:|---:|---:|---:|---:|---:|---|']
        for k in ['K1','K2']:
            c=gg['comparisons'][k];d=c['daily_r'];z=gg['controls'][k]
            lines.append(f"|{k}|{z['n']}|{fmt(z.get('mean_net_r'))}|{c['pairs']}|{d['days']}|{fmt(d['mean'])}|{fmt(d['se'])}|{c['significant']}|")
    lines+=['\n## Yıl ve ay sonuçları','\n| Kohort | Dönem | n | Ortalama R | Toplam R |','|---|---|---:|---:|---:|']
    for name,gg in groups.items():
        for period,z in list(gg['years'].items())+list(gg['months'].items()):lines.append(f"|{name}|{period}|{z['n']}|{fmt(z.get('mean_net_r'))}|{fmt(z.get('total_r'))}|")
    lines+=['\n## Veri ve yorum sınırları','\n- Günler eşit ağırlıklıdır; gün sayısı tam istatistiksel bağımsızlık kanıtı değildir. Yedi günlük örtüşme için HAC7 SE JSON içinde ayrıca vardır.','- Maksimum düşüş kapanmış işlemlerin kronolojik1R toplamıdır; sermaye veya gün içi portföy drawdownı değildir.','- K1/K2 aynı zamanlı eşleştirilmiş karşılaştırmadır; ayrı kontrol portföyü değildir. Sonuç/veri eksik diye başka kontrol seçilmedi.','- +2R takip eşiğidir; kâr hedefi yok. Stop aynı saat içinde geçmişe taşınmaz.','- Funding eksi/eksik değerleri sıfıra çevrilmez. Mark source ve veri sorunları her işlem CSVsinde.','- Ağustos2026 önceden görülmüş geliştirme ayıdır. Eski evrenin birebir replikasyonu ayrı rapordadır.','\nBaşarı kapıları:\n```json',json.dumps(checks,ensure_ascii=False,indent=2),'```']
    z=g['years']['2022'];lines.append(f"\n2022: {z['n']} doğrulanmış limitli işlem, toplam {fmt(z.get('total_r'))}R, ortalama {fmt(z.get('mean_net_r'))}R. Yıl sonucu ayrıca gösterildi; genel başarı kapıları değiştirilmedi.")
    if summary.get('data_warning'):lines.append('\n**'+summary['data_warning']+'**')
    (out/'report.md').write_text('\n'.join(lines))
    return summary

def run(root,out,sources,workers,stage):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    files=['long_short_swing.py','long_short_swing_outcomes.py','swing_validation_engine.py','swing_validation_data.py','swing_validation_backtest.py','research/swing_validation_v1/PROTOCOL.md']
    seal={f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files};seal['data_manifest']=hashlib.sha256((Path(root)/'manifest.json').read_bytes()).hexdigest()
    lock=out/'sealed_run.json'
    if lock.exists():
        old=read(lock)
        if old['seal']!=seal:raise ValueError('SOURCE_OR_DATA_CHANGED')
        if old['status']=='COMPLETE':raise ValueError('COMPLETED_NO_RERUN')
    else:save(lock,dict(status='RUNNING',seal=seal))
    signals=discover(root,out)
    if stage=='signals':return
    entries=plan_entries(root,out,sources,signals,workers)
    if stage=='entries':return
    rows=simulate(root,out,sources,entries,workers)
    summary=report(out,entries,rows,read(Path(root)/'manifest.json'))
    save(lock,dict(status='COMPLETE',seal=seal,report_hash=hashlib.sha256((out/'report.md').read_bytes()).hexdigest()))
    print(json.dumps(dict(status=summary['status'],limited=summary['groups']['limited']['all']),ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--out',required=True);p.add_argument('--source',action='append',default=[]);p.add_argument('--workers',type=int,default=12);p.add_argument('--stage',choices=['signals','entries','all'],default='all');a=p.parse_args();run(a.data,a.out,a.source,a.workers,a.stage)
