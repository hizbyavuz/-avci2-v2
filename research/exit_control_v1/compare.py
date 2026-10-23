"""Offline matched-peer/exit diagnostic; no model tuning or production I/O."""
import argparse
import bisect
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import long_short_swing as swing
import long_short_short_retest as short
from long_short_swing_outcomes import initial, advance, result
from research.short_v2.replay_short import load_evidence, months_between
from research.swing_v1.replay import archive

HOUR, MINUTE = swing.HOUR, swing.MINUTE
HERE = Path(__file__).parent
VERSION = 'MATCHED_PEER_EXIT_DIAGNOSTIC_V1_2026_10_23'
COST_PCT = .30
SEED = 20261023


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def key(p): return f"{p['model']}:{p['symbol']}:{p['confirmation_ms']}"


def cohort(p):
    month = datetime.fromtimestamp(p['confirmation_ms']/1000, timezone.utc).strftime('%Y-%m')
    return month+':'+p['model']+':'+p['direction']


def inputs():
    screen = load_evidence('2026-08-screen.json.gz')['records']
    audits = load_evidence('2026-08-full-minute-audit.json.gz')['records']
    audit_map = {(a['symbol'], a['confirmation_ms'], a['direction']): a for a in audits}
    records = [dict(plan=r['plan'], original=audit_map[(r['plan']['symbol'],
        r['plan']['confirmation_ms'], r['plan']['direction'])]) for r in screen]
    proof = json.loads((ROOT/'research/short_v2/VALIDATION.json').read_text())['report_sha256']
    for month in ('2026-08', '2026-09'):
        path = ROOT/'research/short_v2'/f'{month}-EPISODES.json.gz'
        if sha(path) != proof[path.name]: raise ValueError('SHORT_INPUT_CHANGED')
        records += [dict(plan=r['plan'], original=r['audit']) for r in json.loads(gzip.decompress(path.read_bytes()))['records']]
    if len({key(r['plan']) for r in records}) != len(records): raise ValueError('DUPLICATE_EPISDE')
    return records


def point_features(symbol, rows):
    """Rolling validity counters ensure future rows cannot alter past features."""
    bad = [0]; gaps = [0]; volume = [0.]; out = []
    for i, b in enumerate(rows):
        invalid = False
        try:
            swing.check_bars([b], HOUR)
            q, buy = b.get('quote_volume'), b.get('taker_buy_quote')
            if not swing.finite(q) or q < 0 or not swing.finite(buy) or not 0 <= buy <= q:
                raise ValueError('VOLUME_INVALID')
        except (ValueError, KeyError, TypeError): invalid = True
        bad.append(bad[-1]+int(invalid))
        gaps.append(gaps[-1]+int(i > 0 and b['open_ms'] != rows[i-1]['open_ms']+HOUR))
        volume.append(volume[-1]+(b['quote_volume'] if not invalid else 0.))
        if i < 168: continue
        first = i-168
        if bad[i+1] != bad[first] or gaps[i+1] != gaps[first+1]: continue
        q24 = volume[i+1]-volume[i-23]
        a = swing.atr(rows[i-14:i+1])
        if q24 < 2_000_000 or not swing.finite(a) or a <= 0: continue
        out.append(dict(symbol=symbol, confirmation_ms=b['close_ms'], price=b['close'],
                        atr_1h=a, quote_volume_24h=q24))
    return out


def hour_job(job):
    cache, expected = job
    info, rows = archive(Path(cache), expected['symbol'], expected['month'], offline=True)
    if info['status'] != 'OK' or info['sha256'] != expected['sha256']:
        raise ValueError('HOUR_SOURCE_CHANGED:'+expected['symbol'])
    return point_features(expected['symbol'], rows)


def choose_peer(p, panel):
    ranked = []
    for peer in panel:
        if peer['symbol'] == p['symbol'] or peer['confirmation_ms'] != p['confirmation_ms']: continue
        distance = abs(math.log(peer['quote_volume_24h']/p['quote_volume_24h'])) + abs(
            math.log((peer['atr_1h']/peer['price'])/(p['atr_1h']/p['price'])))
        ranked.append((distance, peer['symbol'], peer))
    nearest = sorted(ranked, key=lambda x:(x[0], x[1]))[:5]
    if not nearest: return None
    seed = f'exit-control-v1:{SEED}:'+key(p)
    index = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(nearest)
    distance, _, peer = nearest[index]
    return dict(peer, distance=distance, eligible_peers=len(ranked), nearest_count=len(nearest),
                selection_seed=seed)


def select(cache, workers):
    target = HERE/'MATCHES.json.gz'
    if target.exists(): raise ValueError('MATCHES_ALREADY_FROZEN')
    records = inputs(); wanted = {r['plan']['confirmation_ms'] for r in records}
    hours = load_evidence('2026-08-coverage.json.gz') + load_evidence('2026-10-22-hourly-download-confirmed.json.gz')['archives']
    jobs = [(str(cache), r) for r in hours if r['status'] == 'OK']; panel = defaultdict(list)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(hour_job, j) for j in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            for row in future.result():
                if row['confirmation_ms'] in wanted: panel[row['confirmation_ms']].append(row)
            if i % 200 == 0: print(json.dumps(dict(hours_symbols_done=i, total=len(jobs))), flush=True)
    matches = []
    for r in records:
        p = r['plan']
        matches.append(dict(id=key(p), cohort=cohort(p), plan=p, peer=choose_peer(p, panel[p['confirmation_ms']])))
    matches.sort(key=lambda r:r['id'])
    data = dict(version=VERSION, protocol_sha256=sha(HERE/'PROTOCOL.md'), code_sha256=sha(__file__),
        selected_before_minute_lookup=True, network_requests=0,
        hour_sources=[r for r in hours if r['status']=='OK'], records=matches)
    target.write_bytes(gzip.compress(json.dumps(data).encode(), mtime=0))
    print(json.dumps(dict(matches=len(matches), no_peer=sum(r['peer'] is None for r in matches), sha256=sha(target))), flush=True)


def fixed_two_r(p, start, entry, path):
    """Conservative OHLC barrier ordering, with exact gap prices at stops."""
    if p['direction'] not in ('LONG', 'SHORT'): raise ValueError('INVALID_DIRECTION')
    state = initial(p, start, entry)
    sign = 1 if p['direction']=='LONG' else -1
    risk = state['initial_risk']; stop = p['stop']; target = entry+sign*2*risk
    if not swing.finite(target) or target <= 0: raise ValueError('INVALID_TARGET')
    cursor = start; end = start+168*HOUR; exit_ = None; missing = None
    for bar in path:
        t = bar['open_ms']
        if t >= end: break
        if t != cursor: missing = cursor; break
        try: swing.check_bars([bar], MINUTE)
        except (ValueError, KeyError, TypeError): missing = t; break
        if exit_ is None:
            stopped = bar['low'] <= stop if sign==1 else bar['high'] >= stop
            taken = bar['high'] >= target if sign==1 else bar['low'] <= target
            if stopped:
                price = min(bar['open'],stop) if sign==1 else max(bar['open'],stop)
                exit_ = dict(kind='STOP',price=price,at_ms=t,time_upper_ms=bar['close_ms'])
            elif taken:
                exit_ = dict(kind='TAKE_PROFIT_2R',price=target,at_ms=t,time_upper_ms=bar['close_ms'])
            elif t+MINUTE == end:
                exit_ = dict(kind='TIMEOUT',price=bar['close'],at_ms=bar['close_ms'],time_upper_ms=bar['close_ms'])
            if exit_: exit_['gross_pct'] = 100*sign*(exit_['price']/entry-1)
        cursor = t+MINUTE
    if cursor < end and missing is None: missing = cursor
    fee = exit_['gross_pct']-COST_PCT if exit_ else None
    return dict(status='COMPLETE' if missing is None else 'DATA_GAP', exit=exit_,
                fee_pct=fee, fee_r=fee/(100*risk/entry) if fee is not None else None,
                missing_ms=missing, full_net_pct=None, funding_status='UNKNOWN')


def measure_one(p, rows, stamps, control_risk_pct=None):
    start = (p['observed_ms']//MINUTE+1)*MINUTE; end = start+168*HOUR
    path = rows[bisect.bisect_left(stamps,start):bisect.bisect_left(stamps,end)]
    base = dict(status='ENTRY_DATA_MISSING', reference_ms=start, full_net_pct=None, funding_status='UNKNOWN')
    if not path or path[0]['open_ms'] != start: return base
    try: swing.check_bars(path[:1], MINUTE)
    except (ValueError, KeyError, TypeError): return dict(base,status='DATA_INVALID')
    entry = path[0]['open']; sign = 1 if p['direction']=='LONG' else -1
    if control_risk_pct is not None:
        if not swing.finite(control_risk_pct) or not 0 < control_risk_pct <= 12:
            return dict(base,status='ENTRY_INVALID',reason='INVALID_CONTROL_RISK')
        p = dict(p, stop=entry*(1-sign*control_risk_pct/100))
    else:
        geometry = short.geometry if p['version']==short.VERSION else swing.geometry
        reason = geometry(p, entry)
        if reason: return dict(base,status='ENTRY_INVALID',reason=reason)
    state = advance(p,initial(p,start,entry),path)
    if state['cursor_ms'] < end: state['missing_ms'] = state['cursor_ms']
    original = result(p,state); risk = 100*state['initial_risk']/entry
    trail = dict(status=original['status'], exit=original['exit'],
        fee_pct=original['fee_slippage_only_pct'], fee_r=original['fee_slippage_only_pct']/risk
            if original['fee_slippage_only_pct'] is not None else None,
        missing_ms=state['missing_ms'],full_net_pct=None,funding_status='UNKNOWN')
    return dict(base,status='MEASURED',entry=entry,stop=p['stop'],risk_pct=risk,
                exits=dict(FROZEN_TRAIL=trail,FIXED_2R=fixed_two_r(p,start,entry,path)))


def minute_job(job):
    cache, symbol, records, hashes = job
    requested = sorted({m for r in records for m in months_between(
        (r['plan']['observed_ms']//MINUTE+1)*MINUTE,
        (r['plan']['observed_ms']//MINUTE+1)*MINUTE+168*HOUR)})
    path = []; sources = []
    for month in requested:
        expected = hashes.get((symbol,month))
        if expected is None:
            sources.append(dict(symbol=symbol,month=month,status='NO_VERIFIED_ARCHIVE')); continue
        info, rows = archive(Path(cache),symbol,month,'1m',offline=True)
        if info['status']!='OK' or info['sha256']!=expected: raise ValueError('MINUTE_SOURCE_CHANGED:'+symbol)
        sources.append(dict(symbol=symbol,month=month,status='OK',sha256=expected)); path += rows
    stamps = [b['open_ms'] for b in path]
    if any(b <= a for a,b in zip(stamps,stamps[1:])): raise ValueError('MINUTES_UNORDERED')
    return [(r['id'],measure_one(r['plan'],path,stamps,r.get('risk_pct'))) for r in records], sources


def run_jobs(cache, records, hashes, workers, label):
    groups = defaultdict(list)
    for r in records: groups[r['plan']['symbol']].append(r)
    values = {}; sources = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(minute_job,(str(cache),s,r,hashes)) for s,r in sorted(groups.items())]
        for i, future in enumerate(as_completed(futures),1):
            rows, src = future.result(); values.update(rows); sources += src
            if i % 100 == 0: print(json.dumps(dict(phase=label,symbols_done=i,total=len(groups))),flush=True)
    return values, sources


def block_interval(rows, field):
    if not rows: return dict(status='NO_PAIRS')
    days = defaultdict(list)
    for r in rows: days[(r['confirmation_ms']+1000)//(24*HOUR)].append(r[field])
    calendar = list(range(min(days),max(days)+1))
    if len(calendar)<7: return dict(status='TOO_FEW_DAYS',calendar_days=len(calendar))
    rng=random.Random(SEED); estimates=[]
    for _ in range(2000):
        selected=[]
        while len(selected)<len(calendar):
            i=rng.randrange(len(calendar)-6);selected.extend(calendar[i:i+7])
        # Reading an absent day must not insert an empty key into defaultdict:
        # that would overstate the number of days with actual observations.
        values=[x for d in selected[:len(calendar)] for x in days.get(d,[])]
        if values: estimates.append(statistics.mean(values))
    estimates.sort()
    return dict(status='DESCRIPTIVE_DEPENDENT_DEVELOPMENT_SAMPLE',lower=estimates[int(.05*len(estimates))],
        upper=estimates[min(len(estimates)-1,int(.95*len(estimates)))],calendar_days=len(calendar),
        signal_days=len(days),block_days=7,repeats=2000,usable_repeats=len(estimates),seed=SEED)


def full(r):
    return r.get('status')=='MEASURED' and all(x['status']=='COMPLETE' for x in r['exits'].values())


def numbers(values):
    return dict(count=len(values),positive=sum(x>0 for x in values),
                mean_fee_only_r=statistics.mean(values) if values else None)


def summary(records):
    answer={}
    for group in sorted({r['cohort'] for r in records}):
        all_=[r for r in records if r['cohort']==group]
        signals=[r for r in all_ if full(r['signal'])]
        pairs=[r for r in signals if full(r['control'])]
        result_=dict(candidates=len(all_),signal_statuses=dict(Counter(r['signal']['status'] for r in all_)),
            control_statuses=dict(Counter(r['control']['status'] for r in all_)),
            signal_full_paths=len(signals),paired_full_paths=len(pairs),unpaired_candidates=len(all_)-len(pairs),
            unique_signal_symbols=len({r['plan']['symbol'] for r in pairs}),
            unique_peer_symbols=len({r['peer']['symbol'] for r in pairs}),
            unique_signal_hours=len({r['plan']['confirmation_ms'] for r in pairs}),all_full_signals={},paired={})
        deltas=[]
        for exit_name in ('FROZEN_TRAIL','FIXED_2R'):
            result_['all_full_signals'][exit_name]=numbers([r['signal']['exits'][exit_name]['fee_r'] for r in signals])
            values=[dict(confirmation_ms=r['plan']['confirmation_ms'],
                delta=r['signal']['exits'][exit_name]['fee_r']-r['control']['exits'][exit_name]['fee_r']) for r in pairs]
            result_['paired'][exit_name]=dict(signal=numbers([r['signal']['exits'][exit_name]['fee_r'] for r in pairs]),
                control=numbers([r['control']['exits'][exit_name]['fee_r'] for r in pairs]),
                signal_minus_control=numbers([r['delta'] for r in values]),descriptive_interval=block_interval(values,'delta'))
        for r in signals:
            deltas.append(dict(confirmation_ms=r['plan']['confirmation_ms'],
                delta=r['signal']['exits']['FIXED_2R']['fee_r']-r['signal']['exits']['FROZEN_TRAIL']['fee_r']))
        result_['same_signals_fixed_minus_trail']=dict(**numbers([r['delta'] for r in deltas]),
            descriptive_interval=block_interval(deltas,'delta'))
        answer[group]=result_
    return answer


def measure(cache, workers):
    if (HERE/'SUMMARY.json').exists(): raise ValueError('RESULTS_EXIST')
    matches=json.loads(gzip.decompress((HERE/'MATCHES.json.gz').read_bytes()))
    if matches['code_sha256']!=sha(__file__) or matches['protocol_sha256']!=sha(HERE/'PROTOCOL.md'):
        raise ValueError('FROZEN_COMPARISON_CHANGED')
    original={key(r['plan']):r['original'] for r in inputs()}
    minute=load_evidence('2026-10-22-minute-download-complete.json.gz')['archives']
    hashes={(r['symbol'],r['month']):r['sha256'] for r in minute if r['status']=='OK'}
    signals, sources=run_jobs(cache,matches['records'],hashes,workers,'SIGNAL')
    controls=[]
    for r in matches['records']:
        a=signals[r['id']];old=original[r['id']]
        if a['status']!=old['status']: raise ValueError('ORIGINAL_ENTRY_STATUS_CHANGED:'+r['id'])
        if a['status']=='MEASURED':
            got=a['exits']['FROZEN_TRAIL'];old_out=old['outcome'];fee=old_out['fee_slippage_only_pct']
            if got['status']!=old_out['status'] or (fee is None)!=(got['fee_pct'] is None) or (
                fee is not None and abs(got['fee_pct']-fee)>1e-9):
                raise ValueError('ORIGINAL_OUTCOME_CHANGED:'+r['id'])
            if r['peer']:
                peer=r['peer'];p=dict(r['plan'],symbol=peer['symbol'],atr_1h=peer['atr_1h'])
                controls.append(dict(id=r['id'],plan=p,risk_pct=a['risk_pct']))
    outcomes,control_sources=run_jobs(cache,controls,hashes,workers,'PEER')
    records=[]
    for r in matches['records']:
        control=outcomes.get(r['id'],dict(status='NO_PEER' if r['peer'] is None else 'NO_VALID_SIGNAL_ENTRY'))
        records.append(dict(r,signal=signals[r['id']],control=control))
    report=dict(version=VERSION,summary=summary(records),all_candidates=len(records),
        original_outcomes_reproduced=True,matched_before_availability_lookup=True,
        matches_sha256=sha(HERE/'MATCHES.json.gz'),code_sha256=sha(__file__),protocol_sha256=sha(HERE/'PROTOCOL.md'),
        minute_sources=sources+control_sources,network_requests=0,crypto_only=False,
        funding_included=False,independent_validation=False,activation_eligible=False)
    (HERE/'SUMMARY.json').write_text(json.dumps(report,indent=2)+'\n')
    (HERE/'OUTCOMES.json.gz').write_bytes(gzip.compress(json.dumps(records).encode(),mtime=0))
    print(json.dumps(dict(candidates=len(records),summary=report['summary'])),flush=True)


def report_correction():
    """Repair report metadata from immutable outcomes; no replay or reselection."""
    proof=json.loads((HERE/'REPORT_CORRECTION.json').read_text())
    if sha(HERE/'OUTCOMES.json.gz')!=proof['outcomes_sha256'] or sha(HERE/'MATCHES.json.gz')!=proof['matches_sha256']:
        raise ValueError('EVIDENCE_CHANGED')
    raw=gzip.decompress((HERE/'SUMMARY_BEFORE_DAY_COUNT_FIX.json.gz').read_bytes())
    if hashlib.sha256(raw).hexdigest()!=proof['original_summary_sha256']:raise ValueError('ORIGINAL_REPORT_CHANGED')
    original=json.loads(raw);records=json.loads(gzip.decompress((HERE/'OUTCOMES.json.gz').read_bytes()))
    revised=summary(records)
    def strip_day_count(value):
        if isinstance(value,dict):return {k:strip_day_count(v) for k,v in value.items() if k!='signal_days'}
        if isinstance(value,list):return [strip_day_count(x) for x in value]
        return value
    if strip_day_count(revised)!=strip_day_count(original['summary']):
        raise ValueError('UNEXPECTED_NUMERICAL_CHANGE')
    report=dict(original,summary=revised,report_code_sha256=sha(__file__),
        reporting_correction=proof,trade_results_unchanged=True)
    (HERE/'SUMMARY.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(corrected='signal_days metadata',trade_results_unchanged=True)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['select','measure','report-correction'])
    parser.add_argument('--cache',type=Path);parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    if args.phase=='report-correction':report_correction()
    else:
        if args.cache is None:parser.error('--cache is required for select/measure')
        (select if args.phase=='select' else measure)(args.cache,args.workers)
