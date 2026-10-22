"""Funding cost intervals when only settlement-minute mark OHLC is available.

These are bounds under the stated bar/fee execution model, not guaranteed fills
or returns. An archive extract must certify its covered period independently of
the timestamps of the funding rows. Missing rates/marks never mean zero cost.
"""
from long_short_swing import HOUR,MINUTE,finite
from long_short_trader_context import ordered

VERSION='SETTLED_FUNDING_MARK_BOUNDS_V1_2026_10_22'


def measure(direction,entry,entry_ms,exit_,fee_only_pct,rates,marks,coverage):
    if direction not in ('LONG','SHORT') or not finite(entry) or entry<=0:
        raise ValueError('INVALID_POSITION')
    end=exit_['time_upper_ms']
    if not coverage or coverage[0]>entry_ms or coverage[1]<end:
        raise ValueError('FUNDING_EXTRACT_COVERAGE_MISSING')
    if not finite(fee_only_pct) or end<entry_ms:raise ValueError('INVALID_OUTCOME')
    rows=ordered(rates,'time_ms',('rate','interval_hours'))
    before=[i for i,r in enumerate(rows) if r['time_ms']<=entry_ms]
    if not before:raise ValueError('NO_PRE_ENTRY_FUNDING_ANCHOR')
    first=before[-1];last=next((i for i,r in enumerate(rows) if r['time_ms']>end),len(rows)-1)
    window=rows[first:last+1]
    for i,r in enumerate(window):
        hours=r['interval_hours']
        if not 0<hours<=24:raise ValueError('INVALID_FUNDING_INTERVAL')
        if i and abs(r['time_ms']-window[i-1]['time_ms']-hours*HOUR)>5000:
            raise ValueError('FUNDING_SCHEDULE_GAP_OR_CHANGE_UNRESOLVED')
    if not window or window[-1]['time_ms']+window[-1]['interval_hours']*HOUR+5000<end:
        raise ValueError('FUNDING_TAIL_GAP')
    sign=1 if direction=='LONG' else -1;low_cost=0.;high_cost=0.;events=[]
    for r in window:
        t=r['time_ms']
        if not entry_ms<=t<=end:continue
        mark=marks.get(t//MINUTE*MINUTE)
        if not mark or not all(finite(x) and x>0 for x in mark) or mark[0]>mark[1]:
            raise ValueError('SETTLEMENT_MARK_MINUTE_MISSING_OR_INVALID')
        possible=[100*sign*r['rate']*m/entry for m in mark]
        ambiguous=exit_['kind']=='STOP' and exit_['at_ms']<=t<=end
        entry_ambiguous=t==entry_ms
        if ambiguous or entry_ambiguous:possible.append(0.)
        low,high=min(possible),max(possible);low_cost+=low;high_cost+=high
        events.append(dict(time_ms=t,rate=r['rate'],mark_low=mark[0],mark_high=mark[1],
            cost_low_pct=low,cost_high_pct=high,exit_minute_ambiguous=ambiguous,
            entry_settlement_simultaneous=entry_ambiguous))
    net_low=fee_only_pct-high_cost;net_high=fee_only_pct-low_cost
    return dict(version=VERSION,status='BOUNDED',funding_cost_low_pct=low_cost,
                funding_cost_high_pct=high_cost,fee_slippage_and_funding_net_low_pct=net_low,
                fee_slippage_and_funding_net_high_pct=net_high,exact_full_net_pct=None,
                sign_under_cost_bounds='POSITIVE' if net_low>0 else 'NEGATIVE' if net_high<0 else 'UNCERTAIN_OR_ZERO',
                events=events)
