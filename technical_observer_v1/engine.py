"""Pure closed-candle definitions. Returned records NEVER authorize an order.

The caller retains the complete input prefix (or later an equivalent checkpoint).
Confirmed swings are appended, never replaced. Zones are point-in-time objects;
new swings or ATR changes can change a later zone snapshot, not an earlier one.
"""
import hashlib
import json
import math
from statistics import median

from .config import DEFAULTS, VERSION

PERIODS = {'15m': 900_000, '1h': 3_600_000, '4h': 14_400_000}


def _id(*values):
    raw = json.dumps(values, sort_keys=True, allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate(rows, tf, asof_ms):
    if not rows:
        return ['MISSING_HISTORY:' + tf]
    errors = []
    step = PERIODS[tf]
    for i, b in enumerate(rows):
        if not isinstance(b, dict):
            return ['INVALID_BAR:' + tf]
        t, end = b.get('open_ms'), b.get('close_ms')
        if type(t) is not int or type(end) is not int or t % step or end != t + step - 1:
            return ['INVALID_CLOCK:' + tf]
        received = b.get('received_ms', end)
        if type(received) is not int or end >= asof_ms or received > asof_ms:
            return ['OPEN_OR_UNAVAILABLE_CANDLE:' + tf]
        if i and t != rows[i - 1]['open_ms'] + step:
            return ['CANDLE_GAP:' + tf]
        if not all(_finite(b.get(k)) and b[k] > 0 for k in ('open', 'high', 'low', 'close')):
            return ['INVALID_PRICE:' + tf]
        if not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']:
            return ['INVALID_OHLC:' + tf]
        if not _finite(b.get('quote_volume')) or b['quote_volume'] < 0:
            return ['INVALID_QUOTE_VOLUME:' + tf]
    if asof_ms - rows[-1]['close_ms'] > step:
        errors.append('STALE_HISTORY:' + tf)
    return errors


def atr(rows, cfg=DEFAULTS):
    """Wilder ATR: initial mean of n true ranges, then (old*(n-1)+TR)/n."""
    n = cfg.atr_period
    result, tr = [], []
    for i, b in enumerate(rows):
        prev = rows[i - 1]['close'] if i else b['open']
        tr.append(max(b['high'] - b['low'], abs(b['high'] - prev), abs(b['low'] - prev)))
        result.append(None if i < n - 1 else sum(tr[:n]) / n if i == n - 1
                      else (result[-1] * (n - 1) + tr[-1]) / n)
    return result


def ema(rows, period):
    values = [b['close'] for b in rows]
    result = []
    for i, value in enumerate(values):
        result.append(None if i < period - 1 else sum(values[:period]) / period if i == period - 1
                      else result[-1] + 2 / (period + 1) * (value - result[-1]))
    return result


def swings(rows, tf, cfg=DEFAULTS):
    """Strict 3/3 extrema, filtered at confirmation; no same-side replacement."""
    av = atr(rows, cfg)
    accepted, rejected = [], []
    for i in range(cfg.swing_left, len(rows) - cfg.swing_right):
        window = rows[i - cfg.swing_left:i] + rows[i + 1:i + cfg.swing_right + 1]
        kinds = [kind for kind, field, sign in [('H', 'high', 1), ('L', 'low', -1)]
                 if all(sign * rows[i][field] > sign * b[field] for b in window)]
        known = rows[i + cfg.swing_right]['close_ms']
        if len(kinds) == 2:
            rejected.append(dict(open_ms=rows[i]['open_ms'], known_ms=known,
                                 reason='AMBIGUOUS_OUTSIDE_BAR'))
            continue
        for kind in kinds:
            field = 'high' if kind == 'H' else 'low'
            p = dict(id=_id(VERSION, cfg.fingerprint(), tf, rows[i]['open_ms'], kind),
                     timeframe=tf, kind=kind, price=rows[i][field], i=i,
                     open_ms=rows[i]['open_ms'], known_ms=known, atr=av[i])
            if av[i] is None or av[i] <= 0:
                rejected.append(dict(p, reason='ATR_NOT_READY'))
                continue
            opposite = next((q for q in reversed(accepted) if q['kind'] != kind), None)
            distance = abs(p['price'] - opposite['price']) / av[i] if opposite else None
            p.update(opposite_id=opposite['id'] if opposite else None, opposite_distance_atr=distance)
            if distance is not None and distance < cfg.swing_min_opposite_atr:
                rejected.append(dict(p, reason='BELOW_MINIMUM_SWING_DISTANCE'))
            else:
                accepted.append(p)
    return dict(confirmed=accepted, rejected=rejected)


def structure(points):
    highs = [p for p in points if p['kind'] == 'H'][-2:]
    lows = [p for p in points if p['kind'] == 'L'][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return dict(label='DOĞRULANMADI', reason='INSUFFICIENT_CONFIRMED_SWINGS', highs=highs, lows=lows)
    high_change = highs[-1]['price'] - highs[-2]['price']
    low_change = lows[-1]['price'] - lows[-2]['price']
    label = 'YÜKSELİŞ' if high_change > 0 and low_change > 0 else 'DÜŞÜŞ' if high_change < 0 and low_change < 0 else 'YATAY'
    return dict(label=label, reason='LAST_TWO_CONFIRMED_HIGHS_AND_LOWS', highs=highs, lows=lows,
                high_relation='HH' if high_change > 0 else 'LH' if high_change < 0 else 'EH',
                low_relation='HL' if low_change > 0 else 'LL' if low_change < 0 else 'EL')


def _role_and_strength(z, rows, av, cfg):
    role = 'RESISTANCE' if z['members'][-1]['kind'] == 'H' else 'SUPPORT'
    changes, touches = [], []
    touching = False
    active = None
    for i, b in enumerate(rows):
        if b['close_ms'] < z['known_ms']:
            continue
        intersects = b['low'] <= z['high'] and b['high'] >= z['low']
        if intersects and not touching:
            active = dict(start_ms=b['open_ms'], end_ms=b['close_ms'], end_i=i,
                          role=role, atr=av[i])
            touches.append(active)
        elif intersects:
            active.update(end_ms=b['close_ms'], end_i=i)
        touching = intersects
        new_role = 'SUPPORT' if b['close'] > z['high'] else 'RESISTANCE' if b['close'] < z['low'] else role
        if new_role != role:
            changes.append(dict(known_ms=b['close_ms'], from_role=role, to_role=new_role,
                                close=b['close'], reason='CLOSE_THROUGH_FAR_EDGE'))
            role = new_role
    reactions = []
    for touch in touches:
        end = touch['end_i']
        future = rows[end + 1:end + 1 + cfg.reaction_bars]
        ready = len(future) == cfg.reaction_bars and touch['atr'] is not None and touch['atr'] > 0
        if ready:
            move = (max(b['high'] for b in future) - z['high'] if touch['role'] == 'SUPPORT'
                    else z['low'] - min(b['low'] for b in future))
            reaction = max(0., move) / touch['atr']
            reactions.append(reaction)
            touch.update(reaction_atr=reaction, reaction_known_ms=future[-1]['close_ms'])
        else:
            touch.update(reaction_atr=None, reaction_known_ms=None)
        touch.pop('end_i')
    age = max(0, len(rows) - 1 - max(p['i'] for p in z['members']))
    recency = max(0., 1 - age / cfg.zone_lookback)
    mean_reaction = sum(reactions) / len(reactions) if reactions else 0.
    tf_weight = dict(cfg.zone_tf_weights)[z['timeframe']]
    strength = (cfg.strength_touch_weight * len(touches) + cfg.strength_reaction_weight * mean_reaction
                + tf_weight + cfg.strength_recency_weight * recency)
    return dict(role=role, role_changes=changes, touches=touches,
                strength=dict(total=strength, touch_count=len(touches), mean_reaction_atr=mean_reaction,
                              measured_reactions=len(reactions), timeframe_weight=tf_weight, recency=recency,
                              decision_effect='NONE'))


def regions(rows, tf, points, cfg=DEFAULTS):
    if not rows:
        return []
    av = atr(rows, cfg)
    if av[-1] is None or av[-1] <= 0:
        return []
    eligible = sorted((p for p in points if p['i'] >= len(rows) - cfg.zone_lookback),
                      key=lambda p: (p['price'], p['open_ms']))
    groups = []
    # Connected components of proximity: reflection symmetric, unlike a greedy
    # left-anchored maximum-span grouping. Chains can produce wider zones.
    for p in eligible:
        if groups and p['price'] - groups[-1][-1]['price'] <= cfg.zone_cluster_atr * av[-1]:
            groups[-1].append(p)
        else:
            groups.append([p])
    out = []
    for members in groups:
        members = sorted(members, key=lambda p: p['known_ms'])
        low, high = min(p['price'] for p in members), max(p['price'] for p in members)
        center = (low + high) / 2
        half = max(high - low, cfg.zone_min_width_atr * av[-1]) / 2
        z = dict(id=_id(VERSION, cfg.fingerprint(), tf, sorted(p['id'] for p in members)),
                 timeframe=tf, low=center - half, high=center + half, center=center,
                 raw_low=low, raw_high=high, atr=av[-1], members=members,
                 known_ms=max(p['known_ms'] for p in members), asof_ms=rows[-1]['close_ms'] + 1)
        z.update(_role_and_strength(z, rows, av, cfg))
        z['snapshot_id'] = _id(z)
        out.append(z)
    return out


def frame_context(rows, tf, cfg=DEFAULTS):
    found = swings(rows, tf, cfg)
    av = atr(rows, cfg)
    return dict(timeframe=tf, bars=len(rows), zone_window_complete=len(rows) >= cfg.zone_lookback,
                last_close_ms=rows[-1]['close_ms'] if rows else None,
                atr=av[-1] if av else None, swings=found,
                structure=structure(found['confirmed']),
                regions=regions(rows, tf, found['confirmed'], cfg))


def late_observation(rows, cfg=DEFAULTS):
    av = atr(rows, cfg)
    ev = ema(rows, cfg.late_ema_period)
    if not rows or av[-1] is None or av[-1] <= 0 or ev[-1] is None or len(rows) <= cfg.late_move_bars:
        return dict(label='DOĞRULANMADI', reasons=['LATE_HISTORY_NOT_READY'], decision_effect='NONE')
    distance = abs(rows[-1]['close'] - ev[-1]) / av[-1]
    move = abs(rows[-1]['close'] - rows[-1 - cfg.late_move_bars]['close']) / av[-1]
    reasons = []
    if distance > cfg.late_ema_distance_atr:
        reasons.append('EMA_DISTANCE_EXCEEDED')
    if move > cfg.late_move_atr:
        reasons.append('RECENT_NET_MOVE_EXCEEDED')
    return dict(label='GEÇ' if reasons else 'GEÇ_ETİKETİ_YOK', reasons=reasons,
                ema=ev[-1], ema_distance_atr=distance, recent_net_move_atr=move,
                decision_effect='NONE')


def stop_reference(direction, points, entry, current_atr, cfg=DEFAULTS):
    sign = 1 if direction == 'LONG' else -1
    kind = 'L' if sign == 1 else 'H'
    candidates = [p for p in points if p['kind'] == kind]
    if not candidates:
        return dict(price=None, anchor=None, labels=['NO_CONFIRMED_STOP_SWING'], decision_effect='NONE')
    anchor = candidates[-1]
    previous = candidates[-2] if len(candidates) > 1 else None
    price = anchor['price'] - sign * cfg.stop_buffer_atr * current_atr
    distance = sign * (entry - price) / current_atr
    labels = []
    relation = None
    if previous:
        difference = anchor['price'] - previous['price']
        relation = ('HL' if difference > 0 else 'LL' if difference < 0 else 'EL') if kind == 'L' else (
            'LH' if difference < 0 else 'HH' if difference > 0 else 'EH')
    if relation != ('HL' if sign == 1 else 'LH'):
        labels.append('NEAREST_SWING_NOT_CONFIRMED_HL' if sign == 1 else 'NEAREST_SWING_NOT_CONFIRMED_LH')
    if distance <= 0:
        labels.append('STOP_WRONG_SIDE')
    if price <= 0:
        labels.append('STOP_NONPOSITIVE')
    if distance > cfg.wide_stop_atr:
        labels.append('STOP_TOO_WIDE')
    return dict(price=price, anchor=anchor, prior_same_kind=previous, relation=relation,
                distance_atr=distance, labels=labels, decision_effect='NONE')


def target_reference(direction, broken, all_regions, entry, current_atr, cfg=DEFAULTS):
    sign = 1 if direction == 'LONG' else -1
    desired = 'RESISTANCE' if sign == 1 else 'SUPPORT'
    choices, rejected = [], []
    for z in all_regions:
        if z['role'] != desired:
            continue
        price = z['low'] if sign == 1 else z['high']
        distance = sign * (price - entry) / current_atr
        beyond = z['low'] > broken['high'] if sign == 1 else z['high'] < broken['low']
        reason = ('BROKEN_ZONE_ITSELF' if z['id'] == broken['id'] else
                  'NOT_ENTIRELY_BEYOND_BROKEN_ZONE' if not beyond else
                  'TARGET_CLOSER_THAN_MINIMUM_ATR' if distance < cfg.target_min_distance_atr else None)
        if reason:
            rejected.append(dict(region=z, price=price, distance_atr=distance, reason=reason))
        else:
            choices.append((distance, -dict(cfg.zone_tf_weights)[z['timeframe']], z['id'], z, price))
    chosen = min(choices, key=lambda item: item[:3]) if choices else None
    return dict(price=chosen[4] if chosen else None, region=chosen[3] if chosen else None,
                distance_atr=chosen[0] if chosen else None,
                reason='FIRST_QUALIFYING_OPPOSING_REGION' if chosen else 'NO_CONFIRMED_TARGET',
                rejected_regions=rejected, decision_effect='NONE')


def net_r(direction, entry, stop, target, cfg=DEFAULTS):
    if direction not in ('LONG', 'SHORT') or not _finite(entry) or entry <= 0:
        return dict(value=None, reason='INVALID_ENTRY_OR_DIRECTION', cost_pct=cfg.round_trip_cost_pct)
    sign = 1 if direction == 'LONG' else -1
    if stop is None or target is None:
        return dict(value=None, reason='MISSING_STOP_OR_TARGET', cost_pct=cfg.round_trip_cost_pct)
    if not all(_finite(value) and value > 0 for value in (stop, target)):
        return dict(value=None, reason='INVALID_STOP_OR_TARGET', cost_pct=cfg.round_trip_cost_pct)
    risk, reward = 100 * sign * (entry - stop) / entry, 100 * sign * (target - entry) / entry
    if risk <= 0 or reward <= 0:
        return dict(value=None, reason='INVALID_DIRECTIONAL_GEOMETRY', risk_pct=risk, reward_pct=reward,
                    cost_pct=cfg.round_trip_cost_pct)
    return dict(value=(reward - cfg.round_trip_cost_pct) / (risk + cfg.round_trip_cost_pct),
                reason='CALCULATED_NOT_VALIDATED', risk_pct=risk, reward_pct=reward,
                cost_pct=cfg.round_trip_cost_pct, funding_included=False)


def observe(symbol, frames, asof_ms, cfg=DEFAULTS, provenance=None):
    """Return JSON-compatible evidence only. No orders, publishing, files or DB."""
    out = dict(version=VERSION, config=cfg.payload(), config_hash=cfg.fingerprint(), symbol=symbol,
               asof_ms=asof_ms, mode='OBSERVATION_ONLY', decision_effect='NONE',
               source_verification=provenance or 'DOĞRULANMADI', issues=[], setups=[])
    if type(asof_ms) is not int or asof_ms <= 0:
        out.update(status='DATA_INVALID', issues=['INVALID_ASOF'])
        return out
    for tf in PERIODS:
        out['issues'].extend(_validate(frames.get(tf, []), tf, asof_ms))
    if out['issues']:
        out['status'] = 'DATA_INVALID'
        return out
    out['input_hash'] = _id(frames)
    out['contexts'] = {tf: frame_context(frames[tf], tf, cfg) for tf in PERIODS}
    rows = frames['15m']
    bar = rows[-1]
    # The defining/confirming candles precede the breakout candle. All inputs
    # must be available at observation time (validated above); this is NOT a
    # claim that the collector had received them exactly at candle-open time.
    before = {tf: [b for b in frames[tf] if b['close_ms'] < bar['open_ms']] for tf in PERIODS}
    prior = {tf: frame_context(before[tf], tf, cfg) for tf in PERIODS}
    out['decision_basis'] = dict(candle_cutoff_ms=bar['open_ms'], observed_ms=asof_ms, contexts=prior)
    av = out['contexts']['15m']['atr']
    out['late'] = late_observation(rows, cfg)
    if av is None or av <= 0 or len(rows) < cfg.volume_lookback + 1:
        out.update(status='INSUFFICIENT_HISTORY', issues=['SETUP_ATR_OR_VOLUME_NOT_READY'])
        return out
    out['status'] = 'OBSERVED'
    all_regions = [z for c in prior.values() for z in c['regions']]
    # The trigger candle is 15m; the broken region may be from any configured TF.
    for z in all_regions:
        for direction, sign, role in [('LONG', 1, 'RESISTANCE'), ('SHORT', -1, 'SUPPORT')]:
            if z['role'] != role:
                continue
            entry = z['high'] if sign == 1 else z['low']
            if not (sign * (bar['close'] - entry) > 0 and sign * (rows[-2]['close'] - entry) <= 0):
                continue
            body = abs(bar['close'] - bar['open'])
            span = bar['high'] - bar['low']
            body_fraction = body / span if span > 0 else 0.
            baseline = median(b['quote_volume'] for b in rows[-cfg.volume_lookback - 1:-1])
            multiple = bar['quote_volume'] / baseline if baseline > 0 else None
            weak = []
            if body_fraction < cfg.breakout_body_fraction:
                weak.append('BODY_TOO_SMALL')
            if multiple is None:
                weak.append('VOLUME_BASELINE_UNDEFINED')
            elif multiple < cfg.breakout_volume_multiple:
                weak.append('VOLUME_TOO_LOW')
            stop = stop_reference(direction, prior['15m']['swings']['confirmed'], entry, av, cfg)
            target = target_reference(direction, z, all_regions, entry, av, cfg)
            p = dict(id=_id(VERSION, cfg.fingerprint(), symbol, direction, z['snapshot_id'], bar['close_ms']),
                     direction=direction, timeframe='15m', broken_region_timeframe=z['timeframe'], asof_ms=asof_ms,
                     decision_effect='NONE', entry_reference=entry, entry_kind='BROKEN_ZONE_EDGE_NOT_FILL',
                     broken_region=z, breakout_bar=dict(bar), atr=av,
                     breakout=dict(label='ZAYIF_KIRILIM' if weak else 'KIRILIM_KOŞULLARI_SAĞLANDI',
                                   reasons=weak, body_fraction=body_fraction, volume_multiple=multiple,
                                   prior_volume_median=baseline, volume_field='quote_volume',
                                   body_direction_matches=sign * (bar['close'] - bar['open']) > 0),
                     stop=stop, target=target, net_r=net_r(direction, entry, stop['price'], target['price'], cfg),
                     late=out['late'], trend_labels={tf: c['structure']['label'] for tf, c in out['contexts'].items()},
                     named_pattern=None, research_only=True)
            out['setups'].append(p)
    out['reason'] = 'BREAKOUT_OBSERVATIONS' if out['setups'] else 'NO_NEW_15M_REGION_CROSS'
    return out
