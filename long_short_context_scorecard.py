"""Episode-level descriptive evaluation; never selects or authorizes trades.

One first EARLY observation per episode, immutable event-time context only.
Comparisons use identical endpoints. Costs are sensitivity proxies, not fills.
"""
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

VERSION = 'CONTEXT_SCORECARD_V1_2026_10_14'
COSTS_PCT = (0.0, 0.10, 0.20, 0.30)


def context_state(context, direction):
    sign = 1 if direction == 'LONG' else -1
    t = (context or {}).get('timeframes', {})
    if any(t.get(h, {}).get('status') != 'OK' for h in ('1h', '4h')):
        return 'UNKNOWN'
    slopes = [sign*t[h]['ema20_slope_pct'] for h in ('1h', '4h')]
    if all(s > 0 for s in slopes):
        return 'ALIGNED'
    if all(s < 0 for s in slopes):
        return 'COUNTER_TREND'
    return 'MIXED'


def context_line(context, direction):
    labels = {'ALIGNED':'1 ve 4 saatlik eğim yönü destekliyor',
              'COUNTER_TREND':'1 ve 4 saatlik eğime ters kısa hareket',
              'MIXED':'1 ve 4 saatlik yönler uyuşmuyor',
              'UNKNOWN':'üst zaman verisi eksik; yön teyit edilemiyor'}
    text = 'Büyük resim: '+labels[context_state(context, direction)]
    t = (context or {}).get('timeframes', {}).get('15m', {})
    if t.get('status') == 'OK':
        pos = t.get('range_position')
        if pos is not None:
            text += f" | Son 24 saat aralığındaki konum: %{pos*100:.0f} (0=dip, 100=tepe)"
        vol = t.get('quote_volume_recent4_over_previous4')
        if vol is not None:
            text += f" | Son 1 saat hacmi/önceki saat: {vol:.2f}×"
    return text


def summarize(rows):
    values=[r['return_pct'] for r in rows]
    raw=[r['return_pct']*(1 if r['direction']=='LONG' else -1) for r in rows]
    n=len(values)
    if not n:
        return {'n':0}
    wins=sum(v>0 for v in values)
    z=1.96; p=wins/n; den=1+z*z/n
    mid=(p+z*z/(2*n))/den
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return {'n':n,'symbols':len({r['symbol'] for r in rows}),
        'right':wins,'wrong':sum(v<0 for v in values),'flat':sum(v==0 for v in values),
        'gross_mean_pct':sum(values)/n,
        'nominal_wilson_95_pct':[100*(mid-half),100*(mid+half)],
        'always_long_mean_pct':sum(raw)/n,'always_short_mean_pct':-sum(raw)/n,
        'fair_random_direction_expected_gross_pct':0.0,
        'cost_sensitivity':{str(cost):{'mean_endpoint_less_cost_pct':sum(values)/n-cost,
                                     'positive_count':sum(v>cost for v in values),
                                     'random_expected_less_cost_pct':-cost}
                            for cost in COSTS_PCT}}


def report(root):
    path=Path(root)/'long_short_breakout_observer.db'
    out={'version':VERSION,'research_only':True,'edge_proven':False,
         'sample_unit':'one first EARLY transition per episode; not card edits',
         'context_source':'observed_at_event only; no hindsight reconstruction',
         'limitations':['correlated episodes; nominal interval is not proof',
             'fixed-horizon raw returns; costs are sensitivity assumptions, not executed fills',
             'no target/stop PnL or funding simulation in this scorecard',
             'no thresholds fitted; report is descriptive, not out-of-sample validation'],
         'groups':{},'outcome_states':{},'issues':[]}
    if not path.is_file():
        out['issues'].append('MISSING_OBSERVER_DB');return out
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
            c.row_factory=sqlite3.Row
            records=c.execute('''SELECT e.id,e.symbol,e.direction,e.config_hash,
                t.features_json,t.delivery_status,o.horizon_seconds,o.state,o.return_pct
                FROM episodes e JOIN transitions t ON t.id=(
                    SELECT MIN(t2.id) FROM transitions t2 WHERE t2.episode_id=e.id AND t2.new_state='EARLY')
                LEFT JOIN outcomes o ON o.episode_id=e.id WHERE e.early_ms IS NOT NULL''').fetchall()
        buckets=defaultdict(list);states=Counter()
        for r in records:
            states[str(r['horizon_seconds'])+':'+str(r['state'])]+=1
            if r['state']!='RESOLVED' or r['return_pct'] is None or not math.isfinite(r['return_pct']):continue
            context=json.loads(r['features_json']).get('move_context',{})
            phase=(context_state(context,r['direction']) if context.get('mode')=='observed_at_event' else 'UNKNOWN')
            row=dict(r)
            # Include undelivered control observations, but never mix them silently with alerts.
            cohort='DELIVERED' if r['delivery_status']=='ACK' else 'NOT_DELIVERED'
            for group in ('ALL',phase):
                buckets[f"{r['config_hash']}:{cohort}:{r['horizon_seconds']}:{group}"].append(row)
        out['groups']={k:summarize(v) for k,v in buckets.items()}
        out['outcome_states']=dict(states)
    except (sqlite3.Error,ValueError,TypeError) as exc:
        out['issues'].append(type(exc).__name__+':'+str(exc)[:120])
    return out


def emit(root):
    result=report(root)
    print('CONTEXT_EPISODE_SCORECARD '+json.dumps(result,ensure_ascii=False),flush=True)
    return result

if __name__=='__main__':
    emit(os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state')
