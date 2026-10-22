"""Offline, frozen momentum discovery comparison; never a trading backtest.

See BASELINE_PROTOCOL.md. Future coverage is checked AFTER past-only ranking.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import random
import statistics

import replay
from long_short_swing import CONFIG, CONFIG_HASH, HOUR, check_bars, finite

PROTOCOL_COMMIT = 'e0eb98ac7d81eb20d56986a670e27e47fc2556a5'
VERSION = 'MOMENTUM_DISCOVERY_COMPARISON_V1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_day(stamp):
    # Decision is one second after the confirmation, including midnight.
    return (stamp + 1000) // (24 * HOUR)


def day_label(day):
    return datetime.fromtimestamp(day * 86400, timezone.utc).date().isoformat()


def panel(data):
    """Point-in-time eligibility, without any future-availability filter."""
    by_time = defaultdict(list)
    for symbol, rows in sorted(data.items()):
        invalid, gaps, volume = [0], [0], [0.]
        for i, b in enumerate(rows):
            bad = 0
            try:
                check_bars([b], HOUR)
                q, buy = b.get('quote_volume'), b.get('taker_buy_quote')
                if not finite(q) or q < 0 or not finite(buy) or not 0 <= buy <= q:
                    raise ValueError('INVALID_VOLUME')
            except (ValueError, KeyError, TypeError):
                bad = 1
            invalid.append(invalid[-1] + bad)
            gaps.append(gaps[-1] + int(i > 0 and b['open_ms'] != rows[i-1]['open_ms'] + HOUR))
            volume.append(volume[-1] + (b['quote_volume'] if not bad else 0.))
            if i < CONFIG['history_hours'] - 1:
                continue
            first = i - CONFIG['history_hours'] + 1
            # A gap BEFORE the first window bar does not invalidate the window.
            if invalid[i+1] != invalid[first] or gaps[i+1] != gaps[first+1]:
                continue
            q24 = volume[i+1] - volume[i-23]
            if q24 < CONFIG['min_quote_volume_24h']:
                continue
            by_time[b['close_ms']].append(dict(symbol=symbol, i=i,
                return_24h_pct=100*(b['close']/rows[i-24]['close']-1),
                quote_volume_24h=q24))
    return dict(by_time)


def rank_at(candidates, direction):
    sign = 1 if direction == 'LONG' else -1
    return sorted(candidates, key=lambda x: (-sign*x['return_24h_pct'], x['symbol']))[:5]


def selection(data, candidate, direction):
    rows = data[candidate['symbol']]
    i = candidate['i']
    past = rows[i-168:i]
    origin = min(b['low'] for b in past) if direction == 'LONG' else max(b['high'] for b in past)
    sign = 1 if direction == 'LONG' else -1
    return dict(**candidate, direction=direction, confirmation_ms=rows[i]['close_ms'],
        move_before_signal_pct=100*sign*(rows[i]['close']/origin-1),
        raw=replay.raw_forward(rows, i, direction))


def mean(values):
    return statistics.mean(values) if values else None


def metrics(records):
    mature = [r for r in records if r['raw']['status'] == 'COMPLETE']
    return dict(selections=len(records), mature=len(mature), incomplete=len(records)-len(mature),
        unique_symbols=len({r['symbol'] for r in records}),
        unique_symbol_hours=len({(r['symbol'], r['confirmation_ms']) for r in records}),
        reached_30=sum(r['raw']['reached_30'] for r in mature),
        raw_reach_30_pct=100*mean([int(r['raw']['reached_30']) for r in mature]) if mature else None,
        median_raw_mfe_pct=statistics.median(r['raw']['raw_mfe_pct'] for r in mature) if mature else None,
        median_raw_mae_pct=statistics.median(r['raw']['raw_mae_pct'] for r in mature) if mature else None,
        median_directional_close_168h_pct=statistics.median(r['raw']['directional_close_168h_pct'] for r in mature) if mature else None,
        mean_directional_close_168h_pct=mean([r['raw']['directional_close_168h_pct'] for r in mature]),
        median_move_before_signal_pct=statistics.median(r['move_before_signal_pct'] for r in mature) if mature else None,
        already_extended_over_25_pct=100*mean([int(r['move_before_signal_pct'] > 25) for r in mature]) if mature else None)


def hour_metrics(records):
    return dict(raw_reach_30_pct=100*mean([int(r['raw']['reached_30']) for r in records]),
        mean_raw_mfe_pct=mean([r['raw']['raw_mfe_pct'] for r in records]),
        mean_raw_mae_pct=mean([r['raw']['raw_mae_pct'] for r in records]),
        mean_directional_close_168h_pct=mean([r['raw']['directional_close_168h_pct'] for r in records]),
        mean_move_before_signal_pct=mean([r['move_before_signal_pct'] for r in records]))


def compare_hours(groups):
    result = dict(paired_hours=len(groups), signal_days=len({utc_day(g['confirmation_ms']) for g in groups}))
    if not groups:
        return result
    for arm in ('swing', 'momentum', 'control'):
        available = [g[arm] for g in groups if g.get(arm) is not None]
        result[arm] = dict(hours=len(available), **{
            k:(mean([r[k] for r in available]) if all(r[k] is not None for r in available) else None)
            for k in groups[0]['swing']})
    result['swing_minus_momentum_reach_pp'] = result['swing']['raw_reach_30_pct'] - result['momentum']['raw_reach_30_pct']
    # Control uses the same hours only; never compare unequal hour denominators.
    controls = [g for g in groups if g.get('control') is not None]
    result['swing_minus_control_reach_pp'] = mean([
        g['swing']['raw_reach_30_pct']-g['control']['raw_reach_30_pct'] for g in controls])
    return result


def block_interval(groups, repeats=2000, block_days=7, seed=20261022):
    if not groups:
        return dict(status='NO_PAIRED_HOURS')
    by_day = defaultdict(list)
    for g in groups:
        by_day[utc_day(g['confirmation_ms'])].append(g['swing']['raw_reach_30_pct']-g['momentum']['raw_reach_30_pct'])
    first, last = min(by_day), max(by_day)
    days = list(range(first, last+1))
    if len(days) < block_days:
        return dict(status='TOO_FEW_DAYS', calendar_days=len(days))
    rng = random.Random(seed)
    estimates = []
    for _ in range(repeats):
        sampled = []
        while len(sampled) < len(days):
            start = rng.randrange(len(days)-block_days+1)
            sampled.extend(days[start:start+block_days])
        values = [x for d in sampled[:len(days)] for x in by_day[d]]
        if values:
            estimates.append(statistics.mean(values))
    estimates.sort()
    return dict(status='DESCRIPTIVE_NOT_INDEPENDENT_VALIDATION', method='non-circular moving day blocks',
        block_days=block_days, calendar_days=len(days), approximate_nonoverlapping_blocks=len(days)/block_days,
        seed=seed, repeats=repeats, usable_repeats=len(estimates),
        reach_difference_pp_interval_90=[estimates[int(.05*(len(estimates)-1))], estimates[int(.95*(len(estimates)-1))]])


def compare(data, screen):
    candidates = panel(data)
    cached = {}
    def picks(stamp, side):
        key = stamp, side
        if key not in cached:
            cached[key] = [selection(data, c, side) for c in rank_at(candidates.get(stamp, []), side)]
        return cached[key]

    btc = {}
    for ctime, cs in candidates.items():
        c = next((c for c in cs if c['symbol']=='BTCUSDT'), None)
        if c:
            ret = c['return_24h_pct']
            btc[ctime] = 'POSITIVE' if ret > 0 else 'NEGATIVE' if ret < 0 else 'FLAT'
    by_side = defaultdict(lambda: defaultdict(list))
    for r in screen['records']:
        by_side[r['plan']['direction']][r['plan']['confirmation_ms']].append(r)
    details, summaries, scheduled_details = {}, {}, {}
    for side in ('LONG', 'SHORT'):
        hours, exclusions = [], Counter()
        for stamp, rs in sorted(by_side[side].items()):
            sw = [dict(symbol=r['plan']['symbol'], confirmation_ms=stamp,
                  move_before_signal_pct=r['plan']['move_before_signal_pct'], raw=r['raw']) for r in rs]
            base = picks(stamp, side)
            reasons = []
            if any(r['raw']['status']!='COMPLETE' for r in sw):reasons.append('SWING_INCOMPLETE')
            if len(base)!=5:reasons.append('FEWER_THAN_FIVE_BASELINE_CANDIDATES')
            if any(r['raw']['status']!='COMPLETE' for r in base):reasons.append('BASELINE_INCOMPLETE')
            control = []
            for r in rs:
                cr = r['control']
                if cr.get('raw', {}).get('status')!='COMPLETE':
                    control = [];break
                control.append(dict(raw=cr['raw'], move_before_signal_pct=0))
            exclusions.update(reasons)
            hours.append(dict(confirmation_ms=stamp, btc_past_24h_regime=btc.get(stamp, 'UNKNOWN'),
                status='PAIRED' if not reasons else 'EXCLUDED', exclusions=reasons,
                swing_records=sw, momentum_records=base,
                swing=hour_metrics(sw) if not reasons else None,
                momentum=hour_metrics(base) if not reasons else None,
                # Origin distance for old controls was not retained; mark unknown.
                control={**hour_metrics(control), 'mean_move_before_signal_pct':None} if control and not reasons else None))
        paired = [g for g in hours if g['status']=='PAIRED']
        schedules = []
        for stamp in sorted(candidates):
            if (stamp+1) % (4*HOUR)==0:
                schedules.append(dict(confirmation_ms=stamp, records=picks(stamp, side)))
        scheduled = [r for g in schedules for r in g['records']]
        summaries[side] = dict(total_signal_hours=len(hours), excluded_signal_hours=len(hours)-len(paired),
            exclusion_reasons=dict(exclusions), equal_hour=compare_hours(paired),
            descriptive_interval=block_interval(paired),
            paired_swing_episode_weighted=metrics([r for g in paired for r in g['swing_records']]),
            paired_momentum_component_weighted=metrics([r for g in paired for r in g['momentum_records']]),
            btc_past_regimes={reg:compare_hours([g for g in paired if g['btc_past_24h_regime']==reg])
                for reg in ('POSITIVE','NEGATIVE','FLAT','UNKNOWN')},
            scheduled_every_4h=dict(selection_hours=len(schedules),
                fewer_than_five_hours=sum(len(g['records'])<5 for g in schedules), **metrics(scheduled)))
        details[side], scheduled_details[side] = hours, schedules
    return dict(summary=summaries, paired_hour_details=details, scheduled_details=scheduled_details,
                baseline_eligible_coin_hours=sum(len(cs) for cs in candidates.values()))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--results',type=Path,default=Path(__file__).with_name('results'))
    args=p.parse_args()
    coverage_path=args.results/'2026-08-coverage.json'
    screen_path=args.results/'2026-08-screen.json'
    coverage=json.loads(coverage_path.read_text())
    screen=json.loads(screen_path.read_text())
    if screen['config_hash']!=CONFIG_HASH:raise ValueError('CONFIG_MISMATCH')
    if any(c['status'] not in ('OK','NOT_PUBLISHED') for c in coverage):
        raise ValueError('INCOMPLETE_COVERAGE_MANIFEST')
    data={}
    for c in coverage:
        if c['status']!='OK':continue
        info,rows=replay.archive(args.cache,c['symbol'],'2026-08',offline=True)
        if info['status']!='OK' or info['sha256']!=c['sha256']:
            raise ValueError('MISSING_OR_CHANGED_ARCHIVE:'+c['symbol'])
        data[c['symbol']]=rows
    print('VERIFIED_ARCHIVES '+str(len(data)),flush=True)
    report=compare(data,screen)
    protocol=Path(__file__).with_name('BASELINE_PROTOCOL.md')
    report['manifest']=dict(version=VERSION,config_hash=CONFIG_HASH,protocol_commit=PROTOCOL_COMMIT,
        protocol_sha256=digest(protocol),code_sha256=digest(Path(__file__)),
        screen_sha256=digest(screen_path),coverage_sha256=digest(coverage_path),
        universe='UNCLASSIFIED_USDT_ARCHIVE_V1',crypto_only=False,
        classification='IN_SAMPLE_RAW_DISCOVERY_COMPARISON_NOT_TRADE_PERFORMANCE',
        network_used=False,validated_archives=len(data),funding='NOT_INCLUDED',costs='NOT_INCLUDED',stops='NOT_INCLUDED')
    full=json.dumps(report,ensure_ascii=False,indent=2).encode()
    out=args.results/'2026-08-momentum-comparison.json'
    out.write_bytes(full)
    evidence=Path(__file__).with_name('evidence')
    compressed=gzip.compress(full,mtime=0)
    (evidence/(out.name+'.gz')).write_bytes(compressed)
    compact={k:v for k,v in report.items() if k not in ('paired_hour_details','scheduled_details')}
    Path(__file__).with_name('MOMENTUM_COMPARISON.json').write_text(json.dumps(compact,ensure_ascii=False,indent=2))
    print(json.dumps(compact,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
