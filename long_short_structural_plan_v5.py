"""V5 bounded common-region breakout, compared with frozen V4.1.
All constituent zones share the original local anchor. Entry must clear the
entire common region, within the pre-existing nearby-level allowance. Independent
obstacles remain blocking; stop, costs and economic thresholds are unchanged.
"""
import hashlib
import json
import math

from long_short_structural_plan_v4 import rebuild as baseline_rebuild

VERSION = 'STRUCTURAL_PLAN_V5_COMMON_REGION_2026_10_15'
ENTRY_FRAMES = ('5m', '15m')


def rebuild(plan, gate, zones, cost_pct, min_net_r, required_room_pct):
    p, g = dict(plan), dict(gate)
    config = dict(version=VERSION, entry_frames=ENTRY_FRAMES, cost_pct=cost_pct,
                  min_net_r=min_net_r, required_room_pct=required_room_pct,
                  level_near_pct=float((gate.get('thresholds') or {}).get('level_near_pct', .8)))
    evidence = dict(config, legacy_plan=dict(plan), valid=False, edge_proven=False,
                    stop_unchanged=True,
                    config_hash=hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())
    # Keep the established persistence key: ledger/rejection consumers read the
    # explicit version inside it, not the historical name of the key.
    g['structural_plan_v3'] = evidence
    p.update(geometry_version=VERSION, triggered=False)
    g.update(room_pct=None,net_t1_r=None,next_opposing_zone=None)

    def reject(reason):
        evidence.update(reason=reason)
        p['target1']=0.0
        g.update(room_pct=None,net_t1_r=None)
        g.update(qualified_precheck=False, room_ok=False, rr_ok=False,
                 reasons=[reason])
        return p, g

    direction = p.get('direction')
    if direction not in ('LONG', 'SHORT'):
        return reject('INVALID_DIRECTION')
    try:
        trigger, stop = float(p['trigger_level']), float(p['invalidation'])
        width = float(p['retest_high']) - float(p['retest_low'])
        if not all(math.isfinite(x) for x in (trigger, stop, width, cost_pct, min_net_r, required_room_pct)):
            return reject('INVALID_NUMERIC_INPUT')
        if min(trigger, stop) <= 0 or min(width, cost_pct, min_net_r, required_room_pct) < 0:
            return reject('INVALID_NUMERIC_INPUT')
        for z in zones:
            lo, center, hi = (float(z[k]) for k in ('low', 'center', 'high'))
            if not all(math.isfinite(x) and x > 0 for x in (lo, center, hi)) or not lo <= center <= hi:
                return reject('INVALID_ZONE')
    except (KeyError, ValueError, TypeError):
        return reject('INVALID_NUMERIC_INPUT')
    long = direction == 'LONG'
    side = 'RESISTANCE' if long else 'SUPPORT'
    local = [z for z in zones if z.get('side') == side and z.get('timeframe') in ENTRY_FRAMES
             and abs(float(z['center']) / trigger - 1) * 100 <= config['level_near_pct']]
    if not local:
        return reject('NO_LOCAL_TRIGGER_ZONE')
    # Distance first, then shorter timeframe; a distant strong 4h level must
    # never displace the micro trigger. All local members share one fixed point;
    # overlap with a newly added member cannot pull another level into the band.
    anchor = min(local, key=lambda z: (abs(float(z['center']) - trigger),
                 ENTRY_FRAMES.index(z['timeframe']), -float(z.get('strength', 0))))
    center = float(anchor['center'])
    # Same fixed price anchor across frames; do not chain neighbouring zones.
    members = [z for z in zones if z.get('side') == side
               and float(z['low']) <= center <= float(z['high'])]
    confluence = {z['timeframe'] for z in members}
    band = dict(anchor, low=min(float(z['low']) for z in members),
                high=max(float(z['high']) for z in members),
                common_anchor=center, members=[dict(z) for z in members])
    level_ok = (int(anchor.get('touches', 0)) >= 2 or len(confluence) >= 2
                or float(anchor.get('strength', 0)) >= 2 or p.get('setup_type') == 'PULLBACK')
    entry = max(trigger, band['high']) if long else min(trigger, band['low'])
    low, high = (entry - width, entry) if long else (entry, entry + width)
    p.update(trigger_level=entry, retest_low=low, retest_high=high, target2=0.0)
    evidence.update(entry=entry, stop=stop, trigger_band=band,
                    entry_shift_pct=(entry / trigger - 1) * 100,
                    retest_width_before=width, retest_width_after=high-low,
                    local_confluence=len(confluence))
    g.update(trigger_level=entry, trigger_zone=band, level_ok=bool(level_ok))
    # Reuse the existing nearby-level limit, not a performance-fitted allowance.
    if abs(entry / trigger - 1) * 100 > config['level_near_pct']:
        return reject('COMMON_REGION_TOO_WIDE')
    if not (0 < stop < low <= entry if long else entry <= high < stop):
        return reject('INVALID_STOP_OR_RETEST_GEOMETRY')
    # A second pivot in the direction of travel is an obstacle even when its
    # band overlaps the trigger. Do not skip it to manufacture distant reward.
    member_ids = {id(z) for z in members}
    obstacles = [z for z in zones if z.get('side') == side and id(z) not in member_ids
                 and (float(z['high']) > entry if long else float(z['low']) < entry)]
    if not obstacles:
        return reject('NO_OBSERVED_TARGET')
    target_zone = min(obstacles, key=lambda z: float(z['low'])) if long else max(obstacles, key=lambda z: float(z['high']))
    target = float(target_zone['low'] if long else target_zone['high'])
    evidence.update(target=target, target_band=dict(target_zone))
    g['next_opposing_zone'] = dict(target_zone)
    if not (stop < entry < target if long else target < entry < stop):
        return reject('ENTRY_INSIDE_OPPOSING_ZONE')
    sign = 1 if long else -1
    reward = sign * (target / entry - 1) * 100
    risk = abs(stop / entry - 1) * 100
    net_r = (reward-cost_pct) / (risk+cost_pct)
    room_ok, rr_ok = reward >= required_room_pct, net_r >= min_net_r
    qualified = bool(level_ok and room_ok and rr_ok)
    evidence.update(valid=True, reason='OK' if qualified else 'INSUFFICIENT_GEOMETRY',
                    risk_pct=risk, reward_pct=reward, net_r=net_r,
                    break_even_win_rate_pct=100*(risk+cost_pct)/(risk+reward))
    p['target1'] = target
    relation = 'üstünde' if long else 'altında'
    p['instruction'] = f'5 dk mum {entry:.10g} {relation} kapanmalı; devam ve masraf kontrolleri tamamlanmalı.'
    g.update(room_pct=reward, room_ok=room_ok, net_t1_r=net_r, rr_ok=rr_ok,
             qualified_precheck=qualified,
             reasons=['V5: ortak bölgenin dışından giriş; hedef ilk ayrı karşıt seviye',
                      f'Masraf sonrası {net_r:.2f}R; kullanılabilir alan %{reward:.2f}'])
    return p, g


def from_candles(plan, gate, frames, price, zone_builder, cost, min_r, room):
    zones = [z for candles, label, weight in frames
             for z in zone_builder(candles, label, weight, price)]
    result, updated = rebuild(plan, gate, zones, cost, min_r, room)
    old_plan, old_gate = baseline_rebuild(plan, gate, zones, cost, min_r, room)
    old = old_gate.get('structural_plan_v3', {})
    new = updated['structural_plan_v3']
    updated['geometry_comparison'] = {
        'same_closed_candles': True, 'pnl_comparison': False,
        'v4': {k: old.get(k) for k in ('version', 'entry', 'stop', 'target', 'reason', 'net_r')},
        'v5': {k: new.get(k) for k in ('version', 'entry', 'stop', 'target', 'reason', 'net_r')},
        'v4_qualified': bool(old_gate.get('qualified_precheck')),
        'v5_qualified': bool(updated.get('qualified_precheck')),
    }
    return result, updated
