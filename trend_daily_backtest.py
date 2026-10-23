"""One sealed daily trend replay. No optimisation or parameter-search interface."""
import argparse
from bisect import bisect_right
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import requests
from trend_breakout_data import Archive,ms,iso,month,months
from trend_breakout_engine import Candle,DAY,MINUTE
from trend_daily_engine import (RULES,RULES_HASH,DailySeries,evaluate,make_plan,
                                update_stop,stop_exit,choose,result,excursions,direction_five)


class Data:
    def __init__(self,root,archive):
        self.root=Path(root);self.archive=archive
        self.universe=json.loads((self.root/'universe.json').read_text())
        self.manifest=json.loads((self.root/'manifest.json').read_text())
        self.series={};self.funding={};self.coverage={};self.last={}
        for row in self.manifest['symbols']:
            path=self.root/'prepared'/f"{row['symbol']}.json"
            if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('PREPARED_DATA_HASH_CHANGED')
            x=json.loads(path.read_text());s=x['symbol']
            self.series[s]=DailySeries([Candle(**b) for b in x['bars']])
            times=[];sums=[0.]
            for f in x['funding']:
                times.append(int(f['time'])//1000*1000);sums.append(sums[-1]+f['rate'])
            self.funding[s]=(times,sums);self.coverage[s]=set(x['funding_coverage'])
            self.last[s]=self.series[s].bars[-1]

    def funding_cost(self,p,at):
        times,sums=self.funding[p['symbol']]
        a=bisect_right(times,p['entry_ms']);b=bisect_right(times,at)
        missing=[m for m in months(p['entry_ms'],at+1) if m not in self.coverage[p['symbol']]]
        return (1 if p['side']=='LONG' else -1)*100*(sums[b]-sums[a]),b-a,missing

    def bar(self,s,at):
        q=self.series[s];i=q.by_open.get(at)
        return q.bars[i] if i is not None else None

    def minutes(self,s,at):
        rows=self.archive.candles(s,'1m',iso(at)[:10],daily=True)
        if rows:return rows
        # A data-only fallback to the same official futures instrument.
        path=self.root/'minute_rest'/f'{s}-{at}.json'
        if path.exists():return [Candle(**b) for b in json.loads(path.read_text())]
        rows=[];cursor=at
        while cursor<at+DAY:
            r=requests.get('https://www.binance.com/fapi/v1/klines',params=dict(symbol=s,interval='1m',startTime=cursor,endTime=at+DAY-1,limit=1000),timeout=30)
            r.raise_for_status();raw=r.json()
            if not isinstance(raw,list):raise ValueError('INVALID_MINUTE_RESPONSE')
            if not raw:break
            rows.extend(Candle(int(x[0]),int(x[6])+1,*map(float,x[1:6]),float(x[7])) for x in raw)
            nxt=int(raw[-1][6])+1
            if nxt<=cursor:raise ValueError('MINUTE_PAGINATION_STALLED')
            cursor=nxt
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps([asdict(b) for b in rows]))
        return rows

    def resolve(self,p,b):
        def with_movement(x):
            move=excursions(p,[],x['exit'])
            if x.get('pre_exit_high') is not None:
                hi,lo=x['pre_exit_high'],x['pre_exit_low']
                fav=(hi-p['entry']) if p['side']=='LONG' else (p['entry']-lo)
                adv=(p['entry']-lo) if p['side']=='LONG' else (hi-p['entry'])
                move['mfe_r']=max(move['mfe_r'],fav/p['risk']);move['mae_r']=max(move['mae_r'],adv/p['risk'])
            return dict(x,**move)
        basic=stop_exit(p,b)
        if not basic:return None
        if basic['exit_ms'] is not None:return dict(basic,**excursions(p,[],basic['exit']),resolution='DAILY_OPEN_GAP')
        key=hashlib.sha256(json.dumps([p['symbol'],b.open_ms,p['side'],p['stop']]).encode()).hexdigest()
        path=self.root/'resolutions'/f'{key}.json'
        if path.exists():
            x=json.loads(path.read_text())
            return with_movement(x)
        try:
            minutes=self.minutes(p['symbol'],b.open_ms)
            before=[];expected=b.open_ms
            for m in minutes:
                if m.open_ms!=expected or m.close_ms!=m.open_ms+MINUTE:raise ValueError('MINUTE_GAP_BEFORE_STOP')
                if not before and abs(m.open-b.open)>max(1e-8,b.open*1e-7):raise ValueError('MINUTE_OPEN_MISMATCH')
                expected=m.close_ms
                e=stop_exit(p,m)
                if e:
                    x=dict(e,exit_ms=e['exit_ms'] if e['exit_ms'] is not None else m.close_ms-1,
                           resolution='1M_STOP_TIME',pre_exit_high=max((v.high for v in before),default=None),pre_exit_low=min((v.low for v in before),default=None))
                    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(x))
                    return with_movement(x)
                before.append(m)
            raise ValueError('DAILY_MINUTE_STOP_MISMATCH')
        except Exception as e:
            return dict(basic,resolution='UNVERIFIED_STOP_TIME',error=type(e).__name__,mfe_r=None,mae_r=None)


def new_book(mode):
    return dict(mode=mode,cash=10000.,positions=[],trades=[],skips=[],stops=[],curve=[],errors=[],counts={},next_day=None)


def preload_stop_days(data,start,end,path):
    """Within the sealed run, prefetch exit-day minute data in parallel.

    This does not select trades or compute returns. Every possible daily signal
    uses the fixed shared exit rules only to identify required data files.
    Portfolio acceptance and accounting are subsequently replayed chronologically.
    """
    if path.exists():return
    required=set();membership={m:{r['symbol'] for r in rows} for m,rows in data.universe['months'].items()}
    for symbol,series in data.series.items():
        for i,b in enumerate(series.bars[:-1]):
            t=b.close_ms
            if not start<=t<end or symbol not in membership.get(month(t),set()):continue
            sig=evaluate(symbol,series,t)
            if sig['status']!='SIGNAL' or series.bars[i+1].open_ms!=t:continue
            p=make_plan(sig,series.bars[i+1].open,t)
            for row in series.bars[i+1:]:
                if row.close_ms>end:break
                hit=stop_exit(p,row)
                if hit:
                    if hit['exit_ms'] is None:required.add((symbol,row.open_ms))
                    break
                try:p['stop']=update_stop(p,series,row.close_ms)['stop']
                except ValueError:break
    failures=[]
    def one(pair):
        try:
            rows=data.minutes(*pair)
            if not rows:raise ValueError('NO_MINUTES')
        except Exception as e:return dict(symbol=pair[0],day=pair[1],error=type(e).__name__)
        return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        jobs=[pool.submit(one,pair) for pair in sorted(required)]
        for n,f in enumerate(as_completed(jobs),1):
            error=f.result()
            if error:failures.append(error)
            if n%100==0 or n==len(jobs):print(json.dumps(dict(stage='exit_minute_data',done=n,total=len(jobs),download_errors=len(failures))),flush=True)
    path.write_text(json.dumps(dict(files=len(required),errors=failures),indent=2))


def accrue(book,p,data,at):
    cost,n,missing=data.funding_cost(p,at)
    book['cash']-=p['notional']*(cost-p['funding_pct'])/100
    p.update(funding_pct=cost,funding_events=n)
    if missing:p['funding_unverified_months']=missing


def close_position(book,p,e,data):
    if e['exit_ms'] is None:
        book['errors'].append(dict(symbol=p['symbol'],time=p['last_day'],reason='UNVERIFIED_STOP_TIME'))
        p.update(e,net_r=None,status='UNVERIFIED');book['trades'].append(p);book['positions'].remove(p)
        # Unknown funding timing is not replaced by an invented fill/return.
        book['cash']+=p['quantity']*(1 if p['side']=='LONG' else -1)*(e['exit']-p['entry'])-p['notional']*.0015
        return
    accrue(book,p,data,e['exit_ms'])
    book['cash']+=p['quantity']*(1 if p['side']=='LONG' else -1)*(e['exit']-p['entry'])-p['notional']*.0015
    p.update(e,**result(p,e['exit'],p['funding_pct']),status='CLOSED',
             hold_days=(e['exit_ms']-p['entry_ms'])/DAY,
             direction_five=direction_five(p,data.series[p['symbol']].bars))
    p['net_usdt']=p['initial_risk_usdt']*p['net_r']
    if p.get('funding_unverified_months'):
        p['net_r_unverified']=p.pop('net_r');p['net_r']=None
        book['errors'].append(dict(symbol=p['symbol'],time=e['exit_ms'],reason='MISSING_ACTUAL_FUNDING'))
    book['trades'].append(dict(p));book['positions'].remove(p)


def equity(book,data,at,opening=False):
    value=book['cash']
    for p in book['positions']:
        b=data.bar(p['symbol'],at)
        price=(b.open if opening else b.close) if b and b.volume>0 else p.get('last_price',p['entry'])
        value+=p['quantity']*(1 if p['side']=='LONG' else -1)*(price-p['entry'])
    return value


def replay(book,data,start,end,checkpoint):
    begin=book.get('next_day') or start
    pool=ThreadPoolExecutor(max_workers=12)
    try:
        for at in range(begin,end,DAY):
            # Existing exposures at signal time prohibit a same-coin re-entry on a gap exit.
            occupied_at_signal={p['symbol'] for p in book['positions']}
            for p in list(book['positions']):
                b=data.bar(p['symbol'],at)
                if b and b.volume>0:
                    e=stop_exit(p,b)
                    if e and e['exit_ms'] is not None:close_position(book,p,dict(e,**excursions(p,[],e['exit']),resolution='DAILY_OPEN_GAP'),data)
                    else:accrue(book,p,data,at)
            candidates=[]
            for row in data.universe['months'].get(month(at),[]):
                s=row['symbol'];sig=evaluate(s,data.series[s],at)
                book['counts'][sig.get('reason','UNKNOWN')]=book['counts'].get(sig.get('reason','UNKNOWN'),0)+1
                if sig['status']!='SIGNAL':continue
                if s in occupied_at_signal:
                    book['skips'].append(dict(sig,status='SKIPPED',reason='SAME_SYMBOL_OPEN'));continue
                bar=data.bar(s,at)
                if not bar or bar.volume==0:
                    book['skips'].append(dict(sig,status='SKIPPED',reason='NEXT_OPEN_MISSING_OR_NO_TRADES'));continue
                candidates.append(make_plan(sig,bar.open,at))
            accepted,rejected=choose(candidates,book['positions'],book['mode']=='limited')
            book['skips'].extend(rejected)
            for p in accepted:
                capital=equity(book,data,at,True)
                if capital<=0:
                    book['errors'].append(dict(time=at,reason='CAPITAL_EXHAUSTED'));continue
                risk=capital*.01;qty=risk/p['risk'];notional=qty*p['entry']
                p.update(initial_risk_usdt=risk,quantity=qty,notional=notional,entry_equity=capital,last_price=p['entry'])
                book['cash']-=notional*.0015;book['positions'].append(p)
            jobs=[]
            for p in list(book['positions']):
                p['last_day']=at;b=data.bar(p['symbol'],at)
                if not b or b.volume==0:
                    p['unverified_market_data']=True
                    if not p.get('missing_data_recorded'):
                        book['errors'].append(dict(symbol=p['symbol'],time=at,reason='OPEN_POSITION_NO_TRADES_OR_MISSING_DATA'))
                        p['missing_data_recorded']=True
                    continue
                e=stop_exit(p,b)
                if e:jobs.append((p,pool.submit(data.resolve,dict(p),b)))
                else:
                    p.update(excursions(p,[b]));p['last_price']=b.close
                    accrue(book,p,data,b.close_ms-1)
                    change=update_stop(p,data.series[p['symbol']],b.close_ms)
                    p['stop']=change['stop'];book['stops'].append(dict(symbol=p['symbol'],entry_ms=p['entry_ms'],**change))
            for p,job in jobs:close_position(book,p,job.result(),data)
            book['curve'].append(dict(time=at+DAY,equity=equity(book,data,at),cash=book['cash'],open_positions=len(book['positions'])))
            book['next_day']=at+DAY
            if month(at)!=month(at+DAY) or at+DAY==end:
                temp=checkpoint.with_suffix('.tmp');temp.write_text(json.dumps(book));temp.replace(checkpoint)
                print(json.dumps(dict(stage='daily_replay',mode=book['mode'],through=iso(at+DAY))),flush=True)
    finally:pool.shutdown()
    return book


def max_dd(values):
    peak=values[0] if values else 0.;worst=0.
    for v in values:
        peak=max(peak,v)
        if peak>0:worst=max(worst,100*(peak-v)/peak)
    return worst


def statistics_for(trades):
    rows=[t for t in trades if t.get('net_r') is not None]
    rows=sorted(rows,key=lambda t:(t['exit_ms'],t['symbol']))
    wins=[t['net_r'] for t in rows if t['net_r']>0];loss=[t['net_r'] for t in rows if t['net_r']<0]
    avg=lambda x:statistics.mean(x) if x else None
    bysymbol=defaultdict(float)
    for t in rows:bysymbol[t['symbol']]+=t['net_r']
    best=sorted(bysymbol,key=lambda s:(-bysymbol[s],s))[:3]
    total=sum(t['net_r'] for t in rows);top=sum(sorted((t['net_r'] for t in rows),reverse=True)[:10])
    running=peak=dd=0.;streak=longest=0
    for t in rows:
        running+=t['net_r'];peak=max(peak,running);dd=max(dd,peak-running)
        streak=streak+1 if t['net_r']<0 else 0;longest=max(longest,streak)
    direction=[t['direction_five'] for t in rows if t.get('direction_five') is not None]
    return dict(trades=len(rows),unverified=len(trades)-len(rows),win_pct=100*len(wins)/len(rows) if rows else None,
                direction_five_pct=100*sum(direction)/len(direction) if direction else None,direction_samples=len(direction),
                mean_net_r=avg([t['net_r'] for t in rows]),total_net_r=total,
                average_win_r=avg(wins),average_loss_r=avg(loss),average_hold_days=avg([t['hold_days'] for t in rows]),
                max_drawdown_r=dd,longest_loss_streak=longest,top_ten_r=top,top_ten_share_pct=100*top/total if total else None,
                stress_mean_r=avg([t['stress_net_r'] for t in rows]),best_three=best,
                without_best_three_mean_r=avg([t['net_r'] for t in rows if t['symbol'] not in best]),
                funding_effect_r=sum(t['funding_effect_r'] for t in rows),
                resolutions=dict(Counter(t.get('resolution') for t in trades)))


def csv_write(path,rows):
    keys=sorted(set().union(*(set(r) for r in rows))) if rows else ['symbol','entry','stop','net_r']
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys,lineterminator='\n');w.writeheader();w.writerows(rows)


def judgement(stats,years,verified=True):
    checks=dict(mean_positive=stats['mean_net_r'] is not None and stats['mean_net_r']>0,
                cost_060_positive=stats['stress_mean_r'] is not None and stats['stress_mean_r']>0,
                without_best_three_positive=stats['without_best_three_mean_r'] is not None and stats['without_best_three_mean_r']>0,
                majority_years_positive=sum(y['total_net_r']>0 for y in years)>len(years)/2,
                data_verified=verified)
    return ('GEÇTİ' if all(checks.values()) else 'BAŞARISIZ'),checks


def final_report(books,data,out,identity):
    start,end=identity['start'],identity['end'];last12=ms(iso(end).replace(str(int(iso(end)[:4])),str(int(iso(end)[:4])-1),1))
    reports={};alltrades=[];skips=[];stops=[];curves=[];openrows=[]
    for mode,book in books.items():
        groups={side:statistics_for([t for t in book['trades'] if side=='ALL' or t['side']==side]) for side in ('ALL','LONG','SHORT')}
        yearly=[];previous=10000.
        for year in range(int(iso(start)[:4]),int(iso(end-1)[:4])+1):
            rows=[t for t in book['trades'] if t.get('exit_ms') is not None and iso(t['exit_ms'])[:4]==str(year)]
            points=[c for c in book['curve'] if iso(c['time']-1)[:4]==str(year)]
            ending=points[-1]['equity'] if points else previous
            yearly.append(dict(year=year,**statistics_for(rows),portfolio_return_pct=100*(ending/previous-1) if previous else None));previous=ending
        verified=not book['errors'] and not data.manifest['errors'] and not any(p.get('funding_unverified_months') for p in book['positions'])
        verdict,checks=judgement(groups['ALL'],yearly,verified)
        recent=[t for t in book['trades'] if t.get('exit_ms') is not None and t['exit_ms']>=last12]
        reports[mode]=dict(groups=groups,years=yearly,last12=statistics_for(recent),verdict=verdict,checks=checks,
                           open_positions=len(book['positions']),errors=book['errors'],counts=book['counts'],
                           ending_equity=book['curve'][-1]['equity'],portfolio_return_pct=100*(book['curve'][-1]['equity']/10000-1),
                           max_drawdown_pct=max_dd([10000]+[c['equity'] for c in book['curve']]))
        alltrades.extend(dict(t,portfolio=mode) for t in book['trades']);skips.extend(dict(t,portfolio=mode) for t in book['skips'])
        stops.extend(dict(t,portfolio=mode) for t in book['stops']);curves.extend(dict(t,portfolio=mode) for t in book['curve'])
        openrows.extend(dict(t,portfolio=mode) for t in book['positions'])
    btc=[b for b in data.series['BTCUSDT'].bars if start<=b.open_ms and b.close_ms<=end]
    btc_initial=btc[0].open;benchmark=dict(start=btc[0].open_ms,end=btc[-1].close_ms,gross_return_pct=100*(btc[-1].close/btc_initial-1),
                  close_max_drawdown_pct=max_dd([btc_initial]+[b.close for b in btc]),interpretation='BTC underlying buy-and-hold; daily closing prices; no funding, no leverage, no costs')
    summary=dict(identity=identity,verdict=reports['limited']['verdict'],results=reports,benchmark=benchmark)
    (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False))
    for name,rows in [('trades',alltrades),('skipped',skips),('stop_updates',stops),('equity',curves),('open_positions',openrows)]:csv_write(out/(name+'.csv'),rows)
    def fmt(x):return 'doğrulanmadı' if x is None else f'{x:.4f}' if isinstance(x,float) else str(x)
    lines=['# Günlük Trend Motoru — geçmiş test','',f"**{summary['verdict']}**",'',f"Veri/simülasyon: {iso(start)} — {iso(end)} (son sınır hariç). Günler UTC.",
           'Parametre denemesi: 0. Kurallar bir kez çalıştırıldı. Canlı kapalı.','',
           '| Portföy/yön | İşlem | İsabet % | 5g yön % | Ortalama R | Toplam R | Ort. kazanç R | Ort. kayıp R | Ort. gün | Düşüş R | Kayıp serisi |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    if any(r['errors'] for r in reports.values()):
        lines[4:4]=['**Veri olayları var: aşağıdaki sermaye eğrisi ve getiriler tam doğrulanmış sonuç değildir. Eksik verili işlemler/pozisyonlar summary.json içinde açıkça işaretlidir.**','']
    keys=['trades','win_pct','direction_five_pct','mean_net_r','total_net_r','average_win_r','average_loss_r','average_hold_days','max_drawdown_r','longest_loss_streak']
    for mode,r in reports.items():
        for side,s in r['groups'].items():lines.append('| '+mode+'/'+side+' | '+' | '.join(fmt(s[k]) for k in keys)+' |')
    for mode,r in reports.items():
        s=r['groups']['ALL'];recent=r['last12']
        lines+=['',f'## {mode}',f"Sonuç: **{r['verdict']}**. Başarı kontrolleri: `{r['checks']}`.",
                f"Sermaye: 10.000 → {fmt(r['ending_equity'])} USDT; getiri %{fmt(r['portfolio_return_pct'])}; günlük kapanış düşüşü %{fmt(r['max_drawdown_pct'])}.",
                f"%0,60 masraf ortalama R: {fmt(s['stress_mean_r'])}. En iyi 3 coin hariç: {fmt(s['without_best_three_mean_r'])}; çıkarılanlar {s['best_three']}.",
                f"Funding toplam etkisi: {fmt(s['funding_effect_r'])} R. En iyi 10 işlem: {fmt(s['top_ten_r'])} R; toplam net R'ye işaretli oranı %{fmt(s['top_ten_share_pct'])}.",
                f"**Son 12 ay (çıkış tarihine göre): {recent['trades']} işlem; ortalama {fmt(recent['mean_net_r'])} R; toplam {fmt(recent['total_net_r'])} R; isabet %{fmt(recent['win_pct'])}.**",
                f"Açık pozisyon: {r['open_positions']}; doğrulanamayan kapanış: {s['unverified']}; veri olayları: {len(r['errors'])}. 1m çözüm sayıları: {s['resolutions']}.",
                '', '| Yıl | İşlem | Toplam R | Portföy getirisi % |','|---|---:|---:|---:|']
        for y in r['years']:lines.append(f"| {y['year']} | {y['trades']} | {fmt(y['total_net_r'])} | {fmt(y['portfolio_return_pct'])} |")
    lines+=['','## BTC karşılaştırması',f"Aynı aralıkta BTC al-tut brüt getiri %{fmt(benchmark['gross_return_pct'])}; günlük kapanış maksimum düşüşü %{fmt(benchmark['close_max_drawdown_pct'])}.",
            '', '## Ölçüm ve veri sınırları',
            '- Yalnız kapanmış günlük mumlar. Giriş kanalı sinyal gününü dışlar. Ertesi günün stopu, az önce kapanan gün dahil son 10 kapalı günden hesaplanır; geriye uygulanmaz.',
            '- Gün içi stop saatini ve çıkış öncesi hareketi belirlemek için 1m arşivi kullanılır; 1m sinyal/filtre oluşturmaz. Çıkış dakikasının iç sırası bilinmez: o dakikanın ekstremi MFE/MAE’ye eklenmez; stop/gap fiyatı eklenir.',
            '- Gerçek funding oranlarının işaretli toplamı sabit giriş nominaline uygulanır. Değişen mark-price nominali ayrıca modellenmez. Eksik funding uydurulmaz; veri doğrulanamıyorsa GEÇTİ sonucu verilmez.',
            '- Funding arşivinde yayımlanmamış dönemler aynı resmi Binance futures REST uç noktasından tamamlanır; kaynak yanıtları saklanır.',
            '- Toplam %0,30 giriş nominali üzerinden gider; sermaye hesabında yarısı girişte, yarısı çıkışta. Her girişte mevcut mark-to-market sermayenin %1’i başlangıç riski olarak ayrılır. Marjin/liquidation ve borçlanma modellenmez.',
            '- 5 günlük yön ölçümü işlem erken kapansa da sonraki beş tam günü kullanır; eksik gelecek veri doğrulanmadı olarak kalır.',
            '- Açık işlemler süre nedeniyle kapatılmaz. Son değerleri sermaye eğrisine dahil, gerçekleşmiş R istatistiklerine dahil değildir. Veri biten/delist pozisyonlara yapay çıkış fiyatı atanmaz; veri olayı olarak ayrıca gösterilir.',
            '- Yıllık R çıkış yılına göre; portföy getirisi günlük kapanış NAV’ından. Kısmi ilk/son yıllar çoğunluk testinde sayılır. R düşüşü kapanış sıralı, portföy düşüşü günlük kapanış değerlerindendir.',
            '- En iyi üç coin çıkarma ve masraf stresi sabit işlem listesinde ölçülür; boşalan kapasite yeniden doldurulmaz.',
            '- Aylık evren geçmiş 30 gün hacmi ve en az 100 günlük veriyle seçilir. Stable/endeks dışlama listesi açıkça kaydedildi; tüm tarihsel ürün sınıflandırması bağımsız exchangeInfo arşiviyle doğrulanmadı.',
            '- BTC karşılaştırması aynı futures mumlarındaki BTC fiyatını al-tut karşılaştırmasıdır; spot portföyü veya funding ödenen perpetual pozisyonu simülasyonu değildir.',
            '', 'Tam kayıtlar: trades.csv, skipped.csv, stop_updates.csv, equity.csv, open_positions.csv, summary.json, sealed_run.json.',
            'Kaynak: https://data.binance.vision/ · https://github.com/binance/binance-public-data']
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    plot_curves(books,btc,out/'equity_curve.png');return summary


def plot_curves(books,btc,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from datetime import datetime,timezone
    fig,axes=plt.subplots(2,1,figsize=(12,8),sharex=True,gridspec_kw={'height_ratios':[2,1]})
    for mode,book in books.items():
        dates=[datetime.fromtimestamp(c['time']/1000,timezone.utc) for c in book['curve']]
        values=[c['equity'] for c in book['curve']];axes[0].plot(dates,values,label=mode)
        if mode=='limited':
            peak=10000.;dd=[]
            for v in values:peak=max(peak,v);dd.append(100*(v/peak-1))
            axes[1].fill_between(dates,dd,0,alpha=.3,color='#c33')
    axes[0].plot([datetime.fromtimestamp(b.close_ms/1000,timezone.utc) for b in btc],[10000*b.close/btc[0].open for b in btc],label='BTC fiyat al-tut (brüt)',alpha=.65)
    axes[0].set_ylabel('USDT · başlangıç 10.000');axes[0].legend();axes[0].set_title('Günlük Trend Motoru · geçmiş test · canlı kapalı')
    if any(book['errors'] for book in books.values()):axes[0].text(.02,.95,'VERİ EKSİK: EĞRİ TAM DOĞRULANMADI',transform=axes[0].transAxes,color='red',va='top')
    axes[1].set_ylabel('Limitli düşüş %');axes[1].set_xlabel('UTC')
    for ax in axes:ax.grid(alpha=.2)
    fig.tight_layout();fig.savefig(path,dpi=150);plt.close(fig)


def run(root,archive_root,out,resume=False):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((root/'manifest.json').read_text());universe=json.loads((root/'universe.json').read_text())
    identity=dict(rules=RULES,rules_hash=RULES_HASH,start=manifest['start'],end=manifest['end'],
                  protocol=json.loads(Path('trend_daily_protocol.json').read_text()),
                  code={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('.').glob('trend_daily_*.py'))},
                  data={r['symbol']:hashlib.sha256((root/'prepared'/f"{r['symbol']}.json").read_bytes()).hexdigest() for r in manifest['symbols']},
                  universe_sha256=hashlib.sha256((root/'universe.json').read_bytes()).hexdigest())
    seal=out/'sealed_run.json'
    if resume:
        if (out/'summary.json').exists() or json.loads(seal.read_text())!=identity:raise RuntimeError('SEALED_RUN_CANNOT_BE_CHANGED_OR_REPEATED')
    else:
        with seal.open('x') as f:json.dump(identity,f,indent=2)
    data=Data(root,Archive(archive_root));books={}
    preload_stop_days(data,identity['start'],identity['end'],out/'minute_preload.json')
    for mode in ('unlimited','limited'):
        checkpoint=out/(mode+'_checkpoint.json')
        book=json.loads(checkpoint.read_text()) if checkpoint.exists() else new_book(mode)
        books[mode]=replay(book,data,identity['start'],identity['end'],checkpoint)
    return final_report(books,data,out,identity)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--archive-cache',required=True)
    p.add_argument('--output',default='research/trend_daily_v1/results');p.add_argument('--resume',action='store_true')
    a=p.parse_args();run(a.data,a.archive_cache,a.output,a.resume)
