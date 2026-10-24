"""V4.2 entry checks, separate from broad chart discovery.

These are declared safety/selection hypotheses, not calibrated probabilities.
WATCH remains an observation; only fresh READY transitions require all checks.
"""
import math
from long_short_chart_setups import geometry

POLICY = 'READY_QUALITY_V42'


def entry_quality(p, price, now_ms):
    if p.get('ready_quality_policy') != POLICY:
        return dict(blockers=[])
    side = p['direction']
    sign = 1 if side == 'LONG' else -1
    entry = max(price, p['trigger']) if sign == 1 else min(price, p['trigger'])
    g = geometry(side, entry, p['stop'], p.get('obstacle'), p['cost_assumption_pct'])
    blockers = []
    if not g.get('valid') or g.get('net_reward_risk', 0)+1e-10 < p['ready_min_net_r']:
        blockers.append('READY_REWARD_BELOW_RISK')
    flow = p.get('hourly_flow') or {}
    end = (now_ms-250)//300000*300000
    fields = (flow.get('buy_share'), flow.get('price_return_pct'))
    valid = (flow.get('status') == 'OK' and flow.get('end_ms') == end
             and flow.get('start_ms') == end-3600000
             and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                     and math.isfinite(v) for v in fields)
             and 0 <= fields[0] <= 1)
    if not valid:
        blockers.append('READY_FLOW_UNAVAILABLE')
    else:
        if sign*(fields[0]-.5) <= 0:
            blockers.append('READY_FLOW_NOT_ALIGNED')
        if sign*fields[1] <= 0:
            blockers.append('READY_PRICE_NOT_RESPONDING')
    return dict(blockers=blockers, geometry=g, flow_fresh=valid)


def candidate_priority(p):
    """Fresh READY, then quality-passing watches; never use future outcomes."""
    q = p.get('entry_quality') or {}
    g = q.get('geometry') or p.get('geometry') or {}
    ratio = g.get('net_reward_risk')
    ratio = ratio if isinstance(ratio, (int, float)) and math.isfinite(ratio) else -1
    return (p['stage'] != 'READY', bool(q.get('blockers')),
            any(b.startswith('READY_FLOW') or b == 'READY_PRICE_NOT_RESPONDING'
                for b in q.get('blockers', [])), -ratio,
            abs(p['price']-p['trigger'])/p['atr5'], p['symbol'])
