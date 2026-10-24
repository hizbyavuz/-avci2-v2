"""Bounded discovery diagnostics. Early observations cannot create episodes.

Use only verified, closed input frames from the production assessment. This
observer measures a possible 15m turn before the hourly direction agrees; it
does not relax production entry, flow, liquidity, or data-health requirements.
"""
import math


def short_flow_aligned(flow, direction, now_ms, minutes=15):
    if not flow or flow.get('status')!='OK':return False
    end=(now_ms-250)//300000*300000
    w=(flow.get('recent_windows') or {}).get(str(minutes)) or {}
    values=(w.get('buy_share'),w.get('price_return_pct'),w.get('quote_volume'))
    if (w.get('end_ms')!=end or w.get('start_ms')!=end-minutes*60000 or
            not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
                    for v in values) or not 0<=values[0]<=1 or values[2]<=0):return False
    sign=1 if direction=='LONG' else -1
    return sign*(values[0]-.5)>0 and sign*values[1]>0


def market_position(frames, price):
    """Distances are measurements, not claims that a top/bottom will reverse."""
    hours=frames['1h'][-24:]
    high=max(r[2] for r in hours);low=min(r[3] for r in hours)
    out=dict(high_24_closed_hours=high,low_24_closed_hours=low,
             position_in_24h_range_pct=100*(price-low)/(high-low) if high>low else None,
             distance_to_24h_high_pct=(high/price-1)*100,
             distance_to_24h_low_pct=(1-low/price)*100,
             return_24_closed_hours_pct=(hours[-1][4]/hours[0][1]-1)*100)
    for minutes,count in ((15,3),(30,6),(60,12)):
        part=frames['5m'][-count:]
        out[f'return_{minutes}m_closed_pct']=(part[-1][4]/part[0][1]-1)*100
    return out


def early_observation(base, frames, now_ms):
    """Observe a defined 5m plan + aligned 15m turn while 1h has not turned."""
    from long_short_chart_first import CONFIG, assess_pattern, daily_from_4h, pivots
    from long_short_chart_setups import setups, level_watch
    from long_short_chart_quality import candidate_priority
    ctx=base.get('context') or {}
    if not all(tf in ctx for tf in ('5m','15m','1h','4h','1d')):return None
    desired=ctx['15m']['direction']
    if desired not in ('UP','DOWN') or ctx['1h']['direction']==desired:return None
    direction='LONG' if desired=='UP' else 'SHORT'
    if not short_flow_aligned(base.get('hourly_flow'),direction,now_ms):return None
    h,l=pivots(frames['5m'])
    patterns=setups(frames['5m'],direction,h,l,CONFIG)
    watch=level_watch(frames['5m'],direction,h,l,CONFIG)
    if watch is not None:patterns.append(watch)
    expanded=dict(frames,**{'1d':daily_from_4h(frames['4h'])})
    candidates=[]
    for pattern in patterns:
        seed=dict(base,direction=direction,stage='REJECTED',reason=None,
                  scenario='EARLY_TURN_OBSERVATION',warnings=[])
        p=assess_pattern(seed,pattern,expanded,base['price'],now_ms)
        if p['stage'] in ('WATCH','READY'):candidates.append(p)
    if not candidates:return None
    selected=min(candidates,key=candidate_priority)
    selected.update(observed_setup_stage=selected['stage'],stage='EARLY_OBSERVATION',
                    reason='HOURLY_DIRECTION_NOT_YET_ALIGNED',telegram_eligible=False,
                    observation_policy=CONFIG['early_observation_policy'])
    return selected


def audit_row(p):
    ctx=p.get('context') or {}
    return dict(symbol=p['symbol'],direction=p['direction'],stage=p['stage'],reason=p['reason'],
                price=p.get('price'),model=p.get('model'),trigger=p.get('trigger'),
                stop=p.get('stop'),obstacle=p.get('obstacle'),
                frames={tf:c['direction'] for tf,c in ctx.items()},
                position=p.get('market_position'),flow=p.get('hourly_flow'),
                ready_blockers=p.get('ready_blockers',[]),patterns=p.get('pattern_evaluations',[]))
