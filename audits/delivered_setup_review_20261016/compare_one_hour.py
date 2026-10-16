"""Exploratory, reproducible comparison; never selects or changes live rules.

Only signal-time fields are predictors. Endpoints and barrier paths are labels.
One-hour is a user-requested secondary horizon; the frozen primary is 180m.
No threshold search, fitted score, causal attribution, or out-of-sample claim.
"""
import json
import math
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).parent
TR = timezone(timedelta(hours=3))


def numeric(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def average(values):
    values = [x for x in values if numeric(x)]
    return st.mean(values) if values else None


def distribution(values):
    values = [x for x in values if numeric(x)]
    return dict(n=len(values), median=st.median(values) if values else None, mean=average(values))


def sign(s):
    return 1 if s['direction'] == 'LONG' else -1


def direction(e):
    x = e['outcome'].get('directional_return_pct')
    return 'UNKNOWN' if not numeric(x) else 'RIGHT' if x > 0 else 'WRONG' if x < 0 else 'FLAT'


def time_block(s):
    dt = datetime.fromtimestamp(s['observed_ms']/1000, TR)
    return f'{dt:%Y-%m-%d} {dt.hour//3*3:02d}:00'


def stats(rows):
    directional = [e for e in rows if direction(e) != 'UNKNOWN']
    right = sum(direction(e) == 'RIGHT' for e in directional)
    n = len(directional)
    p = right/n if n else None
    if n:
        z = 1.96
        center = (p+z*z/(2*n))/(1+z*z/n)
        radius = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
        interval = [100*(center-radius), 100*(center+radius)]
    else:
        interval = None
    net = [e for e in rows if e['state'] == 'RESOLVED' and numeric(e['outcome'].get('model_net_return_pct'))]
    gains = [e['outcome']['model_net_return_pct'] for e in net]
    ratios = [e['outcome']['model_net_return_pct']/e['signal']['geometry']['loss_with_cost_pct']
              for e in net if numeric(e['signal'].get('geometry', {}).get('loss_with_cost_pct'))
              and e['signal']['geometry']['loss_with_cost_pct'] > 0]
    pos = sum(max(0, x) for x in gains)
    loss = -sum(min(0, x) for x in gains)
    return dict(n=len(rows), coins=len({e['signal']['symbol'] for e in rows}),
        episodes=len({e['signal']['episode_id'] for e in rows}),
        states=dict(Counter(e['state'] for e in rows)),
        direction=dict(n=n, right=right, wrong=sum(direction(e)=='WRONG' for e in rows),
                       flat=sum(direction(e)=='FLAT' for e in rows), rate_pct=100*p if n else None,
                       descriptive_wilson_95_pct=interval),
        first_exit=dict(Counter(e['outcome'].get('model_exit', 'AMBIGUOUS_OR_UNKNOWN') for e in rows)),
        net=dict(n=len(net), positive=sum(x>0 for x in gains), negative=sum(x<0 for x in gains),
                 mean_pct=average(gains), median_pct=st.median(gains) if gains else None,
                 mean_r=average(ratios), average_positive_pct=average([x for x in gains if x>0]),
                 average_negative_pct=average([x for x in gains if x<0]),
                 profit_factor=pos/loss if loss else None))


def aligned(s, tf):
    d = s.get('context_directions', {}).get(tf)
    return 'UNKNOWN' if d is None else 'RANGE' if d == 'RANGE' else (
        'ALIGNED' if d == ('UP' if sign(s)==1 else 'DOWN') else 'OPPOSING')


def flow_aligned(s):
    f = s.get('hourly_flow', {})
    a, b = f.get('buy_share'), f.get('price_return_pct')
    if not numeric(a) or not numeric(b):
        return 'UNKNOWN'
    return 'ALIGNED' if sign(s)*(a-.5)>0 and sign(s)*b>0 else 'NOT_ALIGNED'


def sign_bucket(value):
    return 'UNKNOWN' if not numeric(value) else 'POSITIVE' if value>0 else 'NEGATIVE' if value<0 else 'ZERO'


def normal_bucket(value):
    return 'UNKNOWN' if not numeric(value) else 'NORMAL_OR_ABOVE' if value>=1 else 'BELOW_NORMAL'


def grouped(rows, fn):
    groups = defaultdict(list)
    for e in rows:
        groups[str(fn(e['signal']))].append(e)
    return {k:stats(g) for k,g in sorted(groups.items())}


def robustness(rows, pred):
    """Sensitivity, not independent validation: overlapping coin/time clusters."""
    def delta(subset):
        a = [e for e in subset if pred(e['signal'])]
        b = [e for e in subset if not pred(e['signal'])]
        if not a or not b:
            return None
        return stats(a)['direction']['rate_pct']-stats(b)['direction']['rate_pct']
    leave_coin = [delta([e for e in rows if e['signal']['symbol']!=symbol])
                  for symbol in sorted({e['signal']['symbol'] for e in rows})]
    leave_block = [delta([e for e in rows if time_block(e['signal'])!=block])
                   for block in sorted({time_block(e['signal']) for e in rows})]
    by_coin = defaultdict(list)
    for e in rows:
        if pred(e['signal']):
            by_coin[e['signal']['symbol']].append(e)
    return dict(direction_advantage_pp=delta(rows),
        leave_one_coin_out_advantage_range_pp=[min(leave_coin),max(leave_coin)],
        leave_one_time_block_out_advantage_range_pp=[min(leave_block),max(leave_block)],
        by_side={side:dict(group=stats([e for e in rows if e['signal']['direction']==side and pred(e['signal'])]),
                          rest=stats([e for e in rows if e['signal']['direction']==side and not pred(e['signal'])]))
                 for side in ['LONG','SHORT']},
        equal_coin_weight_group_direction_rate_pct=average([stats(g)['direction']['rate_pct'] for g in by_coin.values()]),
        equal_coin_weight_group_net_mean_pct=average([stats(g)['net']['mean_pct'] for g in by_coin.values()]))


def main():
    data = json.loads((HERE/'one_hour_input.json').read_text())
    rows = [e for e in data['events'] if e['horizon']==60 and e['signal']['stage']=='DELIVERED_READY']
    assert len({(e['assessment_id'],e['horizon']) for e in rows}) == len(rows)
    assert all(e['signal']['strategy_id']==data['strategy_id'] for e in rows)
    assert len({e['signal']['episode_id'] for e in rows}) == len(rows)
    watch = {s['episode_id']:s for s in data['signals'] if s['stage']=='DELIVERED_WATCH'}
    cutoff = int(datetime.fromisoformat(data['cutoff'].replace('Z','+00:00')).timestamp()*1000)
    ready = [s for s in data['signals'] if s['stage']=='DELIVERED_READY']
    observed = {e['signal']['episode_id'] for e in rows}
    missing = [s for s in ready if s['episode_id'] not in observed]
    features = {
        'side':lambda s:s['direction'], 'model':lambda s:s.get('model','UNKNOWN'),
        'scenario':lambda s:s.get('scenario','UNKNOWN'),
        'model_by_side':lambda s:s.get('model','UNKNOWN')+' / '+s['direction'],
        'volume_5m':lambda s:normal_bucket(s.get('volume_5m_multiple')),
        'volume_15m':lambda s:normal_bucket(s.get('volume_15m_multiple')),
        'hourly_flow_and_response':flow_aligned,
        'oi_change':lambda s:sign_bucket(s.get('support',{}).get('oi_change_1h')),
        'taker_alignment':lambda s:sign_bucket(sign(s)*(s['support']['taker_ratio']-1)) if numeric(s.get('support',{}).get('taker_ratio')) else 'UNKNOWN',
        'funding_in_signal_direction':lambda s:sign_bucket(sign(s)*s['support']['funding_pct']) if numeric(s.get('support',{}).get('funding_pct')) else 'UNKNOWN',
        'net_reward_risk':lambda s:'UNKNOWN' if not numeric(s.get('geometry',{}).get('net_reward_risk')) else 'AT_LEAST_ONE' if s['geometry']['net_reward_risk']>=1 else 'BELOW_ONE',
        'hourly_flow_acceleration':lambda s:sign_bucket(sign(s)*(s['hourly_flow']['recent_30m_buy_share']-s['hourly_flow']['previous_30m_buy_share'])) if all(numeric(s.get('hourly_flow',{}).get(k)) for k in ['recent_30m_buy_share','previous_30m_buy_share']) else 'UNKNOWN',
        'three_hour_time_block':time_block,
    }
    for tf in ['5m','15m','1h','4h','1d']:
        features['context_'+tf] = lambda s,tf=tf:aligned(s,tf)
    groups = {name:grouped(rows,fn) for name,fn in features.items()}
    comparisons = {}
    for label in ['RIGHT','WRONG','FLAT']:
        subset = [e for e in rows if direction(e)==label]
        vals = defaultdict(list)
        for e in subset:
            s = e['signal']; f=s.get('hourly_flow',{}); g=s.get('geometry',{})
            for field in ['volume_5m_multiple','volume_15m_multiple']:
                vals[field].append(s.get(field))
            for field in ['gross_room_pct','loss_with_cost_pct','net_reward_risk']:
                vals[field].append(g.get(field))
            for field in ['oi_change_1h','funding_pct','taker_ratio','long_short_ratio']:
                vals[field].append(s.get('support',{}).get(field))
            vals['signed_previous_hour_price_move_pct'].append(sign(s)*f['price_return_pct'] if numeric(f.get('price_return_pct')) else None)
            vals['signed_distance_from_trigger_pct'].append(sign(s)*(s['price']/s['trigger']-1)*100)
            if s['episode_id'] in watch:
                w=watch[s['episode_id']]
                vals['watch_to_ready_minutes'].append((s['observed_ms']-w['observed_ms'])/60000)
                if numeric(w.get('price')) and w['price']>0:
                    vals['move_from_watch_to_ready_pct'].append(sign(s)*(s['price']/w['price']-1)*100)
        comparisons[label] = dict(stats=stats(subset), signal_time_features={k:distribution(v) for k,v in vals.items()})
    examples = []
    netrows = [e for e in rows if numeric(e['outcome'].get('model_net_return_pct'))]
    chosen = [('largest_net_loss',e) for e in sorted(netrows,key=lambda e:e['outcome']['model_net_return_pct'])[:3]]
    chosen += [('target_first_then_wrong_endpoint',e) for e in sorted(rows,key=lambda e:e['signal']['observed_ms'])
               if direction(e)=='WRONG' and e['outcome'].get('model_exit')=='OBSTACLE'][:3]
    for reason,e in chosen:
        s=e['signal'];o=e['outcome']
        examples.append(dict(reason=reason,assessment_id=e['assessment_id'],
            time_tr=datetime.fromtimestamp(s['observed_ms']/1000,TR).isoformat(),
            **{k:s[k] for k in ['symbol','direction','model','price','stop','obstacle','volume_5m_multiple','volume_15m_multiple']},
            endpoint_direction=direction(e), first_exit=o.get('model_exit'),
            first_exit_minute=o.get('first_level_minute'), reference_net_pct=o.get('model_net_return_pct')))
    ready_ids = {e['assessment_id'] for e in rows}
    paired_horizons = {}
    for horizon in [15,30,180]:
        paired = [e for e in data['events'] if e['assessment_id'] in ready_ids and e['horizon']==horizon]
        available = {e['assessment_id'] for e in paired}
        paired_horizons[str(horizon)] = dict(other=stats(paired), same_messages_at_60m=stats([e for e in rows if e['assessment_id'] in available]))
    at15 = {e['assessment_id']:e for e in data['events'] if e['horizon']==15}
    transitions = Counter((direction(at15[e['assessment_id']]),direction(e)) for e in rows if e['assessment_id'] in at15)
    report = dict(cutoff=data['cutoff'],strategy_id=data['strategy_id'],horizon=60,
        methodology='Exploratory associations in the already observed sample; no independent validation or causality.',
        coverage=dict(delivered_ready=len(ready),matched=len(rows),
            absent_due=sum(s['observed_ms']+61*60000<=cutoff for s in missing),
            horizon_pending=sum(s['observed_ms']+61*60000>cutoff for s in missing)),
        total=stats(rows),group_comparisons=groups, endpoint_groups=comparisons,
        sensitivities=dict(compression=robustness(rows,lambda s:s.get('model')=='COMPRESSION_BREAKOUT'),
                           high_volume_15m=robustness(rows,lambda s:s.get('volume_15m_multiple',0)>=1)),
        paired_horizons=paired_horizons,
        direction_transitions_15m_to_60m=[dict(at_15m=a,at_60m=b,n=n) for (a,b),n in sorted(transitions.items())],
        examples=examples,
        limitations=[
            '125 resolved barrier models, 2 boundary-ambiguous; endpoint direction still observable in all 127.',
            'Two mature outcomes not exported; nine signals not yet mature at snapshot; neither counted as failures.',
            'One strategy, roughly 18 hours of signal times, 68 coins, repeated coins and shared market moves.',
            'Wilson intervals are descriptive and ignore correlation and multiple comparisons; not proof of statistical edge.',
            '0.30% assumed total cost and reference exits, not actual user fills, leverage, funding or account PnL.',
            'Cost-adjusted R uses recorded stop distance; it is a comparison metric, not a measured portfolio return.',
            'No archived BTC/sector response or pre-signal candle range-position series included in this export.',
            'No future chart data was used to generate signal-time features. Hypotheses arose after viewing outcomes.',
            'Directional endpoints include up to the next minute close; first-minute barrier ambiguity remains explicit.',
            'One-hour endpoint was requested by user; original primary horizon remains 180 minutes.',
        ])
    assert report['total']['direction']['right']+report['total']['direction']['wrong']+report['total']['direction']['flat']==len(rows)
    assert report['coverage']['matched']+report['coverage']['absent_due']+report['coverage']['horizon_pending']==len(ready)
    (HERE/'one_hour_comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(coverage=report['coverage'],total=report['total'],sensitivities=report['sensitivities'],examples=examples),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
