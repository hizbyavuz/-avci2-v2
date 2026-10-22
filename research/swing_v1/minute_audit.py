"""Audit every frozen episode against available minute data, entirely offline."""
import argparse
import bisect
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import statistics

import replay
from long_short_swing import CONFIG, CONFIG_HASH, HOUR, MINUTE, check_bars, geometry
from long_short_swing_outcomes import VERSION as OUTCOME_VERSION, initial, advance, result

VERSION='MINUTE_COVERAGE_AUDIT_V1'
COST_SCENARIOS=(.30,.60,1.)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def months_between(start, end):
    """Months intersecting the half-open [start,end) interval, in UTC."""
    if end <= start:raise ValueError('EMPTY_WINDOW')
    a=datetime.fromtimestamp(start/1000,timezone.utc)
    b=datetime.fromtimestamp((end-1)/1000,timezone.utc)
    y,m=a.year,a.month
    months=[]
    while (y,m) <= (b.year,b.month):
        months.append(f'{y:04d}-{m:02d}')
        y,m=(y+1,1) if m==12 else (y,m+1)
    return months


def merge_windows(windows):
    out=[]
    for start,end in sorted(windows):
        if not out or start > out[-1][1]:out.append([start,end])
        else:out[-1][1]=max(out[-1][1],end)
    return out


def reference_window(p):
    start=(p['observed_ms']//MINUTE+1)*MINUTE
    return start,start+CONFIG['max_hold_hours']*HOUR


def audit_one(p, rows):
    start,end=reference_window(p)
    item=dict(symbol=p['symbol'],direction=p['direction'],confirmation_ms=p['confirmation_ms'],
        observed_ms=p['observed_ms'],reference_ms=start,end_exclusive_ms=end,
        observed_price=p['price'],initial_stop=p['stop'],entry_price=None,
        full_net_pct=None,funding_status='UNKNOWN',outcome=None)
    # Duplicate timestamps and out-of-order input must not be repaired silently.
    stamps=[b['open_ms'] for b in rows]
    if any(b<=a for a,b in zip(stamps,stamps[1:])):
        return dict(item,status='DATA_INVALID',reason='DUPLICATE_OR_UNORDERED_MINUTES')
    lo=bisect.bisect_left(stamps,start)
    hi=bisect.bisect_left(stamps,end)
    path=rows[lo:hi]
    if not path or path[0]['open_ms']!=start:
        sign=1 if p['direction']=='LONG' else -1
        hypothetical=-100*sign*(p['price']-p['stop'])/p['price']-CONFIG['round_trip_cost_pct']
        return dict(item,status='ENTRY_DATA_MISSING',reason='EXACT_NEXT_MINUTE_NOT_AVAILABLE',
            missing_assumed_observed_price_stop_pct=hypothetical,
            sensitivity_label='HYPOTHETICAL_NOT_MEASURED_NOT_WORST_CASE')
    try:check_bars(path[:1],MINUTE)
    except (ValueError,KeyError,TypeError) as exc:
        return dict(item,status='DATA_INVALID',reason=str(exc))
    entry=path[0]['open'];item['entry_price']=entry
    reason=geometry(p,entry)
    if reason:return dict(item,status='ENTRY_INVALID',reason=reason)
    state=advance(p,initial(p,start,entry),path)
    if state['cursor_ms']<end:state['missing_ms']=state['cursor_ms']
    outcome=result(p,state)
    risk_pct=100*state['initial_risk']/entry
    item.update(status='MEASURED',outcome=outcome,initial_risk_pct=risk_pct,
        minutes_measured=(state['cursor_ms']-start)//MINUTE,first_missing_ms=state['missing_ms'],
        initial_risk_r=1.,fee_slippage_only_r=(outcome['fee_slippage_only_pct']/risk_pct
            if outcome['fee_slippage_only_pct'] is not None else None),
        holding_hours=(outcome['exit']['at_ms']-start)/HOUR if outcome['exit'] else None)
    item['reach_classification']={}
    for level in (10,30):
        key=str(level);hit=outcome['raw_reach'].get(key)
        if key in outcome['in_plan_reach']:label='IN_PLAN_CONFIRMED'
        elif hit is None:label='NOT_REACHED' if outcome['status']=='COMPLETE' else 'UNKNOWN_INCOMPLETE'
        elif outcome['exit'] and hit>outcome['exit']['time_upper_ms']:label='ONLY_AFTER_EXIT'
        elif outcome['exit'] and outcome['exit']['at_ms']<=hit<=outcome['exit']['time_upper_ms']:label='AMBIGUOUS_EXIT_MINUTE'
        else:label='UNRESOLVED'
        item['reach_classification'][key]=label
    item['cost_sensitivity']={}
    for cost in COST_SCENARIOS:
        gross=outcome['exit']['gross_pct'] if outcome['exit'] else None
        item['cost_sensitivity'][str(cost)]=dict(fee_slippage_only_pct=gross-cost if gross is not None else None,
            fee_slippage_only_r=(gross-cost)/risk_pct if gross is not None else None,
            full_net_pct=None)
    return item


def trade_stats(items, cost=.30):
    vals=[r['cost_sensitivity'][str(cost)]['fee_slippage_only_pct'] for r in items]
    rs=[r['cost_sensitivity'][str(cost)]['fee_slippage_only_r'] for r in items]
    if any(v is None for v in vals):raise ValueError('OPEN_PLAN_IN_CLOSED_COHORT')
    wins=sum(v>0 for v in vals)
    gains=sum(max(0.,v) for v in vals);losses=-sum(min(0.,v) for v in vals)
    return dict(count=len(items),profitable_fee_only=wins,losing_fee_only=sum(v<0 for v in vals),
        positive_fee_only_pct=100*wins/len(vals) if vals else None,
        mean_fee_only_pct=statistics.mean(vals) if vals else None,
        median_fee_only_pct=statistics.median(vals) if vals else None,
        mean_fee_only_r=statistics.mean(rs) if rs else None,
        equal_notional_profit_factor_fee_only=gains/losses if losses else None,
        profit_factor_status='DEFINED' if losses else 'UNDEFINED_NO_LOSSES_OR_EMPTY',
        full_net_known_count=0,full_net_mean_pct=None)


def summarize(items):
    out={}
    for side in ('ALL','LONG','SHORT'):
        group=[r for r in items if side=='ALL' or r['direction']==side]
        measured=[r for r in group if r['status']=='MEASURED']
        complete=[r for r in measured if r['outcome']['status']=='COMPLETE']
        closed_incomplete=[r for r in measured if r['outcome']['plan_closed'] and r['outcome']['status']!='COMPLETE']
        horizons={}
        for h in ('24','72','168'):
            cp=[r['outcome']['checkpoints'][h] for r in measured if h in r['outcome']['checkpoints']]
            horizons[h]=dict(measured_count=len(cp),missing_or_no_entry=len(group)-len(cp),
                open_plans_included=sum(x['open_plan'] for x in cp),
                mean_fee_only_pct=statistics.mean(x['fee_slippage_only_pct'] for x in cp) if cp else None,
                full_net_pct=None)
        out[side]=dict(all_candidates=len(group),status_counts=dict(Counter(r['status'] for r in group)),
            outcome_status_counts=dict(Counter(r['outcome']['status'] for r in measured)),
            complete_path_conditional_technical_sample=trade_stats(complete),
            closed_but_raw_path_incomplete=trade_stats(closed_incomplete),
            cost_sensitivity_complete_sample={str(c):trade_stats(complete,c) for c in COST_SCENARIOS},
            reach_classifications_complete_sample={str(t):dict(Counter(r['reach_classification'][str(t)] for r in complete)) for t in (10,30)},
            horizons_with_open_and_closed_plans=horizons,
            overall_strategy_win_rate=None,overall_strategy_expectancy=None)
    return out


def run(records,cache,expected_hashes=None):
    grouped=defaultdict(list)
    windows=defaultdict(list)
    for r in records:
        p=r['plan'];grouped[p['symbol']].append(p);windows[p['symbol']].append(reference_window(p))
    items=[];archives=[];requests=[]
    for symbol,plans in sorted(grouped.items()):
        merged=merge_windows(windows[symbol]);months=sorted({m for a,b in merged for m in months_between(a,b)})
        rows=[];missing=[]
        for month in months:
            info,part=replay.archive(cache,symbol,month,'1m',offline=True)
            expected=(expected_hashes or {}).get((symbol,month))
            if expected and (info['status']!='OK' or info.get('sha256')!=expected):
                raise ValueError('PREVIOUSLY_OBSERVED_ARCHIVE_CHANGED:'+symbol+':'+month)
            archives.append(info)
            if info['status']=='OK':rows.extend(part)
            else:missing.append(dict(month=month,status=info['status'],url=info['url']))
        for p in plans:items.append(audit_one(p,rows))
        requests.append(dict(symbol=symbol,candidates=len(plans),
            minute_windows_inclusive_start_exclusive_end=merged,missing_minute_archives=missing,
            funding_windows_requested=merged,
            funding_required_fields=['time_ms','rate','mark_price'],
            funding_coverage_requirement='Complete authoritative extract with explicit coverage; never infer completeness from first/last event',
            historical_asset_class='UNVERIFIED'))
    items.sort(key=lambda x:(x['confirmation_ms'],x['symbol'],x['direction']))
    return items,archives,requests


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--screen',type=Path,default=Path(__file__).with_name('results')/'2026-08-screen.json')
    args=parser.parse_args();root=Path(__file__).parent
    screen=json.loads(args.screen.read_text())
    if screen['config_hash']!=CONFIG_HASH:raise ValueError('CONFIG_MISMATCH')
    previous=root/'results'/'2026-08-minute-check.json'
    known=json.loads(previous.read_text())
    expected={(r['archive']['symbol'],r['archive']['month']):r['archive']['sha256']
        for r in known if r['archive']['status']=='OK'}
    items,archives,requests=run(screen['records'],args.cache,expected)
    manifest=dict(version=VERSION,outcome_version=OUTCOME_VERSION,config_hash=CONFIG_HASH,
        classification='CONVENIENCE_CACHE_SAMPLE_NOT_STRATEGY_VALIDATION',
        universe='UNCLASSIFIED_USDT_ARCHIVE_V1',crypto_only=False,network_used=False,
        screen_sha256=sha(args.screen),protocol_sha256=sha(root/'MINUTE_AUDIT_PROTOCOL.md'),
        code_sha256=sha(Path(__file__)),outcome_code_sha256=sha(root.parents[1]/'long_short_swing_outcomes.py'),
        total_candidates=len(items),candidate_symbols=len({r['symbol'] for r in items}),
        prior_minute_inventory_sha256=sha(previous),previously_observed_archives_verified=len(expected),
        archive_status_counts=dict(Counter(a['status'] for a in archives)),
        funding_status='UNKNOWN',cost_scenarios_pct=COST_SCENARIOS)
    full=dict(manifest=manifest,summary=summarize(items),records=items,archives=archives)
    body=json.dumps(full,ensure_ascii=False,indent=2).encode()
    (root/'results'/'2026-08-minute-audit.json').write_bytes(body)
    (root/'evidence'/'2026-08-minute-audit.json.gz').write_bytes(gzip.compress(body,mtime=0))
    (root/'MINUTE_AUDIT.json').write_text(json.dumps(dict(manifest=manifest,summary=full['summary'],
        records_with_entry_data=[r for r in items if r['status']!='ENTRY_DATA_MISSING']),ensure_ascii=False,indent=2))
    needed=dict(manifest=manifest,symbol_requests=requests,
        note='Data requirements only; no network operation or funding completeness assertion was made.')
    request_body=json.dumps(needed,ensure_ascii=False,indent=2).encode()
    (root/'results'/'minute-data-requests.json').write_bytes(request_body)
    (root/'evidence'/'minute-data-requests.json.gz').write_bytes(gzip.compress(request_body,mtime=0))
    print(json.dumps(dict(manifest=manifest,summary=full['summary']),ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
