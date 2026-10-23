"""Historical replay and reporting. Uses the identical evaluate() used by live.

Run --prepare first (data only). --run seals a one-use lock before calculating
holdout results. Do not delete the lock to tune or silently repeat a test.
"""
import argparse
import bisect
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import time
from trend_breakout_engine import (HOUR, DAY, MINUTE, RULES, RULES_HASH, Candle, Series,
                                   evaluate, make_plan, choose_plans)
from trend_breakout_data import Archive, ms, iso, month, months


def prepare(archive, workers=12):
    universe = json.loads((archive.root/'universe.json').read_text())
    bounds = {}
    for m, rows in universe['months'].items():
        for row in rows:
            s = row['symbol']; at = ms(m+'-01')
            bounds[s] = min(bounds.get(s, at), at)
    end = universe['end']
    failures = []
    def one(symbol, first):
        start = first-60*DAY
        target = archive.root/'prepared'/f'{symbol}.json'
        if target.exists():
            cached=json.loads(target.read_text())
            if cached['start']==start and cached['end']==end:
                return dict(symbol=symbol,source=cached['source'],h=len(cached['h']),q=len(cached['q']),
                            funding_months=len(cached['funding_coverage']),funding_errors=cached['funding_errors'])
        price_error=None
        try:
            h = archive.range(symbol, '1h', start, end)
            q = archive.range(symbol, '4h', start, end)
        except Exception as e:
            price_error=type(e).__name__;h=[];q=[]
        source = 'futures'
        # Whole-symbol fallback only; never stitch a futures and spot series.
        if not h or not q:
            h = archive.range(symbol, '1h', start, end, 'spot')
            q = archive.range(symbol, '4h', start, end, 'spot'); source = 'spot'
        if not h or not q:
            raise ValueError('NO_PRICE_HISTORY')
        Series(h, HOUR); Series(q, 4*HOUR)
        funding, funding_coverage, funding_errors = [], [], []
        for m in months(start, end):
            try:
                rows = archive.funding(symbol, m)
                funding.extend(rows)
                if rows:
                    funding_coverage.append(m)
            except Exception as e:
                funding_errors.append(dict(month=m, error=type(e).__name__))
        out = dict(symbol=symbol, source=source, start=start, end=end, futures_download_error=price_error,
                   h=[asdict(b) for b in h], q=[asdict(b) for b in q], funding=funding,
                   funding_coverage=funding_coverage, funding_errors=funding_errors)
        target.parent.mkdir(exist_ok=True); target.write_text(json.dumps(out, separators=(',', ':')))
        return dict(symbol=symbol, source=source, h=len(h), q=len(q),
                    funding_months=len(funding_coverage), funding_errors=funding_errors)
    records = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        jobs = {ex.submit(one, s, first): s for s, first in bounds.items()}
        for i, f in enumerate(as_completed(jobs), 1):
            try:
                records.append(f.result())
            except Exception as e:
                failures.append(dict(symbol=jobs[f], error=type(e).__name__, detail=str(e)[:250]))
            print(json.dumps(dict(stage='prepare', done=i, total=len(jobs), errors=len(failures))), flush=True)
    manifest = dict(symbols=records, errors=failures, universe_errors=universe['errors'])
    (archive.root/'prepared_manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest


def touch(plan, b):
    long = plan['side'] == 'LONG'
    stop_hit = b.low <= plan['stop'] if long else b.high >= plan['stop']
    tp_hit = b.high >= plan['target'] if long else b.low <= plan['target']
    return stop_hit, tp_hit


def gap_exit(plan, b):
    sign = 1 if plan['side'] == 'LONG' else -1
    if sign*(b.open-plan['stop']) <= 0:
        return 'STOP', b.open, b.open_ms
    if sign*(b.open-plan['target']) >= 0:
        return 'HEDEF', plan['target'], b.open_ms
    return None


def resolve_hour(plan, bar, minute_loader):
    gap = gap_exit(plan, bar)
    if gap:
        return dict(outcome=gap[0], exit=gap[1], exit_ms=gap[2], resolution='OPEN_GAP')
    sl, tp = touch(plan, bar)
    if not sl and not tp:
        return None
    resolution = '1H_SINGLE'
    if sl and tp:
        try:
            minutes = [b for b in minute_loader(plan['symbol'], bar.open_ms)
                       if bar.open_ms <= b.open_ms < bar.close_ms]
        except Exception:
            minutes = []
        valid = (len(minutes) == 60 and all(b.open_ms == bar.open_ms+i*MINUTE and
                 b.close_ms == b.open_ms+MINUTE for i,b in enumerate(minutes)))
        if valid:
            # Detect corrupt/inconsistent archives rather than inventing an ordering.
            valid = (abs(minutes[0].open-bar.open) <= max(1e-10, bar.open*1e-8) and
                     abs(max(b.high for b in minutes)-bar.high) <= max(1e-10, bar.high*1e-8) and
                     abs(min(b.low for b in minutes)-bar.low) <= max(1e-10, bar.low*1e-8))
        if valid:
            for b in minutes:
                gap = gap_exit(plan, b)
                if gap:
                    return dict(outcome=gap[0], exit=gap[1], exit_ms=gap[2], resolution='1M_RESOLVED')
                a, c = touch(plan, b)
                if a or c:
                    return dict(outcome='STOP' if a else 'HEDEF', exit=plan['stop'] if a else plan['target'],
                                exit_ms=b.close_ms-1, resolution='1M_BOTH_STOP' if a and c else '1M_RESOLVED')
        resolution = '1M_MISSING_OR_INVALID_STOP'
    return dict(outcome='STOP' if sl else 'HEDEF', exit=plan['stop'] if sl else plan['target'],
                exit_ms=bar.close_ms-1, resolution=resolution)


def movement24(plan, rows):
    rows = rows[:24]
    if len(rows) != 24 or any(b.open_ms != plan['entry_ms']+i*HOUR for i,b in enumerate(rows)):
        return dict(mfe_pct=None, mae_pct=None, mfe_r=None, mae_r=None, direction_correct=None)
    entry = plan['entry']; long = plan['side'] == 'LONG'
    hi, lo = max(b.high for b in rows), min(b.low for b in rows)
    mfe = max(0., 100*((hi-entry) if long else (entry-lo))/entry)
    mae = max(0., 100*((entry-lo) if long else (hi-entry))/entry)
    return dict(mfe_pct=mfe, mae_pct=mae, mfe_r=mfe/plan['risk_pct'],
                mae_r=mae/plan['risk_pct'], direction_correct=mfe > mae)


def funding_cost(plan, exit_ms, funding, coverage, hours):
    """Funding percentage convention: sum signed published rates.

    Archive funding timestamps have millisecond calculation jitter. Round down
    to second, exclude an entry exactly at the settlement boundary.
    Uncovered months prorate the explicit conservative fallback for both sides.
    """
    start, finish = plan['entry_ms'], exit_ms
    cost, assumed_ms, actual_events = 0., 0, 0
    for m in months(start, finish+1):
        a = ms(m+'-01')
        next_m = list(months(a, a+32*DAY))[1]
        z = ms(next_m+'-01')
        left, right = max(start, a), min(finish, z)
        if m not in coverage:
            assumed_ms += max(0, right-left)
    sign = 1 if plan['side'] == 'LONG' else -1
    for f in funding:
        t = (int(f['time'])//1000)*1000
        if not start < t <= finish or month(t) not in coverage:
            continue
        cost += sign*100*f['rate']; actual_events += 1
    cost += .01*assumed_ms/(8*HOUR)
    return dict(funding_pct=cost, funding_events=actual_events,
                funding_assumed_hours=assumed_ms/HOUR,
                funding_mode='ACTUAL_SIGNED_RATE' if not assumed_ms else 'PARTIAL_OR_FULL_FALLBACK')


def simulate_trade(plan, hours, minute_loader, funding=(), coverage=()):
    idx = bisect.bisect_left([b.open_ms for b in hours], plan['entry_ms'])
    rows = hours[idx:idx+168]
    base = dict(plan, **movement24(plan, rows))
    outcome = None
    for k, b in enumerate(rows):
        if b.open_ms != plan['entry_ms']+k*HOUR:
            return dict(base, outcome='UNVERIFIED_DATA_GAP', exit_ms=None, net_r=None)
        outcome = resolve_hour(plan, b, minute_loader)
        if outcome:
            break
    if outcome is None:
        if len(rows) < 168:
            return dict(base, outcome='UNVERIFIED_INCOMPLETE', exit_ms=None, net_r=None)
        outcome = dict(outcome='TIMEOUT', exit=rows[-1].close, exit_ms=rows[-1].close_ms-1, resolution='168H')
    base.update(outcome)
    base.update(funding_cost(plan, outcome['exit_ms'], funding, coverage, hours))
    sign = 1 if plan['side'] == 'LONG' else -1
    gross = 100*sign*(base['exit']/plan['entry']-1)
    base.update(gross_return_pct=gross, cost_pct=.30,
                net_r=(gross-.30-base['funding_pct'])/plan['risk_pct'],
                stress_net_r=(gross-.60-base['funding_pct'])/plan['risk_pct'])
    return base


def portfolio(trades, limited):
    """Replay identical candidates independently; no best-trade hindsight selection."""
    accepted, skipped, active = [], [], []
    grouped = defaultdict(list)
    for t in trades:
        grouped[t['entry_ms']].append(t)
    for at in sorted(grouped):
        active = [t for t in active if t.get('exit_ms') is None or t['exit_ms'] > at]
        take, reject = choose_plans(grouped[at], active, limited)
        accepted.extend(take); skipped.extend(reject); active.extend(take)
    return accepted, skipped


def metrics(trades):
    valid = [t for t in trades if t.get('net_r') is not None]
    ordered = sorted(valid, key=lambda t: (t['exit_ms'], t['symbol']))
    total, peak, drawdown, streak, worst = 0., 0., 0., 0, 0
    batches = defaultdict(float)
    for t in ordered:
        batches[t['exit_ms']] += t['net_r']
        streak = streak+1 if t['net_r'] < 0 else 0; worst = max(worst, streak)
    for at in sorted(batches):
        total += batches[at]; peak = max(peak, total); drawdown = max(drawdown, peak-total)
    directional = [t for t in valid if t.get('direction_correct') is not None]
    by_symbol = defaultdict(float)
    for t in valid:
        by_symbol[t['symbol']] += t['net_r']
    best = sorted(by_symbol, key=lambda s: (-by_symbol[s], s))[:3]
    removed = [t for t in valid if t['symbol'] not in best]
    avg = lambda rows, key: statistics.mean(t[key] for t in rows) if rows else None
    return dict(trades=len(valid), unverified=len(trades)-len(valid), mean_net_r=avg(valid, 'net_r'),
                total_net_r=sum(t['net_r'] for t in valid), max_drawdown_r=drawdown,
                longest_loss_streak=worst, target_first_pct=100*sum(t['outcome']=='HEDEF' for t in valid)/len(valid) if valid else None,
                direction_accuracy_pct=100*sum(t['direction_correct'] for t in directional)/len(directional) if directional else None,
                direction_samples=len(directional), mean_net_r_cost_060=avg(valid, 'stress_net_r'),
                best_three=best, mean_net_r_without_best_three=avg(removed, 'net_r'),
                trades_without_best_three=len(removed), outcomes=dict(Counter(t['outcome'] for t in trades)),
                resolutions=dict(Counter(t.get('resolution','UNKNOWN') for t in trades)),
                fallback_funding_trades=sum(t.get('funding_assumed_hours',0)>0 for t in valid))


def verdict(m, coverage_ok=True):
    if m['trades'] < 100 or not coverage_ok or m['unverified']:
        return 'YETERSİZ VERİ'
    return 'BAŞARILI' if all(m[k] is not None and m[k] > 0 for k in
        ('mean_net_r', 'mean_net_r_cost_060', 'mean_net_r_without_best_three')) else 'BAŞARISIZ'


def write_csv(path, rows):
    keys = sorted(set().union(*(set(r) for r in rows))) if rows else ['symbol','side','entry_ms','entry','stop','target','outcome','net_r']
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)


def run(archive, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    protocol = json.loads(Path(__file__).with_name('trend_breakout_protocol.json').read_text())
    universe = json.loads((archive.root/'universe.json').read_text())
    manifest = json.loads((archive.root/'prepared_manifest.json').read_text())
    start, end = universe['start'], universe['end']
    split = start + int((end-start)*.70//HOUR)*HOUR
    identity = dict(rules_hash=RULES_HASH, protocol=protocol, start=start, split=split, end=end,
                    variants_tried=0, files={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(Path(__file__).parent.glob('trend_breakout_*.py'))},
                    universe_sha256=hashlib.sha256((archive.root/'universe.json').read_bytes()).hexdigest())
    # Exclusive create: an accidental rerun cannot silently inspect the holdout again.
    with (output/'locked_test_seal.json').open('x') as f:
        json.dump(identity, f, indent=2)
    membership = {m:{r['symbol'] for r in rows} for m,rows in universe['months'].items()}
    candidates, rejected, counts = [], [], Counter()
    minute_cache = {}
    source_by_symbol = {r['symbol']:r['source'] for r in manifest['symbols']}
    def minute_loader(symbol, at):
        key = (symbol, iso(at)[:10])
        if key not in minute_cache:
            if len(minute_cache) >= 32:
                minute_cache.pop(next(iter(minute_cache)))
            minute_cache[key] = archive.candles(symbol, '1m', key[1], source_by_symbol[symbol], daily=True)
        return [b for b in minute_cache[key] if at <= b.open_ms < at+HOUR]
    for n, row in enumerate(sorted(manifest['symbols'], key=lambda x:x['symbol']),1):
        s = row['symbol']; data = json.loads((archive.root/'prepared'/f'{s}.json').read_text())
        h, q = Series([Candle(**b) for b in data['h']], HOUR), Series([Candle(**b) for b in data['q']], 4*HOUR)
        for i,b in enumerate(h.bars):
            t = b.close_ms
            if not start <= t < end or s not in membership.get(month(t),set()):
                continue
            phase = 'development' if t < split else 'locked_test'
            boundary = split if phase == 'development' else end
            if t >= boundary-168*HOUR:
                counts['BOUNDARY_PURGE_168H'] += 1; continue
            sig = evaluate(s,h,q,t); counts[sig['reason']] += 1
            if sig['status'] != 'SIGNAL':
                continue
            if i+1 >= len(h.bars) or h.bars[i+1].open_ms != t:
                rejected.append(dict(sig, phase=phase, reason='NEXT_OPEN_MISSING')); continue
            p = make_plan(sig, h.bars[i+1].open, t, data['source']); p['phase'] = phase
            if p['status'] != 'PLAN':
                rejected.append(p); continue
            candidates.append(simulate_trade(p,h.bars,minute_loader,data['funding'],data['funding_coverage']))
        print(json.dumps(dict(stage='replay', symbols=n, total=len(manifest['symbols']), candidates=len(candidates))),flush=True)
    tables, all_trades, all_skips = {}, [], []
    for limited in (False, True):
        mode = 'limited' if limited else 'unlimited'
        for phase in ('development','locked_test'):
            trades, skips = portfolio([t for t in candidates if t['phase']==phase], limited)
            trades = [dict(t, portfolio=mode) for t in trades]
            all_trades.extend(trades); all_skips.extend(dict(t,portfolio=mode) for t in skips)
            for side in ('ALL','LONG','SHORT'):
                group = trades if side == 'ALL' else [t for t in trades if t['side']==side]
                tables[f'{mode}/{phase}/{side}'] = metrics(group)
    coverage_ok = not manifest['errors'] and not universe['errors']
    summary = dict(identity=identity, data_coverage_ok=coverage_ok, manifest=manifest,
                   evaluation_counts=dict(counts), rejected_plan_counts=dict(Counter(t['reason'] for t in rejected)),
                   skipped_counts=dict(Counter(t['reason'] for t in all_skips)), results=tables,
                   verdicts={mode:verdict(tables[f'{mode}/locked_test/ALL'],coverage_ok) for mode in ('limited','unlimited')})
    monthly = []
    for mode in ('limited','unlimited'):
        for phase in ('development','locked_test'):
            for m in months(start,end):
                group = [t for t in all_trades if t['portfolio']==mode and t['phase']==phase and month(t['entry_ms'])==m]
                if group:
                    monthly.append(dict(portfolio=mode,phase=phase,month=m,**metrics(group)))
    summary['monthly'] = monthly
    (output/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False))
    write_csv(output/'trades.csv',all_trades)
    write_csv(output/'skipped_and_rejected.csv',rejected+all_skips)
    write_csv(output/'monthly.csv',[{k:r[k] for k in ('portfolio','phase','month','trades','total_net_r','mean_net_r')} for r in monthly])
    report(summary,output/'report.md')
    return summary


def report(s, path):
    lines = ['# Trend Kırılım Motoru — geçmiş test', '',
             f"**Sınırlı portföy: {s['verdicts']['limited']} · Sınırsız: {s['verdicts']['unlimited']}**",'',
             f"Dönem: {iso(s['identity']['start'])} — {iso(s['identity']['end'])}. Ayrım: {iso(s['identity']['split'])}.",
             'Orijinal kurallar; 0 değişiklik. Kilitli test tek çalıştırma. Canlı workflow KAPALI.', '',
             '| Portföy / dönem / yön | İşlem | Ort. net R | Toplam R | Maks. düşüş R | Kayıp serisi | Hedef önce % | Yön isabet % | %0,60 ort. R | En iyi 3 hariç ort. R |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    def f(x):
        return 'doğrulanmadı' if x is None else f'{x:.4f}' if isinstance(x,float) else str(x)
    keys = ('trades','mean_net_r','total_net_r','max_drawdown_r','longest_loss_streak','target_first_pct','direction_accuracy_pct','mean_net_r_cost_060','mean_net_r_without_best_three')
    for label,r in s['results'].items():
        lines.append('| '+label+' | '+' | '.join(f(r[k]) for k in keys)+' |')
    lines += ['', '## Veri ve uygulama sınırları',
              '- Futures arşivi ve resmi SHA256 doğrulaması; fallback kullanılan parite kaynakları summary.json ve CSV’de.',
              '- Evren: tüm arşiv sembolleri, önceki 30 günlük USDT hacmi; 60 günlük geçmiş. Stablecoin/endeks dışlama listesi veri modülünde açıkça kayıtlı; tarihsel ürün sınıflandırması bağımsız exchangeInfo arşiviyle doğrulanmadı.',
              '- Funding yüzdesi: elde tutma aralığındaki gerçek oranların yön işaretli toplamı. Eksik aylar iki yön için de %0,01 / 8 saat gider varsayımı. Sabit giriş nominali üzerinden yüzde muhasebesi; mark-price ile değişen nominal tutar ayrıca modellenmedi.',
              '- Stop/TP aynı saatte: tam 1m veriyle sıra; aynı dakikada ikisi veya eksik/tutarsız 1m: STOP. Sayılar aşağıda.',
              '- Geliştirme/test sınırı ve dönem sonundaki son 168 sinyal saati sonuçtan bağımsız dışlandı.',
              '- Düşüş: kapanan işlemlerden oluşan R eğrisi; açık işlemlerin anlık toplam zararını ölçmez.',
              '- Test varsayımsal dolumdur. Günlük kâr veya günlük sinyal garantisi değildir.',
              f"- Veri kapsamı: {'tam indirme kontrolü geçti' if s['data_coverage_ok'] else 'EKSİK — sonuç doğrulanmadı'}. Ürün sınıflandırması ve funding fiyatı sınırlamaları yukarıda.", '',
              '## Sonuç dağılımı ve 1m çözümü']
    for label,r in s['results'].items():
        if label.endswith('/ALL'):
            lines += [f"- {label}: {r['outcomes']}; çözüm {r['resolutions']}; funding varsayımlı {r['fallback_funding_trades']}; en iyi 3 {r['best_three']}."]
    lines += ['', '## Ay ay toplam net R', '| Portföy | Dönem | Ay | İşlem | Toplam R |', '|---|---|---|---:|---:|']
    for r in s['monthly']:
        lines.append(f"| {r['portfolio']} | {r['phase']} | {r['month']} | {r['trades']} | {r['total_net_r']:.4f} |")
    lines += ['', '## Atlananlar', '```json', json.dumps(dict(evaluations=s['evaluation_counts'], plans=s['rejected_plan_counts'], limits=s['skipped_counts']),indent=2,ensure_ascii=False),'```', '',
              'Tam işlemler: trades.csv; reddedilenler: skipped_and_rejected.csv; ayrıntı: summary.json; değişmez protokol: locked_test_seal.json.',
              'Kaynaklar: https://github.com/binance/binance-public-data · https://data.binance.vision/']
    path.write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--cache',required=True); p.add_argument('--output',default='research/trend_breakout_v1/results')
    p.add_argument('--prepare',action='store_true'); p.add_argument('--run',action='store_true'); p.add_argument('--workers',type=int,default=12)
    a = p.parse_args(); archive = Archive(a.cache)
    if a.prepare: prepare(archive,a.workers)
    if a.run: run(archive,a.output)
