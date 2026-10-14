"""Versioned structural geometry, using only already closed confirmed pivots.

No score fitting. A level inside the same connected price band is not a target.
The old stop is retained. The first distinct obstacle is never skipped for R.
"""
import hashlib
import json
import math
import os

VERSION = 'STRUCTURAL_PLAN_V3_2026_10_15'


def enabled():
    return os.getenv('LS_STRUCTURAL_PLAN_V3', '0') == '1'


def connected_bands(zones):
    bands = []
    for z in sorted(zones, key=lambda x: float(x['low'])):
        lo, hi = float(z['low']), float(z['high'])
        if not all(math.isfinite(x) and x > 0 for x in (lo, hi)) or lo > hi:
            raise ValueError('invalid structural zone')
        if bands and lo <= bands[-1]['high']:
            bands[-1]['high'] = max(bands[-1]['high'], hi)
            bands[-1]['members'].append(dict(z))
        else:
            bands.append({'low': lo, 'high': hi, 'members': [dict(z)]})
    return bands


def rebuild(plan, gate, zones, cost_pct, min_net_r, required_room_pct):
    """Return a new plan + gate. No mutation of the frozen baseline arguments."""
    p, g = dict(plan), dict(gate)
    evidence = {'version': VERSION, 'legacy_plan': dict(plan),
                'legacy_qualified': bool(gate.get('qualified_precheck')),
                'cost_pct': cost_pct, 'min_net_r': min_net_r,
                'required_room_pct': required_room_pct, 'edge_proven': False}
    def reject(reason):
        evidence.update(valid=False, reason=reason)
        g.update(qualified_precheck=False, room_ok=False, rr_ok=False,
                 structural_plan_v3=evidence)
        p['geometry_version'] = VERSION
        return p, g
    direction = p.get('direction')
    if direction not in ('LONG', 'SHORT'):
        return reject('INVALID_DIRECTION')
    trigger, stop = float(p['trigger_level']), float(p['invalidation'])
    if not all(math.isfinite(x) and x > 0 for x in (trigger, stop)):
        return reject('INVALID_LEVELS')
    bands = connected_bands(zones)
    anchor = gate.get('trigger_zone')
    if not anchor:
        return reject('NO_CONFIRMED_TRIGGER_ZONE')
    band = next((b for b in bands if b['low'] <= float(anchor['high'])
                 and b['high'] >= float(anchor['low'])), None)
    if not band:
        return reject('TRIGGER_ZONE_NOT_IN_EVIDENCE')
    long = direction == 'LONG'
    entry = max(trigger, band['high']) if long else min(trigger, band['low'])
    obstacles = ([b for b in bands if b['low'] > entry] if long else
                 [b for b in reversed(bands) if b['high'] < entry])
    if not obstacles:
        return reject('NO_OBSERVED_TARGET')
    target_band = obstacles[0]
    target = target_band['low'] if long else target_band['high']
    if not (stop < entry < target if long else target < entry < stop):
        return reject('INVALID_GEOMETRY')
    sign = 1 if long else -1
    reward = sign * (target / entry - 1) * 100
    risk = abs(stop / entry - 1) * 100
    net_r = (reward - cost_pct) / (risk + cost_pct)
    level_ok = bool(g.get('level_ok') or p.get('setup_type') == 'PULLBACK')
    room_ok, rr_ok = reward >= required_room_pct, net_r >= min_net_r
    evidence.update(valid=True, reason='OK' if level_ok and room_ok and rr_ok else 'INSUFFICIENT_GEOMETRY',
                    entry=entry, stop=stop, target=target, risk_pct=risk,
                    reward_pct=reward, net_r=net_r,
                    break_even_win_rate_pct=100*(risk+cost_pct)/(risk+reward),
                    trigger_band=band, target_band=target_band,
                    stop_unchanged=(stop == float(plan['invalidation'])))
    evidence['config_hash'] = hashlib.sha256(json.dumps({
        'version': VERSION, 'cost_pct': cost_pct, 'min_net_r': min_net_r,
        'required_room_pct': required_room_pct}, sort_keys=True).encode()).hexdigest()
    p.update(trigger_level=entry, target1=target, target2=0.0,
             retest_low=min(float(p['retest_low']), entry) if long else entry,
             retest_high=entry if long else max(float(p['retest_high']), entry),
             geometry_version=VERSION, triggered=False)
    side = 'üstünde' if long else 'altında'
    p['instruction'] = f"5 dk mum {entry:.10g} {side} kapanmalı; ardından devam ve masraf kontrolleri tamamlanmalı."
    g.update(trigger_level=entry, trigger_zone={**anchor, 'low': band['low'], 'high': band['high']},
             next_opposing_zone=target_band, room_pct=reward, room_ok=room_ok,
             net_t1_r=net_r, rr_ok=rr_ok,
             qualified_precheck=level_ok and room_ok and rr_ok,
             structural_plan_v3=evidence,
             reasons=['V3: giriş kırılan bölgenin dış kenarı; hedef ilk ayrı fiyat bölgesi',
                      f'Masraf sonrası {net_r:.2f}R; kullanılabilir alan %{reward:.2f}'])
    return p, g


def from_candles(plan, gate, frames, price, zone_builder, cost, min_r, room):
    zones = []
    for k, label, weight in frames:
        zones.extend(zone_builder(k, label, weight, price))
    return rebuild(plan, gate, zones, cost, min_r, room)
