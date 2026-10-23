"""Post-observation 1m path; gaps explicit, ambiguous minute stop-first.

Raw movement is separate from hypothetical stopped-plan performance.
Funding/executable spread are unavailable; never claim full net profitability.
"""
from bisect import bisect_left
from .config import CONFIG as C, HOUR, MINUTE
from .core import payoff


def measure(plan, minutes):
    start = (plan['asof_ms']//MINUTE+C['entry_delay_minutes'])*MINUTE
    end = start+C['horizon_hours']*HOUR
    timestamps = [b['open_ms'] for b in minutes]
    left = bisect_left(timestamps, start)
    right = bisect_left(timestamps, end)
    path = minutes[left:right]
    out = dict(reference_ms=start, horizon_end_ms=end, status='UNRESOLVED_ENTRY',
               entry=None, exit=None, fee_slippage_only_pct=None, full_net_pct=None,
               funding_included=False, assumption_cost_pct=C['assumed_round_trip_cost_pct'],
               raw_mfe_pct=None, raw_mae_pct=None, horizon_fee_only_pct=None, net_r=None)
    if not path or path[0]['open_ms'] != start:
        return out
    entry = path[0]['open']
    sign = 1 if plan['direction'] == 'LONG' else -1
    out['entry'] = entry
    is_plan = plan.get('plan_eligible', False)
    eligible = is_plan and payoff(plan['direction'], entry, plan['stop'], plan['target'])['valid']
    out['entry_cancelled'] = bool(is_plan and not eligible)
    favorable, adverse, elapsed = [], [], []
    expected = start
    reaches = {str(v): None for v in (1, 3, 5, 7, 10, 15)}
    for bar in path:
        if bar['open_ms'] != expected or bar['close_ms'] != expected+MINUTE-1:
            out['status'] = 'DATA_GAP'
            break
        expected += MINUTE
        elapsed.append(bar)
        best = 100*sign*((bar['high'] if sign == 1 else bar['low'])/entry-1)
        worst = -100*sign*((bar['low'] if sign == 1 else bar['high'])/entry-1)
        favorable.append(best); adverse.append(worst)
        for value in reaches:
            if reaches[value] is None and best >= float(value):
                reaches[value] = (bar['close_ms']+1-start)//MINUTE
        if eligible and out['exit'] is None:
            stop, target = plan['stop'], plan['target']
            stop_open = sign*(bar['open']-stop) <= 0
            target_open = sign*(bar['open']-target) >= 0
            stop_hit = bar['low'] <= stop if sign == 1 else bar['high'] >= stop
            target_hit = bar['high'] >= target if sign == 1 else bar['low'] <= target
            if stop_open:
                out['exit'] = dict(kind='GAP_STOP', price=bar['open'], at_ms=bar['open_ms'])
            elif target_open:
                out['exit'] = dict(kind='TARGET', price=target, at_ms=bar['open_ms'])
            elif stop_hit:
                out['exit'] = dict(kind='STOP_FIRST_AMBIGUOUS' if target_hit else 'STOP', price=stop, at_ms=bar['close_ms'])
            elif target_hit:
                out['exit'] = dict(kind='TARGET', price=target, at_ms=bar['close_ms'])
    complete = expected == end
    out['observation_complete'] = complete
    if complete:
        out['status'] = 'COMPLETE'
        out['raw_mfe_pct'] = max(0., max(favorable))
        out['raw_mae_pct'] = max(0., max(adverse))
        out['reach_minutes'] = reaches
        out['horizon_fee_only_pct'] = 100*sign*(path[-1]['close']/entry-1)-C['assumed_round_trip_cost_pct']
        if eligible and out['exit'] is None:
            out['exit'] = dict(kind='TIMEOUT', price=path[-1]['close'], at_ms=path[-1]['close_ms'])
    elif out['status'] != 'DATA_GAP':
        out['status'] = 'INCOMPLETE_HORIZON'
    if out['exit']:
        out['fee_slippage_only_pct'] = 100*sign*(out['exit']['price']/entry-1)-C['assumed_round_trip_cost_pct']
        risk = 100*sign*(entry-plan['stop'])/entry+C['assumed_round_trip_cost_pct']
        out['net_r'] = out['fee_slippage_only_pct']/risk
    return out
