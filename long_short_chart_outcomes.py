"""Version-two observation labels, including the notification's first minute.

Minute OHLC cannot reveal whether its high/low happened before a mid-minute
notification. Such a first barrier is UNRESOLVED_BOUNDARY, never a fabricated
win or a silently omitted minute. MFE/MAE are reported as bounds in that case.
"""
import math


def outcome(p, bars, horizon, cost_pct):
    observed = int(p['observed_ms'])
    start = observed//60000*60000
    end = ((observed+horizon*60000+59999)//60000)*60000
    mapping = {int(r[0]):r for r in bars}
    wanted = list(range(start, end, 60000))
    if any(t not in mapping for t in wanted):
        raise ValueError('MISSING_NATIVE_PATH_INCLUDING_FIRST_MINUTE')
    path = [mapping[t] for t in wanted]
    for r in path:
        if (int(r[6]) != int(r[0])+59999 or
                not all(math.isfinite(float(v)) and float(v)>0 for v in r[1:5]) or
                not r[3] <= min(r[1],r[4]) <= max(r[1],r[4]) <= r[2]):
            raise ValueError('INVALID_NATIVE_OUTCOME_CANDLE')
    price = p.get('price')
    if not isinstance(price,(float,int)) or not math.isfinite(price) or price<=0:
        raise ValueError('MISSING_REFERENCE_PRICE')
    partial = observed != start
    full = path[1:] if partial else path
    # Lower bound excludes unknown pre-signal extrema; upper bound includes them.
    known_prices = [price] + [float(path[0][4])] if partial else [price]
    high = max(known_prices+[float(r[2]) for r in full])
    low = min(known_prices+[float(r[3]) for r in full])
    result = dict(scope='POST_NOTIFICATION_REFERENCE_MODEL_NOT_ACTUAL_FILLS',
        observed_ms=observed, endpoint_ms=end-1,
        effective_horizon_seconds=(end-observed)/1000,
        endpoint_policy='FIRST_MINUTE_CLOSE_AT_OR_AFTER_HORIZON',
        initial_partial_minute_included=partial, first_level=None,
        first_level_minute=None, boundary_ambiguous=False,
        return_pct=(float(path[-1][4])/price-1)*100,
        max_up_pct_lower=max(0,(high/price-1)*100),
        max_up_pct_upper=max(0,(max(high,float(path[0][2]))/price-1)*100),
        max_down_pct_lower=min(0,(min(low,float(path[0][3]))/price-1)*100),
        max_down_pct_upper=min(0,(low/price-1)*100),
        first_barrier_status='RESOLVED')
    sign = 1 if p['direction']=='LONG' else -1 if p['direction']=='SHORT' else None
    if sign is None:
        return result
    result['directional_return_pct'] = sign*result['return_pct']
    stop, target = p.get('stop'), p.get('obstacle')
    exit_price = float(path[-1][4])
    for i,r in enumerate(path):
        stop_hit = stop is not None and (r[3]<=stop if sign==1 else r[2]>=stop)
        target_hit = target is not None and (r[2]>=target if sign==1 else r[3]<=target)
        if not (stop_hit or target_hit):
            continue
        if i==0 and partial:
            result.update(boundary_ambiguous=True,
                first_barrier_status='UNRESOLVED_BOUNDARY',
                possible_first_minute_levels=(["INVALIDATION"] if stop_hit else [])+
                                            (["OBSTACLE"] if target_hit else []))
            break
        level = 'INVALIDATION' if stop_hit else 'OBSTACLE'
        exit_price = stop if stop_hit else target
        # A gap through the stop exits at the first observed candle open.
        if stop_hit and sign*(float(r[1])-stop)<0:
            exit_price = float(r[1])
        result.update(first_level=level, first_level_minute=i,
            first_level_window_start_ms=max(observed,int(r[0])),
            first_level_window_end_ms=int(r[6]), same_minute_ambiguous=bool(stop_hit and target_hit),
            exit_reference_price=exit_price)
        break
    # WATCH and rejected ideas are observations, never presumed entries.
    if p.get('stage')=='DELIVERED_READY' and stop is not None and target is not None:
        if not result['boundary_ambiguous']:
            result.update(model_exit=result['first_level'] or 'HORIZON',
                model_net_return_pct=sign*(exit_price/price-1)*100-cost_pct,
                cost_assumption_pct=cost_pct,
                model_label='LOCKED_LEVELS_OR_HORIZON_WITH_ASSUMED_COST_NOT_USER_PNL')
        else:
            result['pessimistic_net_return_pct']=sign*(stop/price-1)*100-cost_pct
    return result
