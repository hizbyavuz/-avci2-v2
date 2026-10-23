"""Fixed six-symbol historical review; no exchange, Telegram, or cloud mutations."""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import time

from .archive import archive, aggregate
from .config import CONFIG as C, CONFIG_HASH, HOUR, PERIODS, VERSION, identity
from .core import assess, message
from .outcomes import measure
from .store import code_hash, encoded

SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'ADAUSDT', 'DUSKUSDT', 'KERNELUSDT')
MONTHS = ('2026-08', '2026-09')


def timestamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()*1000)


def cutoff(all_frames, closed_times, interval, now):
    frames = {}
    for tf in (interval, '1h', '4h'):
        n = bisect_right(closed_times[tf], now-1)
        frames[tf] = all_frames[tf][max(0, n-C['maximum_bars']):n]
    return frames


def summarize(records):
    complete = [r for r in records if r.get('outcome', {}).get('observation_complete')]
    plans = [r for r in records if r['plan'].get('plan_eligible')]
    closed = [r for r in plans if r['outcome']['fee_slippage_only_pct'] is not None]
    returns = [r['outcome']['fee_slippage_only_pct'] for r in closed]
    wins, losses = sum(max(0, v) for v in returns), -sum(min(0, v) for v in returns)
    return dict(events=len(records), stages=dict(Counter(r['plan']['stage'] for r in records)),
                directions=dict(Counter(r['plan']['direction'] for r in records)),
                warnings=dict(Counter(w for r in records for w in r['plan'].get('warnings', []))),
                outcome_states=dict(Counter(r['outcome']['status'] for r in records)),
                complete_observations=len(complete), eligible_plans=len(plans), closed_plans=len(closed),
                entry_cancelled=sum(r['outcome'].get('entry_cancelled', False) for r in plans),
                closed_fee_only_mean_pct=statistics.mean(returns) if returns else None,
                closed_mean_net_r=statistics.mean(r['outcome']['net_r'] for r in closed) if closed else None,
                closed_positive_count=sum(v > 0 for v in returns),
                closed_profit_factor=wins/losses if losses else None,
                horizon_fee_only_mean_pct=statistics.mean(r['outcome']['horizon_fee_only_pct'] for r in complete) if complete else None,
                raw_median_mfe_pct=statistics.median(r['outcome']['raw_mfe_pct'] for r in complete) if complete else None,
                raw_median_mae_pct=statistics.median(r['outcome']['raw_mae_pct'] for r in complete) if complete else None,
                full_net_available=0, unresolved=len(records)-len(complete),
                per_day=dict(Counter(datetime.fromtimestamp(r['plan']['asof_ms']/1000, timezone.utc).strftime('%Y-%m-%d') for r in records)))


def run(cache, output, offline=False):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    manifest = dict(version=VERSION, config_hash=CONFIG_HASH, code_hash=code_hash(), config=C,
                    symbols=SYMBOLS, months=MONTHS, days='15 inclusive to 22 exclusive UTC',
                    declared_before_outcome_measurement=True,
                    development='2026-08', separate_validation='2026-09',
                    warnings=['Six-symbol convenience sample, not a point-in-time full universe.',
                              'No order-book history or funding: full net/executability unknown.',
                              'Events correlated and overlapping; not independent trades or portfolio returns.',
                              'No parameter optimization, selection by outcome, or live trading.'])
    (output/'manifest.json').write_text(encoded(manifest), encoding='utf-8')
    started = time.monotonic()
    evidence, failures = [], []
    def download(symbol, month):
        meta, _ = archive(cache, symbol, month, offline)
        return symbol, month, meta
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(download, s, m): (s, m) for m in MONTHS for s in SYMBOLS}
        for future in as_completed(futures):
            s, m = futures[future]
            try:
                _, _, meta = future.result(); evidence.append(dict(symbol=s, month=m, **meta))
                print('ARCHIVE_OK', s, m, flush=True)
            except Exception as exc:
                failures.append(dict(symbol=s, month=m, reason=type(exc).__name__))
                print('ARCHIVE_FAILED', s, m, type(exc).__name__, flush=True)
    (output/'archives.json').write_text(encoded(dict(verified=evidence, failures=failures)), encoding='utf-8')
    groups, random_groups, simple_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    health, examples = Counter(), set()
    simulation = defaultdict(lambda: {'last_watch': {}, 'attempts': [], 'cycle': Counter(), 'reasons': Counter()})
    all_events = gzip.open(output/'events.jsonl.gz', 'wt', encoding='utf-8')
    simple_log = gzip.open(output/'baselines.jsonl.gz', 'wt', encoding='utf-8')
    try:
        for month in MONTHS:
            begin, end = timestamp(month+'-15'), timestamp(month+'-22')
            for symbol in SYMBOLS:
                if any(x['symbol'] == symbol and x['month'] == month for x in failures): continue
                _, minutes = archive(cache, symbol, month, True)
                all_frames = {tf: aggregate(minutes, tf) for tf in PERIODS}
                closed_times = {tf: [b['close_ms'] for b in rows] for tf, rows in all_frames.items()}
                for interval in ('15m', '30m'):
                    cohort = month+':'+interval
                    seen = set()
                    for i, b in enumerate(all_frames[interval]):
                        now = b['close_ms']+1
                        if not begin <= now < end: continue
                        frames = cutoff(all_frames, closed_times, interval, now)
                        a = assess(symbol, frames, now, interval)
                        health[(cohort, a['status'])] += 1
                        for p in a['decisions']:
                            if p['event_id'] in seen: continue
                            seen.add(p['event_id'])
                            outcome = measure(p, minutes)
                            day = now//(24*HOUR)*(24*HOUR)
                            seed = int(identity('random-control-v1', p['event_id']), 16)
                            rp = dict(asof_ms=day+(seed % (24*HOUR//PERIODS[interval]))*PERIODS[interval],
                                      direction='LONG' if (seed//100000) % 2 else 'SHORT', stage='RANDOM', plan_eligible=False)
                            random_result = dict(plan=rp, outcome=measure(rp, minutes))
                            record = dict(cohort=cohort, symbol=symbol, plan=p, outcome=outcome,
                                          random_control=random_result)
                            groups[cohort].append(record); random_groups[cohort].append(random_result)
                            all_events.write(encoded(record)+'\n')
                            category = (p['stage'], p['direction'])
                            if interval == '15m' and month == '2026-08' and category not in examples:
                                examples.add(category)
                                from .chart import render
                                name = symbol+'-'+p['stage']+'-'+p['direction']
                                folder = output/'examples'
                                render(a, frames, p, folder/(name+'.png'))
                                (folder/(name+'.txt')).write_text(message(p), encoding='utf-8')
                                (folder/(name+'.json.gz')).write_bytes(gzip.compress(encoded(dict(assessment=a, frames=frames, plan=p)).encode(), mtime=0))
                        # A separately defined simple 20-bar breakout baseline.
                        past = all_frames[interval][i-20:i]
                        if len(past) == 20:
                            side = 'LONG' if b['close'] > max(x['high'] for x in past) else 'SHORT' if b['close'] < min(x['low'] for x in past) else None
                            if side:
                                bp = dict(asof_ms=now, direction=side, stage='SIMPLE_BREAKOUT', plan_eligible=False)
                                record = dict(cohort=cohort, symbol=symbol, plan=bp, outcome=measure(bp, minutes))
                                simple_groups[cohort].append(record)
                                simple_log.write(encoded(record)+'\n')
                    print('REPLAYED', cohort, symbol, 'events', len(groups[cohort]), flush=True)
        # Estimate quota suppression in global time order. This is not a full
        # live lifecycle/receipt simulation (terminal suppression is excluded).
        for cohort, records in groups.items():
            state = simulation[cohort]
            for r in sorted(records, key=lambda r: (r['plan']['asof_ms'], r['symbol'], r['plan']['event_id'])):
                p = r['plan']; now = p['asof_ms']; symbol = r['symbol']
                state['attempts'] = [t for t in state['attempts'] if t > now-24*HOUR]
                if p['stage'] == 'LATE': reason = 'LATE_RECORDED_ONLY'
                elif state['cycle'][now] >= C['max_notifications_per_cycle']: reason = 'CYCLE_LIMIT'
                elif len(state['attempts']) >= C['max_notifications_24h']: reason = 'DAILY_LIMIT'
                elif p['stage'] == 'WATCH' and now-state['last_watch'].get(symbol, 0) < C['notification_cooldown_minutes']*60_000: reason = 'SYMBOL_COOLDOWN'
                else:
                    reason = 'WOULD_NOTIFY'; state['attempts'].append(now); state['cycle'][now] += 1
                    state['last_watch'][symbol] = now
                state['reasons'][reason] += 1
        summary = dict(manifest=manifest, archives_verified=len(evidence), archive_failures=failures,
                       evaluated_windows={cohort: {status: n for (co, status), n in health.items() if co == cohort}
                                          for cohort in sorted({c for c, _ in health})},
                       cohorts={co: dict(strategy=summarize(rs), random_control=summarize(random_groups[co]),
                                         simple_breakout=summarize(simple_groups[co]),
                                         notification_simulation=dict(simulation[co]['reasons']))
                                for co, rs in groups.items()}, elapsed_seconds=time.monotonic()-started,
                       notification_simulation_scope='Quota/cooldown estimate only; excludes terminal lifecycle suppression, feed delay and delivery receipts.',
                       validated_for_live=False, no_orders=True, no_telegram_sent=True)
        (output/'summary.json').write_text(encoded(summary), encoding='utf-8')
        print('SUMMARY', encoded(summary), flush=True)
        return summary
    finally:
        all_events.close(); simple_log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    run(args.cache, args.out, args.offline)
