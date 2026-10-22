"""Offline LONG 15/30/60 minute entry diagnostic; production code is immutable."""
import argparse
import bisect
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import long_short_swing as swing
from long_short_swing_outcomes import initial, advance, result
from research.short_v2.replay_short import load_evidence
from research.swing_v1.replay import archive

HERE = Path(__file__).parent
HOUR, MINUTE = swing.HOUR, swing.MINUTE
DAY = 24*HOUR
VERSION = 'LONG_ENTRY_TIMEFRAME_DIAGNOSTIC_V1_2026_10_23'
PERIODS = (15, 30, 60)
START = int(datetime(2026, 8, 8, tzinfo=timezone.utc).timestamp()*1000)
END = int(datetime(2026, 8, 24, tzinfo=timezone.utc).timestamp()*1000)
PROTECTED = ('long_short_swing.py', 'long_short_swing_outcomes.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def aggregate(rows, minutes):
    """Never complete partial buckets with future/forward-filled values."""
    period = minutes*MINUTE
    groups = defaultdict(list)
    for b in rows:
        groups[b['open_ms']//period*period].append(b)
    output, rejected = [], Counter()
    for start, group in sorted(groups.items()):
        try:
            if len(group) != minutes or group[0]['open_ms'] != start:
                raise ValueError('PARTIAL_BUCKET')
            swing.check_bars(group, MINUTE)
            for b in group:
                q, buy = b.get('quote_volume'), b.get('taker_buy_quote')
                if not swing.finite(q) or q < 0 or not swing.finite(buy) or not 0 <= buy <= q:
                    raise ValueError('INVALID_VOLUME')
            output.append(dict(open_ms=start, close_ms=start+period-1,
                open=group[0]['open'], close=group[-1]['close'],
                high=max(b['high'] for b in group), low=min(b['low'] for b in group),
                quote_volume=sum(b['quote_volume'] for b in group),
                taker_buy_quote=sum(b['taker_buy_quote'] for b in group)))
        except (ValueError, KeyError, TypeError) as exc:
            rejected[str(exc)] += 1
    return output, dict(rejected)


def context(hours, hour_start):
    stamps = [b['open_ms'] for b in hours]
    i = bisect.bisect_left(stamps, hour_start)
    rows = hours[max(0, i-168):i]
    if len(rows) != 168 or rows[-1]['close_ms'] != hour_start-1:
        return None
    try:
        swing.check_bars(rows, HOUR, hour_start)
    except ValueError:
        return None
    prior = rows[-72:]
    lows = swing.pivots(prior, 'low')
    floor = min(b['low'] for b in prior)
    atr = swing.atr(rows)
    return dict(hour_start=hour_start, rows=rows, trigger=max(b['high'] for b in prior),
        origin_price=min(b['low'] for b in rows), atr_1h=atr,
        stop=(lows[-1]['price'] if lows else floor)-.25*atr,
        quote_volume_24h=sum(b['quote_volume'] for b in rows[-24:]))


def candidate(symbol, bar, by_open, ctx, minutes):
    if ctx is None:
        return None, 'HOUR_CONTEXT_MISSING'
    if ctx['quote_volume_24h'] < 2_000_000:
        return None, 'ILLIQUID'
    if bar['close'] <= ctx['trigger']:
        return None, 'NO_BREAK'
    history = [by_open.get(bar['open_ms']-d*DAY) for d in range(1, 8)]
    if any(b is None for b in history):
        return None, 'VOLUME_BASELINE_MISSING'
    baseline = statistics.median(b['quote_volume'] for b in history)
    if baseline <= 0 or bar['quote_volume'] <= 0:
        return None, 'VOLUME_BASELINE_ZERO'
    multiple = bar['quote_volume']/baseline
    buy = bar['taker_buy_quote']/bar['quote_volume']
    if multiple < 3 or buy <= .5:
        return None, 'VOLUME_OR_BUY_WEAK'
    p = {k: ctx[k] for k in ('trigger', 'origin_price', 'atr_1h', 'stop', 'quote_volume_24h')}
    p.update(version=VERSION, symbol=symbol, model=f'COMMON_HOUR_CONTEXT_LONG_{minutes}M',
        direction='LONG', timeframe_minutes=minutes, price=bar['close'],
        confirmation_ms=bar['close_ms'], observed_ms=bar['close_ms']+1000,
        context_end_ms=ctx['hour_start']-1, volume_multiple=multiple, buy_share=buy,
        obstacle=swing.obstacle(ctx['rows'], bar['close'], 'LONG'), research_only=True)
    reason = swing.geometry(p, p['price'])
    return (None, reason) if reason else (p, 'READY')


def select(symbol, minutes, bars, hours, contexts, start=START, end=END):
    by_open = {b['open_ms']: b for b in bars}
    hour_by_open = {b['open_ms']: b for b in hours}
    plans, counts = [], Counter()
    active, inside, previous_hour = None, 0, None
    for b in bars:
        if not start <= b['open_ms'] < end:
            continue
        t = b['open_ms']//HOUR*HOUR
        if previous_hour != t:
            if active:
                prior = hour_by_open.get(t-HOUR)
                if previous_hour is not None and t != previous_hour+HOUR:
                    inside = 0
                inside = inside+1 if prior and prior['close'] <= active['trigger'] else 0
                if inside >= 6:
                    active = None
            previous_hour = t
        p, reason = candidate(symbol, b, by_open, contexts.get(t), minutes)
        counts[reason] += 1
        if p:
            if active:
                counts['EPISODE_SUPPRESSED'] += 1
            else:
                active, inside = p, 0
                plans.append(p)
    return plans, dict(counts)


def measure(p, rows, stamps):
    start = (p['observed_ms']//MINUTE+1)*MINUTE
    end = start+168*HOUR
    path = rows[bisect.bisect_left(stamps, start):bisect.bisect_left(stamps, end)]
    base = dict(reference_ms=start, funding_status='UNKNOWN', full_net_pct=None)
    if not path or path[0]['open_ms'] != start:
        return dict(base, status='ENTRY_DATA_MISSING')
    try:
        swing.check_bars(path[:1], MINUTE)
    except (ValueError, KeyError, TypeError) as exc:
        return dict(base, status='DATA_INVALID', reason=str(exc))
    entry = path[0]['open']
    reason = swing.geometry(p, entry)
    if reason:
        return dict(base, status='ENTRY_INVALID', entry=entry, reason=reason)
    state = advance(p, initial(p, start, entry), path)
    if state['cursor_ms'] < end:
        state['missing_ms'] = state['cursor_ms']
    out = result(p, state)
    risk = 100*(entry-p['stop'])/entry
    fee = out['fee_slippage_only_pct']
    return dict(base, status='MEASURED', entry=entry, risk_pct=risk, outcome=out,
        fee_r=fee/risk if fee is not None else None,
        holding_hours=(out['exit']['at_ms']-start)/HOUR if out['exit'] else None)


def run_symbol(job):
    cache, expected = job
    meta, rows = archive(Path(cache), expected['symbol'], '2026-08', '1m', offline=True)
    if meta['status'] != 'OK' or meta['sha256'] != expected['sha256']:
        raise ValueError('SOURCE_CHANGED:'+expected['symbol'])
    stamps = [b['open_ms'] for b in rows]
    if any(b <= a for a, b in zip(stamps, stamps[1:])):
        raise ValueError('SOURCE_ORDER_DUPLICATE:'+expected['symbol'])
    hours, rejected = aggregate(rows, 60)
    contexts = {t: context(hours, t) for t in range(START, END, HOUR)}
    records, counts, rejects = [], {}, {'60': rejected}
    for minutes in PERIODS:
        if minutes == 60:
            bars = hours
        else:
            bars, rejects[str(minutes)] = aggregate(rows, minutes)
        plans, counts[str(minutes)] = select(expected['symbol'], minutes, bars, hours, contexts)
        records += [dict(plan=p, audit=measure(p, rows, stamps)) for p in plans]
    return dict(symbol=expected['symbol'], records=records, counts=counts,
                rejected_buckets=rejects, source=expected)


def avg(x):
    return statistics.mean(x) if x else None


def median(x):
    return statistics.median(x) if x else None


def complete(r):
    a = r['audit']
    return a['status'] == 'MEASURED' and a['outcome']['status'] == 'COMPLETE'


def summarize(records):
    answer = {}
    for minutes in PERIODS:
        group = [r for r in records if r['plan']['timeframe_minutes'] == minutes]
        good = [r for r in group if complete(r)]
        audits = [r['audit'] for r in good]
        fees = [a['outcome']['fee_slippage_only_pct'] for a in audits]
        rs = [a['fee_r'] for a in audits]
        reaches = {}
        for threshold in (5, 10, 20, 30, 50):
            hit = [a for a in audits if str(threshold) in a['outcome']['in_plan_reach']]
            reaches[str(threshold)] = dict(count=len(hit),
                pct=100*len(hit)/len(audits) if audits else None,
                later_loss=sum(a['outcome']['fee_slippage_only_pct'] < 0 for a in hit),
                median_hours=median([(a['outcome']['in_plan_reach'][str(threshold)]-a['reference_ms'])/HOUR for a in hit]),
                raw_including_post_exit_count=sum(str(threshold) in a['outcome']['raw_reach'] for a in audits))
        answer[str(minutes)] = dict(candidates=len(group), complete=len(good),
            statuses=dict(Counter(r['audit']['status'] for r in group)),
            path_statuses=dict(Counter(r['audit']['outcome']['status'] for r in group if r['audit']['status']=='MEASURED')),
            symbols=len({r['plan']['symbol'] for r in good}), positive=sum(x>0 for x in fees),
            positive_pct=100*sum(x>0 for x in fees)/len(fees) if fees else None,
            mean_fee_r=avg(rs), median_fee_r=median(rs), mean_fee_pct=avg(fees), median_fee_pct=median(fees),
            median_risk_pct=median([a['risk_pct'] for a in audits]),
            median_holding_hours=median([a['holding_hours'] for a in audits]),
            known_obstacle=sum(r['plan']['obstacle'] is not None for r in good),
            in_plan_reach=reaches, days=dict(sorted(Counter(datetime.fromtimestamp(
                (r['plan']['confirmation_ms']+1)/1000, timezone.utc).strftime('%Y-%m-%d') for r in good).items())))
    return answer


def paired(records):
    index = {}
    for r in records:
        p = r['plan']
        k = (p['timeframe_minutes'], p['symbol'], p['confirmation_ms']//HOUR)
        index.setdefault(k, r)
    output = {}
    for tf in (15, 30):
        pairs = []
        for (period, symbol, hour), r in sorted(index.items()):
            if period != tf:
                continue
            ref = index.get((60, symbol, hour))
            if ref is None or not complete(r) or not complete(ref):
                continue
            a, b = r['audit'], ref['audit']
            pairs.append(dict(symbol=symbol, hour=hour,
                earlier_minutes=(ref['plan']['confirmation_ms']-r['plan']['confirmation_ms'])/MINUTE,
                entry_change_pct=100*(a['entry']/b['entry']-1), fee_r_delta=a['fee_r']-b['fee_r']))
        output[str(tf)] = dict(pairs=len(pairs), median_earlier_minutes=median([p['earlier_minutes'] for p in pairs]),
            mean_entry_change_pct=avg([p['entry_change_pct'] for p in pairs]),
            mean_fee_r_delta=avg([p['fee_r_delta'] for p in pairs]), records=pairs)
    return output


def freeze():
    target = HERE/'FROZEN.json'
    if target.exists():
        raise ValueError('ALREADY_FROZEN')
    sources = sorted([r for r in load_evidence('2026-10-22-minute-download-complete.json.gz')['archives']
        if r['status']=='OK' and r['month']=='2026-08'], key=lambda r:r['symbol'])
    if len(sources) != 392 or any(r['sha256'] != r['official_checksum'].split()[0] for r in sources):
        raise ValueError('UNEXPECTED_SOURCE_MANIFEST')
    proof = dict(version=VERSION, protocol_sha256=sha(HERE/'PROTOCOL.md'), code_sha256=sha(__file__),
        test_sha256=sha(ROOT/'tests/test_entry_timeframe.py'),
        protected_sha256={name:sha(ROOT/name) for name in PROTECTED}, sources=sources,
        period_utc=['2026-08-08T00:00:00Z','2026-08-24T00:00:00Z'], periods_minutes=PERIODS,
        independent_validation=False, crypto_only=False, funding_included=False,
        activation_eligible=False, network_requests=0)
    target.write_text(json.dumps(proof, indent=2)+'\n')
    print(json.dumps(dict(frozen_sha256=sha(target), sources=len(sources))))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['freeze','run'])
    p.add_argument('--cache', type=Path)
    p.add_argument('--workers', type=int, default=4)
    args = p.parse_args()
    if args.action == 'freeze':
        freeze()
        return
    if args.cache is None:
        raise ValueError('CACHE_REQUIRED')
    if (HERE/'SUMMARY.json').exists() or (HERE/'OUTCOMES.json.gz').exists():
        raise ValueError('OUTPUT_EXISTS')
    proof = json.loads((HERE/'FROZEN.json').read_text())
    if proof['code_sha256'] != sha(__file__) or proof['protocol_sha256'] != sha(HERE/'PROTOCOL.md'):
        raise ValueError('METHOD_CHANGED')
    for name, expected in proof['protected_sha256'].items():
        if sha(ROOT/name) != expected:
            raise ValueError('PROTECTED_SOURCE_CHANGED:'+name)
    records, sources = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(run_symbol, (str(args.cache), source)) for source in proof['sources']]
        for i, future in enumerate(as_completed(jobs), 1):
            item = future.result()
            records += item.pop('records')
            sources.append(item)
            if i % 40 == 0:
                print(json.dumps(dict(symbols_done=i, total=len(jobs), candidates=len(records))), flush=True)
    records.sort(key=lambda r:(r['plan']['timeframe_minutes'], r['plan']['symbol'], r['plan']['confirmation_ms']))
    sources.sort(key=lambda r:r['symbol'])
    report = dict(version=VERSION, frozen_sha256=sha(HERE/'FROZEN.json'), summary=summarize(records),
        paired_same_hour=paired(records), sources=sources, network_requests=0, funding_included=False,
        independent_validation=False, crypto_only=False, activation_eligible=False, live_deployed=False)
    (HERE/'OUTCOMES.json.gz').write_bytes(gzip.compress(json.dumps(records).encode(), mtime=0))
    report['outcomes_sha256'] = sha(HERE/'OUTCOMES.json.gz')
    (HERE/'SUMMARY.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()
