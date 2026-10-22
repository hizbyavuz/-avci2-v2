"""Point-in-time evidence for the existing swing engine. No invented probabilities.

OI is base-asset open interest, account ratios count accounts, and settled
funding is not a forecast. This module never sends a message or places an order.
The fixed variants are candidates for validation, not certified trade signals.
"""
import hashlib
import json
import math
import statistics

from long_short_swing import HOUR, MINUTE, check_bars, finite, four_hour

VERSION='TRADER_CONTEXT_V2_2026_10_22'
CONFIG=dict(metrics_delay_ms=5*MINUTE, metrics_max_age_ms=10*MINUTE,
            beta_min_returns=96, carry_hours=72, carry_max_r=.25,
            round_trip_cost_pct=.30, crowded_long_accounts=2., crowded_short_accounts=.5,
            target_r=2., max_stop_pct=12., max_chase_atr=1.)
CONFIG_HASH=hashlib.sha256(json.dumps(CONFIG,sort_keys=True).encode()).hexdigest()


def ordered(rows, key, fields):
    """No order dependence, but never silently choose conflicting duplicates."""
    unique={}
    for row in rows:
        stamp=row.get(key)
        if not isinstance(stamp,int) or isinstance(stamp,bool):raise ValueError('INVALID_TIMESTAMP')
        for f in fields:
            if not finite(row.get(f)):raise ValueError('NONFINITE_'+f.upper())
        if stamp in unique and any(row[f]!=unique[stamp][f] for f in fields):
            raise ValueError('CONFLICTING_DUPLICATE')
        unique[stamp]=row
    return [unique[t] for t in sorted(unique)]


def derivative_features(metrics, funding, decision_ms):
    """Inputs carry event and availability times, not download times.

    A live adapter must supply actual observed availability. The archive adapter
    supplies an explicit reconstruction delay; it does not certify live latency.
    """
    out=dict(errors={},oi_change_1h_pct=None,oi_change_4h_pct=None,oi_change_24h_pct=None,
             account_ratio=None,funding_rate=None,funding_interval_hours=None,
             funding_paid_scenario_pct=None,oi_state='UNKNOWN')
    try:
        # Future rows cannot change a past decision, including validation errors.
        visible=[r for r in metrics if r.get('available_ms',decision_ms+1)<=decision_ms
                 and r.get('time_ms',decision_ms+1)<=decision_ms]
        series=ordered(visible,'time_ms',('oi_base','account_ratio'))
        if not series:raise ValueError('OI_HISTORY_MISSING')
        last=series[-1];end=last['time_ms']
        if not 0<=decision_ms-end<=CONFIG['metrics_max_age_ms']:raise ValueError('OI_STALE')
        if end%(5*MINUTE):raise ValueError('OI_TIME_NOT_ALIGNED')
        by_time={r['time_ms']:r for r in series}
        if not 0<last['account_ratio']:raise ValueError('ACCOUNT_RATIO_INVALID')
        out.update(oi_asof_ms=end,account_ratio=last['account_ratio'])
        for hours in (1,4,24):
            times=range(end-hours*HOUR,end+1,5*MINUTE)
            window=[by_time.get(t) for t in times]
            if any(r is None or r['oi_base']<=0 for r in window):
                out['errors'][f'oi_{hours}h']='OI_WINDOW_INCOMPLETE';continue
            out[f'oi_change_{hours}h_pct']=100*(last['oi_base']/window[0]['oi_base']-1)
        v=out['oi_change_1h_pct']
        if v is not None:out['oi_state']='BUILD' if v>0 else 'UNWIND' if v<0 else 'FLAT'
    except (ValueError,KeyError,TypeError) as exc:out['errors']['metrics']=str(exc)
    try:
        visible=[r for r in funding if r.get('available_ms',decision_ms+1)<=decision_ms
                 and r.get('time_ms',decision_ms+1)<=decision_ms]
        rates=ordered(visible,'time_ms',('rate','interval_hours'))
        if not rates:raise ValueError('SETTLED_FUNDING_MISSING')
        last=rates[-1]
        if not 0<last['interval_hours']<=24:raise ValueError('FUNDING_INTERVAL_INVALID')
        if decision_ms-last['time_ms']>last['interval_hours']*HOUR+5*MINUTE:
            raise ValueError('SETTLED_FUNDING_STALE')
        out.update(funding_rate=last['rate'],funding_interval_hours=last['interval_hours'],
                   funding_asof_ms=last['time_ms'])
    except (ValueError,KeyError,TypeError) as exc:out['errors']['funding']=str(exc)
    return out


def price_features(rows, btc_rows, decision_ms):
    out=dict(errors={})
    try:
        rows=[r for r in rows if r['close_ms']<decision_ms][-169:]
        if len(rows)<169:raise ValueError('HISTORY_MISSING_169H')
        check_bars(rows,HOUR,decision_ms)
        if decision_ms-rows[-1]['close_ms']>15*MINUTE:raise ValueError('PRICE_STALE')
        for b in rows:
            q,buy=b.get('quote_volume'),b.get('taker_buy_quote')
            if not finite(q) or q<0 or not finite(buy) or not 0<=buy<=q:
                raise ValueError('FLOW_INVALID')
        q4=sum(r['quote_volume'] for r in rows[-4:])
        if q4<=0:raise ValueError('FLOW_VOLUME_ZERO')
        blocks=four_hour(rows)
        if len(blocks)<2:raise ValueError('FOUR_HOUR_HISTORY_MISSING')
        out.update(return_1h_pct=100*(rows[-1]['close']/rows[-2]['close']-1),
                   return_4h_pct=100*(rows[-1]['close']/rows[-5]['close']-1),
                   return_24h_pct=100*(rows[-1]['close']/rows[-25]['close']-1),
                   closed_4h_return_pct=100*(blocks[-1]['close']/blocks[-2]['close']-1),
                   closed_4h_ms=blocks[-1]['close_ms'],
                   flow_4h=2*sum(r['taker_buy_quote'] for r in rows[-4:])/q4-1,
                   quote_volume_24h=sum(r['quote_volume'] for r in rows[-24:]))
        low=min(r['low'] for r in rows[:-1]);high=max(r['high'] for r in rows[:-1])
        out['prior_7d_range_position']=(rows[-1]['close']-low)/(high-low) if high>low else None
        # Closed UTC days only, never a partially formed daily bar.
        days={}
        for b in rows:days.setdefault(b['open_ms']//(24*HOUR),[]).append(b)
        full=[d for d in days.values() if len(d)==24 and d[0]['open_ms']%(24*HOUR)==0]
        out['closed_day_return_pct']=100*(full[-1][-1]['close']/full[-1][0]['open']-1) if full else None
        out['closed_day_ms']=full[-1][-1]['close_ms'] if full else None
    except (ValueError,KeyError,TypeError) as exc:
        out['errors']['price']=str(exc);return out
    try:
        lookup={r['open_ms']:r for r in btc_rows if r['close_ms']<decision_ms}
        btc=[lookup.get(r['open_ms']) for r in rows]
        if any(r is None for r in btc):raise ValueError('BTC_UNALIGNED')
        check_bars(btc,HOUR,decision_ms)
        # Estimate beta before the signal's four-hour measurement window.
        x=[math.log(b['close']/a['close']) for a,b in zip(btc[:-5],btc[1:-4])]
        y=[math.log(b['close']/a['close']) for a,b in zip(rows[:-5],rows[1:-4])]
        if len(x)<CONFIG['beta_min_returns']:raise ValueError('BETA_HISTORY_MISSING')
        mx,my=statistics.mean(x),statistics.mean(y)
        variance=sum((v-mx)**2 for v in x)
        if variance<=1e-16:raise ValueError('BTC_VARIANCE_ZERO')
        beta=sum((a-mx)*(b-my) for a,b in zip(x,y))/variance
        residual=100*(math.log(rows[-1]['close']/rows[-5]['close'])
                      -beta*math.log(btc[-1]['close']/btc[-5]['close']))
        out.update(beta=beta,beta_training_end_ms=rows[-5]['close_ms'],
                   btc_residual_4h_log_pct=residual)
    except (ValueError,KeyError,TypeError) as exc:out['errors']['relative']=str(exc)
    return out


def evaluate(plan, price, derivatives):
    """Return all fixed variant decisions and reasons. Never fabricate confidence."""
    if plan.get('direction') not in ('LONG','SHORT'):raise ValueError('INVALID_DIRECTION')
    sign=1 if plan['direction']=='LONG' else -1
    missing=[];structure=[];participation=[];context=[]
    for key in ('return_1h_pct','closed_4h_return_pct','btc_residual_4h_log_pct'):
        value=price.get(key)
        if not finite(value):missing.append('MISSING_'+key.upper());structure.append('MISSING_'+key.upper())
        elif sign*value<=0:structure.append('CONFLICT_'+key.upper())
    flow=price.get('flow_4h')
    if not finite(flow):participation.append('MISSING_FLOW_4H')
    elif sign*flow<=0:participation.append('FLOW_CONFLICT')
    for h in (1,4):
        value=derivatives.get(f'oi_change_{h}h_pct')
        if not finite(value):participation.append(f'MISSING_OI_{h}H')
        elif value<=0:participation.append(f'OI_{h}H_NOT_BUILDING')
    rate=derivatives.get('funding_rate');interval=derivatives.get('funding_interval_hours')
    ratio=derivatives.get('account_ratio');risk=plan.get('risk_pct')
    carry=None;crowded=None
    if not all(finite(v) for v in (rate,interval,ratio,risk)) or interval<=0 or ratio<=0 or risk<=0:
        context.append('CARRY_OR_ACCOUNT_DATA_MISSING')
    else:
        paid_rate=max(0.,sign*rate)
        carry=100*paid_rate*CONFIG['carry_hours']/interval
        crowded=(ratio>CONFIG['crowded_long_accounts'] if sign==1 else ratio<CONFIG['crowded_short_accounts'])
        if crowded and paid_rate>0:context.append('PAID_FUNDING_AND_CROWDED_SIDE')
        if (carry+CONFIG['round_trip_cost_pct'])/risk>CONFIG['carry_max_r']:
            context.append('CARRY_AND_FEES_EXCEED_RISK_BUDGET')
    variants={}
    for name,reasons in [('BASE',[]),('STRUCTURE',structure),
                         ('PARTICIPATION',structure+participation),
                         ('CONTEXT',structure+participation+context)]:
        if plan.get('stage')!='READY':reasons=['BASE_NOT_READY']+reasons
        variants[name]=dict(pass_candidate=not reasons,reasons=reasons)
    return dict(version=VERSION,config_hash=CONFIG_HASH,direction=plan['direction'],
                variants=variants,paid_carry_72h_scenario_pct=carry,crowded_accounts=crowded,
                oi_state=derivatives.get('oi_state','UNKNOWN'),
                target_r=CONFIG['target_r'],actionable=False,
                actionability_reason='UNVALIDATED_RESEARCH_VARIANT',win_probability=None)


def assess(plan, rows, btc_rows, metrics, funding, decision_ms):
    price=price_features(rows,btc_rows,decision_ms)
    deriv=derivative_features(metrics,funding,decision_ms)
    result=evaluate(plan,price,deriv)
    result.update(price_evidence=price,derivative_evidence=deriv)
    return result
