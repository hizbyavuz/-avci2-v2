"""A 5m setup uses its latest intact, confirmed 5m opposite pivot as invalidation.
V5 remains the reference. Targets, economic floors and entry confirmations stay.
"""
import copy
import hashlib
import json
from long_short_structural_plan_v5 import from_candles as baseline

VERSION='STRUCTURAL_PLAN_V6_LOCAL_INVALIDATION_2026_10_15'

def local_invalidation(k,direction,price,plan):
    if direction not in ('LONG','SHORT'):return None
    long=direction=='LONG'
    levels=list(map(float,k['low' if long else 'high'][-48:]))
    limit=min(float(price),float(plan['retest_low'])) if long else max(float(price),float(plan['retest_high']))
    for i in range(len(levels)-3,1,-1):
        value=levels[i];neighbours=levels[i-2:i]+levels[i+1:i+3]
        pivot=all(value<=x for x in neighbours) if long else all(value>=x for x in neighbours)
        intact=all(x>=value for x in levels[i+1:]) if long else all(x<=value for x in levels[i+1:])
        if pivot and intact and (0<value<limit if long else value>limit):
            return dict(level=value,timeframe='5m',bars_ago=len(levels)-1-i,
                confirmation_bars=2,method='LATEST_INTACT_CONFIRMED_OPPOSITE_PIVOT')
    return None

def from_candles(plan,gate,frames,price,zone_builder,cost,min_r,room):
    old_plan,old_gate=baseline(plan,gate,frames,price,zone_builder,cost,min_r,room)
    p=copy.deepcopy(plan)
    k5=next((k for k,label,_ in frames if label=='5m'),None)
    invalidation=local_invalidation(k5,p.get('direction'),price,p) if k5 else None
    if invalidation:p['invalidation']=invalidation['level']
    result,updated=baseline(p,gate,frames,price,zone_builder,cost,min_r,room)
    evidence=updated['structural_plan_v3']
    evidence.update(version=VERSION,stop_unchanged=False,previous_stop=plan.get('invalidation'),
        invalidation_evidence=invalidation)
    evidence['config_hash']=hashlib.sha256(json.dumps(dict(version=VERSION,
        baseline_config_hash=evidence['config_hash'],stop_method='LATEST_INTACT_CONFIRMED_OPPOSITE_5M_PIVOT',
        lookback=48,confirmation_bars=2),sort_keys=True).encode()).hexdigest()
    result['geometry_version']=VERSION
    result['local_invalidation']=invalidation
    if not invalidation:
        evidence.update(valid=False,reason='NO_LOCAL_INVALIDATION',stop=None)
        updated.update(qualified_precheck=False,room_ok=False,rr_ok=False,room_pct=None,net_t1_r=None,
            reasons=['NO_LOCAL_INVALIDATION'])
        result.update(target1=0.0,invalidation=0.0)
    updated['geometry_comparison']={
        'same_closed_candles':True,'pnl_comparison':False,
        'v5':{k:old_gate['structural_plan_v3'].get(k) for k in ('version','entry','stop','target','reason','net_r')},
        'v6':{k:evidence.get(k) for k in ('version','entry','stop','target','reason','net_r')},
        'v5_qualified':bool(old_gate['qualified_precheck']),
        'v6_qualified':bool(updated['qualified_precheck'])}
    return result,updated
