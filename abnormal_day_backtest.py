"""One fixed abnormal-day experiment and predeclared matched controls."""
import argparse
from bisect import bisect_right
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
import csv
from dataclasses import asdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import statistics
import requests
from trend_breakout_data import Archive,ms,iso,month,months
from abnormal_day_engine import (Candle,DAY,HOUR,RULES,RULES_HASH,DailySeries,evaluate,
    make_plan,emergency_exit,timed_exit,funding_pct,result,choose,select_control)


class Data:
    def __init__(self,root,archive_root):
        self.root=Path(root);self.archive=Archive(archive_root)
        self.manifest=json.loads((self.root/'manifest.json').read_text())
        self.universe=json.loads((self.root/'universe.json').read_text());self.series={};self.events={};self.coverage={}
        for r in self.manifest['symbols']:
            p=self.root/'prepared'/(r['symbol']+'.json')
            if hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256']:raise ValueError('DATA_HASH_CHANGED')
            x=json.loads(p.read_text());s=x['symbol'];self.series[s]=DailySeries([Candle(**b) for b in x['bars']])
            self.events[s]=x['funding'];self.coverage[s]=set(x['funding_coverage'])

    @lru_cache(maxsize=64)
    def hourly_month(self,s,m):
        return {b.open_ms:b for b in self.archive.candles(s,'1h',m)}

    @lru_cache(maxsize=256)
    def hourly_day(self,s,day):
        rows=self.archive.candles(s,'1h',iso(day)[:10],daily=True)
        if not rows:
            p=self.root/'hourly_rest'/f'{s}-{day}.json'
            if p.exists():rows=[Candle(**v) for v in json.loads(p.read_text())]
            else:
                r=requests.get('https://www.binance.com/fapi/v1/klines',params=dict(symbol=s,interval='1h',startTime=day,endTime=day+DAY-1,limit=1000),timeout=30)
                r.raise_for_status();raw=r.json()
                if not isinstance(raw,list):raise ValueError('INVALID_HOURLY_RESPONSE')
                rows=[Candle(int(v[0]),int(v[6])+1,*map(float,v[1:6]),float(v[7])) for v in raw]
                p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps([asdict(v) for v in rows]))
        return {b.open_ms:b for b in rows}

    def hour(self,s,at):
        b=self.hourly_month(s,month(at)).get(at)
        return b or self.hourly_day(s,at//DAY*DAY).get(at)

    def funding(self,p,until):
        missing=[m for m in months(p['entry_ms'],until+1) if m not in self.coverage[p['symbol']]]
        if missing:raise ValueError('MISSING_ACTUAL_FUNDING:'+','.join(missing))
        events=self.events[p['symbol']]
        # Official settlement series must bracket the holding interval without
        # holes larger than the maximum standard 8-hour funding interval.
        times=[int(v['time'])//1000*1000 for v in events]
        lo=bisect_right(times,p['entry_ms']);hi=bisect_right(times,until)
        selected=times[max(0,lo-1):min(len(times),hi+1)]
        if not selected or selected[0]>p['entry_ms'] or selected[-1]<until:
            raise ValueError('FUNDING_BOUNDARY_NOT_BRACKETED')
        if any(b-a>8*HOUR+1000 for a,b in zip(selected,selected[1:])):raise ValueError('FUNDING_SERIES_GAP')
        return funding_pct(p['side'],events,p['entry_ms'],until)

    def outcome(self,p,stop=True):
        if p['deadline_ms']>self.manifest['end']:
            return dict(p,status='PENDING',reason='INSUFFICIENT_FOLLOWUP')
        try:
            event=None
            for at in range(p['entry_ms'],p['deadline_ms'],HOUR):
                b=self.hour(p['symbol'],at)
                if not b or b.close_ms!=at+HOUR or b.volume<=0:raise ValueError('HOURLY_DATA_MISSING_OR_NO_TRADES')
                if stop:
                    event=emergency_exit(p,b)
                    if event:
                        if event['exit_reason']=='STOP' and any(at<int(v['time'])//1000*1000<b.close_ms and int(v['time'])//1000*1000%HOUR for v in self.events[p['symbol']]):
                            raise ValueError('STOP_FUNDING_ORDER_UNVERIFIED')
                        break
            if event is None:
                b=self.hour(p['symbol'],p['deadline_ms'])
                if not b or b.volume<=0:raise ValueError('TIME_EXIT_PRICE_UNVERIFIED')
                event=timed_exit(p,b.open,p['deadline_ms'])
            funding=self.funding(p,event['exit_ms'])
            return dict(p,**event,**result(p,event['exit'],funding),status='CLOSED')
        except Exception as e:
            return dict(p,status='UNVERIFIED',reason=type(e).__name__+':'+str(e)[:130],net_r=None,net_return_pct=None)


def candidates(data):
    signals=[];normals=defaultdict(list);diagnostics=Counter();end=data.manifest['end'];start=data.manifest['start']
    for symbol,series in data.series.items():
        for b in series.bars:
            at=b.close_ms
            if not start<=at<end:continue
            sig=evaluate(symbol,series,at)
            if sig.get('z') is not None and abs(sig['z'])<1:normals[symbol].append(sig)
            members={r['symbol'] for r in data.universe['months'].get(month(at),[])}
            if symbol not in members:continue
            diagnostics[sig['reason']]+=1
            if sig['status']=='SIGNAL':signals.append(sig)
    signals.sort(key=lambda s:(s['time'],s['symbol']))
    matched={f"{s['symbol']}:{s['time']}":select_control(s,normals[s['symbol']]) for s in signals}
    return signals,matched,dict(diagnostics)


def prepare_outcomes(data,signals,matched,path):
    path=Path(path);cached={}
    if path.exists():
        for line in path.read_text().splitlines():
            r=json.loads(line);cached[r['id']]=r
    pending=[s for s in signals if f"{s['symbol']}:{s['time']}" not in cached]
    def one(s):
        id=f"{s['symbol']}:{s['time']}";pair=matched[id]
        row=dict(id=id,signal=s,control_signal=pair)
        try:
            b=data.hour(s['symbol'],s['time'])
            if not b or b.volume<=0:raise ValueError('ENTRY_PRICE_UNVERIFIED')
            p=make_plan(s,b.open,s['time']);row['main']=data.outcome(p)
            row['hours48']=data.outcome(make_plan(s,b.open,s['time'],48))
            row['no_stop']=data.outcome(p,False)
        except Exception as e:row['main']=dict(s,status='UNVERIFIED',reason=type(e).__name__+':'+str(e)[:130])
        if pair is not None:
            try:
                b=data.hour(pair['symbol'],pair['time'])
                if not b or b.volume<=0:raise ValueError('CONTROL_ENTRY_UNVERIFIED')
                row['control']=data.outcome(make_plan(pair,b.open,pair['time']))
            except Exception as e:row['control']=dict(pair,status='UNVERIFIED',reason=type(e).__name__+':'+str(e)[:130])
        else:row['control']=dict(status='UNVERIFIED',reason='NO_NORMAL_CONTROL_DAY')
        return row
    # Append each completed independent observation before the portfolio replay.
    with ThreadPoolExecutor(max_workers=8) as pool, path.open('a') as f:
        jobs=[pool.submit(one,s) for s in pending]
        for n,job in enumerate(as_completed(jobs),1):
            r=job.result();f.write(json.dumps(r)+'\n');f.flush();cached[r['id']]=r
            if n%100==0 or n==len(jobs):print(json.dumps(dict(stage='fixed_outcomes',done=len(cached),total=len(signals))),flush=True)
    return cached


def simulate(data,signals,outcomes,limited):
    active=[];closed=[];skips=[];curve=[];cash=10000.;errors=[]
    bytime=defaultdict(list)
    for s in signals:bytime[s['time']].append(s)
    for at in range(data.manifest['start'],data.manifest['end']+1,DAY):
        for p in list(active):
            r=outcomes[p['id']]['main']
            due=r.get('exit_ms',p['deadline_ms'])
            if due>at:continue
            if r['status']=='CLOSED':
                cash+=p['risk_usdt']*r['net_r']+p['notional']*.0015
                closed.append(dict(r,**{k:p[k] for k in ('id','risk_usdt','entry_equity','quantity','notional')}))
            else:
                errors.append(dict(id=p['id'],reason=r.get('reason','UNVERIFIED')))
                closed.append(dict(p,status='UNVERIFIED',net_r=None,net_return_pct=None,reason=r.get('reason')))
            active.remove(p)
        pending=[]
        for s in bytime.get(at,[]):
            id=f"{s['symbol']}:{s['time']}";o=outcomes[id]['main']
            if 'entry' not in o:
                skips.append(dict(s,id=id,reason='ENTRY_PRICE_UNVERIFIED'));errors.append(dict(id=id,reason=o.get('reason')));continue
            pending.append(dict(make_plan(s,o['entry'],at),id=id))
        take,skip=choose(pending,active,limited);skips.extend(skip)
        for p in take:
            if cash<=0:errors.append(dict(id=p['id'],reason='CAPITAL_EXHAUSTED'));continue
            risk=cash*.01;p.update(risk_usdt=risk,entry_equity=cash,quantity=risk/p['risk'],notional=risk/p['risk']*p['entry'])
            cash-=p['notional']*.0015;active.append(p)
        # At UTC boundaries all previous 24-hour positions have expired.
        curve.append(dict(time=at,equity=cash,open_positions=len(active)))
    return dict(trades=closed,skips=skips,curve=curve,open_positions=active,errors=errors)


def stats(rows):
    valid=sorted((r for r in rows if r.get('net_return_pct') is not None),key=lambda r:(r['exit_ms'],r['symbol']))
    avg=lambda xs:statistics.mean(xs) if xs else None
    totals=defaultdict(float)
    for r in valid:totals[r['symbol']]+=r['net_return_pct']
    best=sorted(totals,key=lambda s:(-totals[s],s))[:3];running=peak=dd=0.;longest=streak=0
    for r in valid:
        running+=r['net_r'];peak=max(peak,running);dd=max(dd,peak-running)
        streak=streak+1 if r['net_return_pct']<0 else 0;longest=max(streak,longest)
    return dict(trades=len(valid),unverified=len(rows)-len(valid),win_pct=100*sum(r['net_return_pct']>0 for r in valid)/len(valid) if valid else None,
                mean_net_return_pct=avg([r['net_return_pct'] for r in valid]),mean_net_r=avg([r['net_r'] for r in valid]),
                total_net_return_pct=sum(r['net_return_pct'] for r in valid),total_net_r=sum(r['net_r'] for r in valid),
                stress_mean_pct=avg([r['stress_net_return_pct'] for r in valid]),without_best_three_mean_pct=avg([r['net_return_pct'] for r in valid if r['symbol'] not in best]),
                best_three=best,max_drawdown_r=dd,longest_loss_streak=longest,emergency_exits=sum(r['exit_reason'] in ('STOP','GAP_STOP') for r in valid))


def verdict(s,control,year_rows,verified=True):
    checks=dict(minimum_200=s['trades']>=200,mean_positive=s['mean_net_return_pct'] is not None and s['mean_net_return_pct']>0,
                stress_positive=s['stress_mean_pct'] is not None and s['stress_mean_pct']>0,
                without_best_three_positive=s['without_best_three_mean_pct'] is not None and s['without_best_three_mean_pct']>0,
                beats_control=s['mean_net_return_pct'] is not None and control['mean_net_return_pct'] is not None and s['mean_net_return_pct']>control['mean_net_return_pct'],
                majority_years_positive=sum(y['total_net_return_pct']>0 for y in year_rows)>len(year_rows)/2,data_verified=verified)
    return ('YETERSİZ VERİ' if not checks['minimum_200'] else 'GEÇTİ' if all(checks.values()) else 'BAŞARISIZ'),checks


def csv_write(p,rows):
    keys=sorted(set().union(*(set(r) for r in rows))) if rows else ['symbol','net_r']
    with Path(p).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys,lineterminator='\n');w.writeheader();w.writerows(rows)


def report(data,books,outcomes,out,identity,diagnostics):
    start,end=data.manifest['start'],data.manifest['end'];last12=ms(str(int(iso(end)[:4])-1)+iso(end)[4:]);results={};allrows=[];skips=[];curves=[]
    for mode,book in books.items():
        mode_result={}
        for side in ('ALL','LONG','SHORT'):
            rows=[r for r in book['trades'] if side=='ALL' or r['side']==side]
            controls=[dict(outcomes[r['id']]['control'],matched_id=r['id']) for r in rows]
            s=stats(rows);c=stats(controls)
            years=[dict(year=y,**stats([r for r in rows if str(y)==iso(r.get('exit_ms',r['entry_ms']))[:4]])) for y in range(int(iso(start)[:4]),int(iso(end-1)[:4])+1)]
            verified=not book['errors'] and not data.manifest['errors'] and s['unverified']==0 and c['unverified']==0
            decision,checks=verdict(s,c,years,verified)
            paired=[(r,outcomes[r['id']]['control']) for r in rows if r.get('net_return_pct') is not None and outcomes[r['id']]['control'].get('net_return_pct') is not None]
            mode_result[side]=dict(stats=s,control=c,years=years,verdict=decision,criteria_only_verdict=verdict(s,c,years,True)[0],checks=checks,
                paired_count=len(paired),paired_difference_pct=statistics.mean(a['net_return_pct']-b['net_return_pct'] for a,b in paired) if paired else None,
                last12=stats([r for r in rows if r.get('exit_ms',r['entry_ms'])>=last12]),
                hours48=stats([outcomes[r['id']].get('hours48',{}) for r in rows]),no_stop=stats([outcomes[r['id']].get('no_stop',{}) for r in rows]))
        equity=[10000]+[r['equity'] for r in book['curve']];peak=10000.;dd=0.
        for v in equity:peak=max(peak,v);dd=max(dd,100*(1-v/peak))
        results[mode]=dict(groups=mode_result,ending_equity=equity[-1],return_pct=100*(equity[-1]/10000-1),max_drawdown_pct=dd,errors=book['errors'])
        allrows.extend(dict(r,portfolio=mode,variant='main_24h') for r in book['trades'])
        for r in book['trades']:
            for key in ('control','hours48','no_stop'):allrows.append(dict(outcomes[r['id']].get(key,{}),portfolio=mode,variant=key,matched_id=r['id']))
        skips.extend(dict(r,portfolio=mode) for r in book['skips']);curves.extend(dict(r,portfolio=mode) for r in book['curve'])
    summary=dict(verdict=results['limited']['groups']['ALL']['verdict'],identity=identity,results=results,diagnostics=diagnostics,signals=len(outcomes),data_errors=data.manifest['errors'])
    (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False))
    csv_write(out/'trades.csv',allrows);csv_write(out/'skipped.csv',skips);csv_write(out/'equity.csv',curves)
    csv_write(out/'all_signals.csv',[dict(v['signal'],id=k) for k,v in sorted(outcomes.items())])
    csv_write(out/'control_matching.csv',[dict(v['control_signal'] or {},matched_id=k) for k,v in sorted(outcomes.items())])
    def f(x):return 'doğrulanmadı' if x is None else f'{x:.5f}' if isinstance(x,float) else str(x)
    lines=['# Anormal Gün Devamı — 24 saat',f"**{summary['verdict']}**",'',f"Veri: futures · {iso(start)} — {iso(end)} (son sınır hariç). Günlük mumlar UTC.",
           'Parametre denemesi: 0. Kontrol tohumu: 20261023. Tek tamamlanmış sabit kural deneyi. Canlı kapalı.',
           f"Veri hazırlama olayları: {len(data.manifest['errors'])}. Eksik verili sonuçlar tam doğrulanmış kabul edilmez.",
           '', '| Hesap / yön | İşlem | İsabet % | Ort. net % | Ort. net R | Toplam R | Kontrol ort. % | Eşleşmiş fark yüzde puan | Acil stop |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for mode,r in results.items():
        for side,g in r['groups'].items():
            s=g['stats'];lines.append('| '+mode+'/'+side+' | '+' | '.join(f(v) for v in [s['trades'],s['win_pct'],s['mean_net_return_pct'],s['mean_net_r'],s['total_net_r'],g['control']['mean_net_return_pct'],g['paired_difference_pct'],s['emergency_exits']])+' |')
    for mode,r in results.items():
        g=r['groups']['ALL'];s=g['stats'];recent=g['last12']
        lines+=['',f'## {mode}',f"Sonuç **{g['verdict']}** · kontroller: `{g['checks']}`.",
          f"%0,60 masraf ortalaması: %{f(s['stress_mean_pct'])}. En iyi üç coin hariç: %{f(s['without_best_three_mean_pct'])}; çıkarılanlar: {s['best_three']}.",
          f"Başlangıç 10.000 → {f(r['ending_equity'])} USDT. Getiri %{f(r['return_pct'])}; günlük sınır değerlerinde düşüş %{f(r['max_drawdown_pct'])}. R düşüşü {f(s['max_drawdown_r'])}; kayıp serisi {s['longest_loss_streak']}.",
          f"**Son 12 ay: {recent['trades']} işlem, ortalama net %{f(recent['mean_net_return_pct'])}, toplam {f(recent['total_net_r'])} R.**",
          f"Ayrı yön sonuçları: LONG **{r['groups']['LONG']['verdict']}**, SHORT **{r['groups']['SHORT']['verdict']}**. Yalnız altı sayısal koşul: LONG {r['groups']['LONG']['criteria_only_verdict']}, SHORT {r['groups']['SHORT']['criteria_only_verdict']}. Genel sonucu değiştirmez.",
          f"Kontrol: {g['control']['trades']} ölçülen / {g['control']['unverified']} doğrulanmayan; eşleşen çift {g['paired_count']}. İşlem veri olayları {len(r['errors'])}.",
          '', '| Yıl | İşlem | Toplam net % (eşit nominal) | Toplam R |', '|---|---:|---:|---:|']
        for y in g['years']:lines.append(f"| {y['year']} | {y['trades']} | {f(y['total_net_return_pct'])} | {f(y['total_net_r'])} |")
        lines+=['','### Önceden belirtilen ek ölçümler (başarı ölçütü değil)', '| Ölçüm | İşlem | Ort. net % | Ort. R | Toplam R | Doğrulanmayan/tamamlanmayan |', '|---|---:|---:|---:|---:|---:|']
        for key in ('hours48','no_stop'):
            q=g[key];lines.append('| '+key+' | '+' | '.join(f(q[x]) for x in ('trades','mean_net_return_pct','mean_net_r','total_net_r','unverified'))+' |')
    lines+=['','## Ölçüm sınırları ve sabit yorumlar',
      '- Standart sapma örneklem sapmasıdır (ddof=1). Önceki 90 getiri sinyal gününü içermez. ATR14 Wilder; girişten önce bilinen kapanmış günlük mumlar.',
      '- Evren her ay başında geçmiş 30 günlük quote-volume ile ilk 20, en az 120 günlük tarihçe. Tarihsel arşiv kataloğu delist ürünlerini içerir; güncel TRADING listesiyle filtreleme yok.',
      '- Aynı zaman damgasında önce vadesi dolan/stop olan pozisyonlar kapanır, sonra yeni sinyaller |z| sırasıyla alınır. 1R başlangıç stop mesafesidir.',
      '- 24/48 saatlik çıkış ilgili sınırdaki 1h açılışıdır. Saat içi stopun kesin saniyesi bilinmez; ilgili saatin son milisaniyesi kullanılır. Saat içi funding sırası belirsizse sonuç doğrulanmadı.',
      '- Gerçek funding oranları yön işaretiyle sabit giriş nominaline uygulanır; eksik funding sıfır varsayılmaz. Settlement sınırı giriş < funding zamanı ≤ çıkış. Marjin/liquidation modeli yok.',
      '- Kontrol aynı coin ve sinyal gününün aynı takvim ayındaki tüm gözlenen |z|<1 günlerinden, sonuçlarına bakmadan, sabit hash/tohumla seçilir. Bu geriye dönük eşleştirme canlı sinyale girmez. Her kontrol gününün z/ATR hesabı kendi gününe kadar kapalı veri kullanır.',
      '- Kontroller eşleşmiş bağımsız karşılaştırmalardır, ayrı bir limitli portföy değildir. Aynı normal gün birden fazla sinyale eşleşebilir; eşleşmeler bağımsız örnekler olduğu iddia edilmez. Kontrol bulunamazsa tekrar seçim/uydurma yapılmaz.',
      '- 48 saat ve stopsuz sonuçlar ana hesapta alınan aynı sinyallerde ölçülür; kapasiteleri yeniden dağıtılmaz, portföy alternatifi diye sunulmaz. Bunlarla kural seçilmez.',
      '- En iyi üç coin toplam net getiriye göre seçilip aynı işlem listesinden çıkarılır. Masraf stresi işlem listesini değiştirmez. Yıllık ve son 12 ay hesapları çıkış tarihine göre; kısmi yıllar çoğunluk testine dahil.',
      '- Sermaye eğrisi UTC günlük sınırlarda, kapanan 24h işlemler ve yeni girişin %0,15 ücretiyle hesaplanır; gün içi maksimum düşüş değildir. Her yeni giriş anındaki sermayenin %1’i risk edilir. Çıkış masrafı diğer %0,15; toplam giriş nominalinin %0,30’u.',
      '- Doğrulanmayan piyasa verisi varsa sayısal sonuçlar tam doğrulanmış sayılmaz; canlı açılmaz. Tüm olaylar JSON/CSV içindedir.',
      '', 'Kaynak çalışma: https://doi.org/10.1007/s11408-020-00357-1 (bu deney birebir replikasyon değildir).',
      'Veri: https://data.binance.vision/ · gerçek funding tamamlaması: https://www.binance.com/fapi/v1/fundingRate']
    (out/'report.md').write_text('\n'.join(lines)+'\n');plot(books,out/'equity_curve.png',any(not r['groups']['ALL']['checks']['data_verified'] for r in results.values()));return summary


def plot(books,path,unverified=False):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from datetime import datetime,timezone
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True)
    for mode,b in books.items():
        times=[datetime.fromtimestamp(p['time']/1000,timezone.utc) for p in b['curve']];values=[p['equity'] for p in b['curve']]
        axes[0].plot(times,values,label=mode)
        if mode=='limited':
            peak=10000.;dd=[]
            for v in values:peak=max(peak,v);dd.append(100*(v/peak-1))
            axes[1].fill_between(times,dd,0,color='#c33',alpha=.3)
    axes[0].set_title('Anormal Gün Devamı · 24 saat · canlı kapalı');axes[0].set_ylabel('USDT · başlangıç 10.000');axes[0].legend()
    if unverified:axes[0].text(.02,.94,'VERİ OLAYLARI: TAM DOĞRULANMADI',transform=axes[0].transAxes,color='red')
    axes[1].set_ylabel('Limitli düşüş %');axes[1].set_xlabel('UTC')
    for ax in axes:ax.grid(alpha=.2)
    fig.tight_layout();fig.savefig(path,dpi=150);plt.close(fig)


def run(root,archive,out,resume=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);data=Data(root,archive)
    identity=dict(rules=RULES,rules_hash=RULES_HASH,protocol=json.loads(Path('abnormal_day_protocol.json').read_text()),
      code={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('.').glob('abnormal_day_*.py'))},
      manifest_sha256=hashlib.sha256((Path(root)/'manifest.json').read_bytes()).hexdigest(),universe_sha256=hashlib.sha256((Path(root)/'universe.json').read_bytes()).hexdigest(),
      start=data.manifest['start'],end=data.manifest['end'])
    seal=out/'sealed_run.json'
    if resume:
        if (out/'summary.json').exists() or json.loads(seal.read_text())!=identity:raise RuntimeError('SEALED_RUN_CANNOT_CHANGE_OR_REPEAT')
    else:
        with seal.open('x') as f:json.dump(identity,f,indent=2)
    signals,matched,diagnostics=candidates(data)
    (out/'frozen_control_matching.json').write_text(json.dumps(matched,indent=2))
    print(json.dumps(dict(stage='signals_frozen',signals=len(signals),controls=sum(v is not None for v in matched.values()))),flush=True)
    outcomes=prepare_outcomes(data,signals,matched,out/'outcomes.jsonl')
    books={mode:simulate(data,signals,outcomes,mode=='limited') for mode in ('unlimited','limited')}
    return report(data,books,outcomes,out,identity,diagnostics)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--archive-cache',required=True)
    p.add_argument('--output',default='research/abnormal_day_v1/results');p.add_argument('--resume',action='store_true')
    a=p.parse_args();run(a.data,a.archive_cache,a.output,a.resume)
