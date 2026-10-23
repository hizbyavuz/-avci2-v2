"""Fixed Saatlik Motor replay. --run seals source/data and resumes only that run."""
import argparse
from bisect import bisect_left,bisect_right
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
import csv
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import statistics
from hourly_motor_engine import (Candle,Series,HOUR,DAY,RULES,RULES_HASH,
    daily_direction,evaluate,make_plan,funding_percent,result)
from trend_breakout_data import Archive,ms,iso,month
from trend_breakout_backtest import resolve_hour


def load_symbol(root,s):
    with gzip.open(Path(root)/'prepared'/(s+'.json.gz'),'rt') as f:return json.load(f)


def check_funding(plan,until,events):
    times=[int(f['time'])//1000*1000 for f in events]
    lo=bisect_right(times,plan['entry_ms']);hi=bisect_right(times,until)
    bracket=times[max(0,lo-1):min(len(times),hi+1)]
    if not bracket or bracket[0]>plan['entry_ms'] or bracket[-1]<until:
        raise ValueError('ACTUAL_FUNDING_NOT_BRACKETED')
    if any(b-a>8*HOUR+1000 for a,b in zip(bracket,bracket[1:])):
        raise ValueError('ACTUAL_FUNDING_GAP')
    return funding_percent(plan['side'],events,plan['entry_ms'],until)


def simulate_trade(plan,hours,minute_loader,events,end):
    """Pure exit semantics also used by live for complete entry-aligned hours."""
    if plan['deadline_ms']>end:return dict(plan,status='PENDING',reason='FOLLOWUP_NOT_CLOSED')
    by_time=hours if isinstance(hours,dict) else {b.open_ms:b for b in hours}
    try:
        exit_event=None
        for at in range(plan['entry_ms'],plan['deadline_ms'],HOUR):
            b=by_time.get(at)
            if not b or b.close_ms!=at+HOUR or b.volume<=0:raise ValueError('HOUR_MISSING_OR_NO_TRADES')
            exit_event=resolve_hour(plan,b,minute_loader)
            if exit_event:
                if any(at<f['time']<b.close_ms and f['time']%HOUR for f in events):
                    raise ValueError('INTRAHOUR_FUNDING_EXIT_ORDER_UNKNOWN')
                break
        if exit_event is None:
            b=by_time.get(plan['deadline_ms'])
            if not b or b.volume<=0:raise ValueError('TIMEOUT_PRICE_MISSING')
            exit_event=dict(outcome='TIMEOUT',exit=b.open,exit_ms=b.open_ms,resolution='2H_BOUNDARY_OPEN')
        funding=check_funding(plan,exit_event['exit_ms'],events)
        return dict(plan,**exit_event,**result(plan,exit_event['exit'],funding),status='CLOSED')
    except Exception as e:return dict(plan,status='UNVERIFIED',reason=str(e)[:180])


def candidates(root,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    u=json.loads((root/'universe.json').read_text());manifest=json.loads((root/'manifest.json').read_text())
    btc=Series([Candle(**b) for b in load_symbol(root,'BTCUSDT')['daily']],DAY)
    memberships={m:{r['symbol'] for r in rows} for m,rows in u['months'].items()}
    selected=sorted(set().union(*memberships.values()));records=[];audit=Counter();daily_counts=Counter()
    for n,s in enumerate(selected,1):
        x=load_symbol(root,s);d=Series([Candle(**b) for b in x['daily']],DAY);h=Series([Candle(*b) for b in x['hourly']],HOUR)
        directions={};quota=set()
        for i,b in enumerate(h.bars):
            at=b.close_ms
            if not u['start']<=at<u['end'] or s not in memberships.get(month(at),set()):continue
            day=at//DAY*DAY
            if day not in directions:
                directions[day]=daily_direction(s,d,btc,at)
                daily_counts[directions[day]['reason']]+=1
            direction=directions[day]
            groups=('MAIN','CONTROL_A') if direction['direction'] else ('CONTROL_B',)
            for group in groups:
                sig=evaluate(s,h,direction,at,group);audit[group+':'+sig['reason']]+=1
                if sig['status']!='SIGNAL':continue
                sig['id']=f'{group}:{s}:{at}'
                if (group,day) in quota:records.append(dict(sig,status='SKIPPED',reason='DAILY_QUOTA'));continue
                if i+1>=len(h.bars) or h.bars[i+1].open_ms!=at or h.bars[i+1].volume<=0:
                    # Unknown execution cannot be treated as an unused quota
                    # and replaced with a later, better-observed signal.
                    quota.add((group,day));records.append(dict(sig,status='UNVERIFIED',reason='ENTRY_PRICE_MISSING'));continue
                plan=make_plan(sig,h.bars[i+1].open,at)
                records.append(plan)
                if plan['status']=='PLAN':quota.add((group,day))
        if n%10==0 or n==len(selected):print(json.dumps(dict(stage='signals',done=n,total=len(selected),records=len(records))),flush=True)
    records.sort(key=lambda p:(p['time'],p['group'],p['symbol']))
    (out/'candidates.json').write_text(json.dumps(records,separators=(',',':')))
    (out/'signal_audit.json').write_text(json.dumps(dict(hourly=dict(audit),daily=dict(daily_counts)),indent=2))
    return records


def write_csv(path,rows):
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with Path(path).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        for r in rows:w.writerow({k:json.dumps(v,separators=(',',':'),ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})


def metrics(rows):
    valid=sorted([r for r in rows if r['status']=='CLOSED'],key=lambda r:(r['exit_ms'],r['symbol']))
    if not valid:return dict(n=0,mean_net_pct=None,stress_mean_pct=None)
    n=len(valid);count=Counter(r['outcome'] for r in valid);running=peak=dd=0.;loss=longest=0
    for r in valid:
        running+=r['net_r'];peak=max(peak,running);dd=max(dd,peak-running)
        loss=loss+1 if r['net_pct']<0 else 0;longest=max(longest,loss)
    return dict(n=n,target_pct=100*count['HEDEF']/n,stop_pct=100*count['STOP']/n,timeout_pct=100*count['TIMEOUT']/n,
                win_pct=100*sum(r['net_pct']>0 for r in valid)/n,mean_net_pct=statistics.mean(r['net_pct'] for r in valid),
                stress_mean_pct=statistics.mean(r['net_pct']-.30 for r in valid),mean_gross_pct=statistics.mean(r['gross_pct'] for r in valid),
                mean_net_r=statistics.mean(r['net_r'] for r in valid),total_net_r=sum(r['net_r'] for r in valid),max_drawdown_r=dd,longest_loss_streak=longest,
                funding_pct_sum=sum(r['funding_pct'] for r in valid),resolutions=dict(Counter(r['resolution'] for r in valid)))


def assess(main,a,b):
    checks=dict(at_least_200=main['n']>=200,positive=main['n']>0 and main['mean_net_pct']>0,
                stress_positive=main['n']>0 and main['stress_mean_pct']>0,
                beats_control_a=bool(main['n'] and a['n'] and main['mean_net_pct']>a['mean_net_pct']),
                beats_control_b=bool(main['n'] and b['n'] and main['mean_net_pct']>b['mean_net_pct']))
    return ('GEÇTİ' if all(checks.values()) else 'BAŞARISIZ'),checks


def report(root,out,rows,seal,records):
    out=Path(out);manifest=json.loads((Path(root)/'manifest.json').read_text());end=manifest['end'];groups={}
    for g in ('MAIN','CONTROL_A','CONTROL_B'):
        rr=[r for r in rows if r['group']==g]
        groups[g]=dict(all=metrics(rr),long=metrics([r for r in rr if r['side']=='LONG']),short=metrics([r for r in rr if r['side']=='SHORT']),
                       last12=metrics([r for r in rr if r['entry_ms']>=ms('2025-10-23')]),
                       years={str(y):metrics([r for r in rr if iso(r['entry_ms'])[:4]==str(y)]) for y in range(2020,2027)},
                       statuses=dict(Counter(r['status'] for r in rr)))
    status,checks=assess(*(groups[g]['all'] for g in ('MAIN','CONTROL_A','CONTROL_B')))
    summary=dict(status=status,checks=checks,rules_hash=RULES_HASH,start=manifest['start'],end=end,groups=groups,
                 data_issues=[dict(id=r['id'],reason=r.get('reason')) for r in rows+records if r['status']=='UNVERIFIED'],
                 candidate_statuses=dict(Counter(r['status'] for r in records)),source_seal=seal)
    (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False))
    def fmt(x):return 'doğrulanmadı' if x is None else f'{x:.5f}'
    text=['# Saatlik Motor — '+status,'',f"Dönem: {iso(manifest['start'])} – {iso(end)} (UTC; son tam gün 22 Ekim 2026).",
          'Binance USDT-M perpetual; gerçek funding; toplam %0,30 masraf. Parametre denemesi: **0**. Canlı: **KAPALI**.','',
          '| Grup | İşlem | Hedef % | Stop % | Süre % | Ortalama net getiri % | %0,60 masrafta % |',
          '|---|---:|---:|---:|---:|---:|---:|']
    for g in groups:
        m=groups[g]['all'];text.append(f"| {g} | {m['n']} | {fmt(m.get('target_pct'))} | {fmt(m.get('stop_pct'))} | {fmt(m.get('timeout_pct'))} | {fmt(m['mean_net_pct'])} | {fmt(m['stress_mean_pct'])} |")
    if all(groups[g]['all']['n'] for g in groups):
        main_mean=groups['MAIN']['all']['mean_net_pct']
        text+=['',f"Ana strateji − kontrol A: {main_mean-groups['CONTROL_A']['all']['mean_net_pct']:+.5f} yüzde puan; ana strateji − kontrol B: {main_mean-groups['CONTROL_B']['all']['mean_net_pct']:+.5f} yüzde puan."]
    text+=['','## Önceden sabit başarı koşulları','']+[f"- {k}: {'sağlandı' if v else 'sağlanmadı'}" for k,v in checks.items()]
    text+=['','## LONG / SHORT ve son 12 ay','','| Grup | Kesit | İşlem | Ortalama net % | Hedef % | Stop % | Süre % |','|---|---|---:|---:|---:|---:|---:|']
    for g,v in groups.items():
        for key in ('long','short','last12'):
            m=v[key];text.append(f"| {g} | {key} | {m['n']} | {fmt(m['mean_net_pct'])} | {fmt(m.get('target_pct'))} | {fmt(m.get('stop_pct'))} | {fmt(m.get('timeout_pct'))} |")
    text+=['','Son 12 ay: 23 Ekim 2025 – 23 Ekim 2026 UTC.','', '## Yıl yıl','','| Grup | Yıl | İşlem | Hedef % | Stop % | Süre % | Ortalama net % | Toplam net R |','|---|---|---:|---:|---:|---:|---:|---:|']
    for g,v in groups.items():
        for y,m in v['years'].items():text.append(f"| {g} | {y} | {m['n']} | {fmt(m.get('target_pct'))} | {fmt(m.get('stop_pct'))} | {fmt(m.get('timeout_pct'))} | {fmt(m['mean_net_pct'])} | {fmt(m.get('total_net_r'))} |")
    text+=['','## Veri ve yöntem','',
      '- Kontrol A: yön listesindeki coinlerin ters yönlü 24 saat kırılımları. Kontrol B: aynı aylık ilk30 evreninde iki yön listesinde de olmayan coinlerin LONG/SHORT kırılımları. Her iki grupta aynı hacim, plan, günlük kota, masraf ve funding kuralları; gelecek getiriyle eşleştirme yok.',
      '- Kırılım önceki24 saatten, hacim önceki20 saatten gelir; sinyal mumu ikisinden de çıkarılır. Hacim ≥1,5 kat. Günlük liste UTC00:00 kapanışıyla güncellenir; tam00:00 saatlik sinyali o anda kapanmış günlük mumu kullanır.',
      '- Giriş ertesi saat açılışı; stop sinyal mumunun dibi/tepesi ±0,5 ATR14; hedef brüt1,5R; süre çıkışı giriş+2h açılışı. Olumsuz stop gap açılıştan, hedef gap ihtiyatlı olarak hedeften.',
      '- EMA SMA ile başlar; ATR Wilder14. Eksik mumda göstergeler yeniden yeterli kesintisiz geçmiş bekler. Sentetik fiyat/funding yok. Bir coin/grup için UTC sinyal-kapanış gününde en fazla1 plan; ek portföy limiti yok.',
      '- Gerçek funding giriş nominaline, yön işaretiyle uygulanır. Giriş anındaki settlement hariç, çıkış anındaki dahil. Milisaniye jitter saniyeye indirilir; funding kaydı işlemi iki yandan çevrelemeli, aralık8 saati aşmamalı.',
      '- Aynı saatte SL/TP: 1m sırası; 1m yok/tutarsız veya aynı1m içinde ikisi varsa STOP. Tek isabetli saatte çıkış zamanı saat sonundan1ms önce; saat içi funding sırası belirsizse doğrulanmadı.',
      '- Delist arşivleri dahil. Yalnız stablecoinler hariç; asgari yaş filtresi yok. Yeni listelenenlerde önceki30 gün içindeki mevcut günlerin hacmi toplanır; EMA50 doğal 50 mum başlangıcı gerektirir. Ara boşluğu olan30 günlük pencerenin sıralaması doğrulanamaz; o coin/ay atlanır. Arşivin başlamasından önceki dönem varsayılmaz.',
      f"- Veri hazırlama hatası: {len(manifest['errors'])}; işlem sonucu doğrulanamayan: {len(summary['data_issues'])}. Bunlar net getiri hesabına katılmadı; kayıtlar CSV ve data_manifest.json içinde korunuyor.",
      f"- Veri dizilerinde saatlik boşluk aralığı: {sum(len(v.get('hourly_gaps',[])) for v in manifest['symbols'])}; günlük boşluk aralığı: {sum(len(v.get('daily_gaps',[])) for v in manifest['symbols'])}. Gösterge başlangıcının/boşluğunun etkilediği adaylar signal_audit.json içindedir.",
      '- Sonuçlar kapalı işlem örneklemi içindir. Ortalama getiri, portföy getirisi değildir; eşzamanlı toplam kaldıraç/sermaye modeli bu deneyde tanımlanmadı.',
      '- Kaynak: https://data.binance.vision/ ; gerçek funding tamamlama: https://www.binance.com/fapi/v1/fundingRate . ZIPler resmi SHA256 ile doğrulandı.','',
      '## Ek muhasebe','', '```json',json.dumps({g:dict(statuses=v['statuses'],resolutions=v['all'].get('resolutions',{}),mean_gross_pct=v['all'].get('mean_gross_pct'),max_drawdown_r=v['all'].get('max_drawdown_r')) for g,v in groups.items()},indent=2,ensure_ascii=False),'```','',
      '**'+status+'**. Kurallar değiştirilmedi. Canlı workflow ve Telegram gönderimi açılmadı.']
    (out/'report.md').write_text('\n'.join(text)+'\n')
    write_csv(out/'trades.csv',rows)
    return summary


def run(root,archive_root,out,workers=12):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest['errors']:raise ValueError('PREPARATION_ERRORS_MUST_BE_REVIEWED')
    for r in manifest['symbols']:
        if hashlib.sha256((root/'prepared'/(r['symbol']+'.json.gz')).read_bytes()).hexdigest()!=r['sha256']:raise ValueError('DATA_CHANGED')
    files=['hourly_motor_engine.py','hourly_motor_data.py','hourly_motor_backtest.py','hourly_motor_live.py','hourly_motor_protocol.json','test_hourly_motor.py','trend_breakout_backtest.py']
    seal=dict(rules_hash=RULES_HASH,files={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files},
              manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),universe_sha256=hashlib.sha256((root/'universe.json').read_bytes()).hexdigest())
    lock=out/'sealed_run.json'
    if lock.exists():
        old=json.loads(lock.read_text())
        if old['seal']!=seal:raise ValueError('SEALED_SOURCE_OR_DATA_CHANGED')
        if old['status']=='COMPLETE':raise ValueError('ALREADY_COMPLETED_NO_RERUN')
    else:lock.write_text(json.dumps(dict(status='RUNNING',seal=seal),indent=2))
    records=json.loads((out/'candidates.json').read_text()) if (out/'candidates.json').exists() else candidates(root,out)
    plans=[p for p in records if p['status']=='PLAN'];done={};journal=out/'outcomes.jsonl'
    if journal.exists():
        for line in journal.read_text().splitlines():
            r=json.loads(line);done[r['id']]=r
    by_symbol=defaultdict(list)
    for p in plans:
        if p['id'] not in done:by_symbol[p['symbol']].append(p)
    archive=Archive(archive_root)
    def one(s,pp):
        x=load_symbol(root,s);hours={r[0]:Candle(*r) for r in x['hourly']}
        @lru_cache(maxsize=8)
        def minutes(symbol,at):return archive.candles(symbol,'1m',iso(at)[:10],daily=True)
        return [simulate_trade(p,hours,minutes,x['funding'],manifest['end']) for p in pp]
    with journal.open('a') as f,ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(one,s,pp):s for s,pp in by_symbol.items()}
        for n,future in enumerate(as_completed(jobs),1):
            for r in future.result():done[r['id']]=r;f.write(json.dumps(r,separators=(',',':'))+'\n')
            f.flush()
            if n%10==0 or n==len(jobs):print(json.dumps(dict(stage='fixed_replay',done=n,total=len(jobs),trades=len(done))),flush=True)
    rows=[done[p['id']] for p in plans]
    summary=report(root,out,rows,seal,records)
    write_csv(out/'all_candidates.csv',records)
    (out/'data_manifest.json').write_text((root/'manifest.json').read_text())
    (out/'universe.json').write_text((root/'universe.json').read_text())
    lock.write_text(json.dumps(dict(status='COMPLETE',seal=seal,report_sha256=hashlib.sha256((out/'report.md').read_bytes()).hexdigest()),indent=2))
    print(json.dumps(dict(status=summary['status'],main=summary['groups']['MAIN']['all']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--archive-cache',required=True);p.add_argument('--out',required=True);p.add_argument('--workers',type=int,default=12)
    p.add_argument('--run',action='store_true',required=True);a=p.parse_args();run(a.data,a.archive_cache,a.out,a.workers)
