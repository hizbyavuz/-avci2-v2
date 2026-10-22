"""Describe existing experiments; never select a new rule or change a plan.

All inputs are local, hash-checked evidence. BTC context ends at the decision.
Early marks are descriptive counterfactuals, NOT new exit rules or validation.
"""
import argparse
import bisect
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from long_short_swing import HOUR, MINUTE, check_bars
from research.short_v2.replay_short import load_evidence, months_between
from research.swing_v1.replay import archive

VERSION = 'SHORT_CAUSE_DIAGNOSTIC_V1_2026_10_22'
HORIZONS = (4, 24)


def context(rows, decision_ms):
    """Reject a gap and never use a row closed after the decision."""
    times = [b['close_ms'] for b in rows]
    i = bisect.bisect_right(times, decision_ms) - 1
    out = {}
    for h in (24, 168):
        key = str(h)
        part = rows[max(0, i-h):i+1] if i >= 0 else []
        try:
            if len(part) != h+1 or part[-1]['close_ms'] != decision_ms:
                raise ValueError('BTC_HISTORY_MISSING_OR_UNALIGNED')
            check_bars(part, HOUR, decision_ms+1)
            value = 100*(part[-1]['close']/part[0]['close']-1)
            out[key] = dict(status='KNOWN', return_pct=value,
                            regime='UP' if value > 0 else 'DOWN' if value < 0 else 'FLAT')
        except (ValueError, KeyError, TypeError):
            out[key] = dict(status='UNKNOWN', return_pct=None, regime='UNKNOWN')
    return out


def early_mark(audit, bar, hours):
    """Raw point return and stopped-plan mark are distinct quantities."""
    end = audit['reference_ms'] + hours*HOUR
    if bar is None or bar['open_ms'] != end-MINUTE:
        return dict(status='MISSING', raw_direction_pct=None, plan_fee_only_r=None)
    check_bars([bar], MINUTE)
    sign = 1 if audit['direction'] == 'LONG' else -1
    raw = 100*sign*(bar['close']/audit['entry_price']-1)
    exit_ = audit['outcome']['exit']
    exited = exit_ is not None and exit_['time_upper_ms'] < end
    gross = exit_['gross_pct'] if exited else raw
    return dict(status='KNOWN', raw_direction_pct=raw,
                plan_fee_only_r=(gross-.30)/audit['initial_risk_pct'],
                already_closed=exited)


def stats(records):
    closed = [r for r in records if r.get('result_r') is not None]
    values = [r['result_r'] for r in closed]
    return dict(count=len(records), known_results=len(values),
                positive=sum(x > 0 for x in values),
                mean_fee_only_r=statistics.mean(values) if values else None)


def summarize(records):
    result = {}
    keys = sorted({r['cohort'] for r in records})
    for key in keys:
        all_ = [r for r in records if r['cohort'] == key]
        full = [r for r in all_ if r['path_status'] == 'COMPLETE']
        losses = [r for r in full if r['result_r'] <= 0]
        bins = {}
        for h in ('24', '168'):
            bins[h] = {regime: stats([r for r in full if r['btc'][h]['regime'] == regime])
                       for regime in ('UP', 'DOWN', 'FLAT', 'UNKNOWN')}
        early = {}
        for h in map(str, HORIZONS):
            known = [r['early'][h] for r in full if r['early'].get(h, {}).get('status') == 'KNOWN']
            early[h] = dict(available=len(known), unavailable=len(full)-len(known),
                            raw_direction_positive=sum(r['raw_direction_pct'] > 0 for r in known),
                            raw_direction_mean_pct=statistics.mean(r['raw_direction_pct'] for r in known) if known else None,
                            stopped_plan_mean_fee_only_r=statistics.mean(r['plan_fee_only_r'] for r in known) if known else None)
        result[key] = dict(all_candidates=len(all_), statuses=dict(Counter(r['status'] for r in all_)),
            path_states=dict(Counter(r['path_status'] for r in all_)), complete_conditional=stats(full),
            # Upper bound handles unknown high/low ordering in a stop minute.
            definitely_never_1r=sum(r['mfe_upper_r'] < 1 for r in full),
            confirmed_1r_then_loss=sum(r['mfe_lower_r'] >= 1 for r in losses),
            confirmed_2r_then_loss=sum(r['mfe_lower_r'] >= 2 for r in losses),
            initial_stop_loss=sum(r['initial_stop_exit'] for r in losses),
            mean_gross_giveback_from_mfe_lower_r=statistics.mean(
                r['mfe_lower_r']-r['result_r']-.30/r['initial_risk_pct'] for r in full) if full else None,
            early=early, btc_context_complete_conditional=bins)
    return result


def symbol_job(job):
    cache, symbol, records, expected = job
    requests = sorted({m for r in records if r['path_status'] == 'COMPLETE'
        for m in months_between(r['_audit']['reference_ms'], r['_audit']['reference_ms']+24*HOUR)})
    by_time = {}; sources = []
    for month in requests:
        sha = expected.get((symbol, month))
        if not sha:
            sources.append(dict(symbol=symbol, month=month, status='UNVERIFIED')); continue
        info, rows = archive(Path(cache), symbol, month, '1m', offline=True)
        if info['status'] != 'OK' or info['sha256'] != sha:
            raise ValueError('MINUTE_SOURCE_CHANGED:'+symbol+':'+month)
        sources.append(dict(symbol=symbol, month=month, status='OK', sha256=sha))
        by_time.update((b['open_ms'], b) for b in rows)
    for r in records:
        a = r.pop('_audit'); r['early'] = {}
        if r['path_status'] != 'COMPLETE': continue
        for h in HORIZONS:
            r['early'][str(h)] = early_mark(a, by_time.get(a['reference_ms']+h*HOUR-MINUTE), h)
    return records, sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args(); here = Path(__file__).parent
    target = here/'CAUSE_DIAGNOSTIC.json'
    if target.exists(): raise ValueError('OUTPUT_EXISTS')
    records = []; inputs = []
    base = load_evidence('2026-08-full-minute-audit.json.gz')
    cohorts = [('2026-08:V1:'+r['direction'], r) for r in base['records']]
    for month in ('2026-08', '2026-09'):
        path = here/(month+'-EPISODES.json.gz'); body = path.read_bytes()
        inputs.append(dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(body).hexdigest()))
        x = json.loads(gzip.decompress(body))
        cohorts += [(month+':'+r['plan']['model'], r['audit']) for r in x['records']]
    hour_evidence = [load_evidence('2026-08-coverage.json.gz'),
                     load_evidence('2026-10-22-hourly-download-confirmed.json.gz')['archives']]
    btc = []; btc_sources = []
    for evidence in hour_evidence:
        expected = next(r for r in evidence if r['symbol'] == 'BTCUSDT' and r['status'] == 'OK')
        info, rows = archive(args.cache, 'BTCUSDT', expected['month'], offline=True)
        if info['status'] != 'OK' or info['sha256'] != expected['sha256']:
            raise ValueError('BTC_SOURCE_CHANGED')
        btc += rows; btc_sources.append(info)
    minute = load_evidence('2026-10-22-minute-download-complete.json.gz')['archives']
    expected_minutes = {(r['symbol'], r['month']):r['sha256'] for r in minute if r['status'] == 'OK'}
    for cohort, a in cohorts:
        out = a.get('outcome') or {}; risk = a.get('initial_risk_pct')
        fee = out.get('fee_slippage_only_pct'); ex = out.get('exit')
        records.append(dict(cohort=cohort, symbol=a['symbol'], direction=a['direction'],
            confirmation_ms=a['confirmation_ms'], status=a['status'], path_status=out.get('status', 'NOT_STARTED'),
            initial_risk_pct=risk, result_r=fee/risk if risk and fee is not None else None,
            mfe_lower_r=out['in_plan_mfe_lower_pct']/risk if risk else None,
            mfe_upper_r=out['in_plan_mfe_upper_pct']/risk if risk else None,
            initial_stop_exit=bool(ex and ex['kind']=='STOP' and
                abs(ex['price']-a['initial_stop']) <= 1e-10*max(1, abs(a['initial_stop']))),
            btc=context(btc, a['confirmation_ms']), _audit=a))
    by_symbol = defaultdict(list)
    for r in records: by_symbol[r['symbol']].append(r)
    records = []; sources = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending = [pool.submit(symbol_job, (str(args.cache), s, rows, expected_minutes))
                   for s, rows in sorted(by_symbol.items())]
        for i, future in enumerate(as_completed(pending), 1):
            rows, local = future.result(); records += rows; sources += local
            if i % 100 == 0: print(json.dumps(dict(symbols_done=i, total=len(pending))), flush=True)
    records.sort(key=lambda r:(r['cohort'], r['confirmation_ms'], r['symbol']))
    report = dict(version=VERSION, summary=summarize(records), all_candidates=len(records),
        inputs=inputs, btc_sources=btc_sources, minute_sources=sources,
        evidence_manifest_sha256=hashlib.sha256((ROOT/'research/swing_v1/evidence/MANIFEST.json').read_bytes()).hexdigest(),
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_cohorts_unchanged=True, network_requests=0, threshold_search=False,
        inference='DESCRIPTIVE_ALREADY_SEEN_DATA_NOT_CAUSAL_OR_VALIDATION',
        crypto_only=False, funding_included=False, new_strategy_approved=False)
    target.write_text(json.dumps(report, indent=2)+'\n')
    (here/'CAUSE_RECORDS.json.gz').write_bytes(gzip.compress(json.dumps(records).encode(), mtime=0))
    print(json.dumps(dict(summary=report['summary'])), flush=True)


if __name__ == '__main__': main()
