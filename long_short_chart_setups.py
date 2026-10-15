"""Two closed-candle hypotheses and explicit, cost-adjusted plan geometry.

No estimated success probabilities. The symmetric payoff floor is a declared
selection policy for a new cohort, not a fitted claim about trading returns.
"""
import math


def true_range(rows):
    values = [max(b[2]-b[3], abs(b[2]-a[4]), abs(b[3]-a[4]))
              for a, b in zip(rows, rows[1:])]
    return sum(values)/len(values) if values else 0.0


def setup(rows, direction, highs, lows, config):
    """Return observed trigger/structural invalidation, never a fitted stop."""
    sign = 1 if direction == 'LONG' else -1
    # Reflect shorts into upward coordinates so pattern rules are identical.
    r = rows if sign == 1 else [[x[0], -x[1], -x[3], -x[2], -x[4], *x[5:]] for x in rows]
    hh, ll = (highs, lows) if sign == 1 else (
        [(t, -v) for t, v in lows], [(t, -v) for t, v in highs])
    n = config['compression_bars']
    # Test an actual last-candle break first, then a still-forming box.
    for forming in (False, True):
        end = len(r) if forming else len(r)-1
        box = r[end-n:end]
        before = r[end-n-21:end-n]
        if len(box) != n or len(before) < 15:
            continue
        base = true_range(before)
        top, bottom = max(x[2] for x in box), min(x[3] for x in box)
        contraction = true_range(box)/base if base > 0 else math.inf
        compressed = (contraction <= config['compression_range_ratio'] and
                      top-bottom <= config['compression_width_atr']*base)
        crossed = r[-1][4] > top and r[-2][4] <= top
        approaching = r[-1][4] > r[-2][4] and r[-1][4] > r[-1][1]
        if compressed and ((not forming and crossed) or (forming and approaching)):
            return dict(model='COMPRESSION_BREAKOUT', trigger=sign*top,
                stop=sign*(bottom-config['stop_buffer_atr']*base),
                model_anchor_ms=int(box[0][0]), model_ready=crossed and not forming,
                confirmation_ms=int(r[-1][6]) if crossed and not forming else None,
                pattern_evidence=dict(box_start_ms=int(box[0][0]), box_end_ms=int(box[-1][6]),
                    box_high=max(x[2] for x in rows[end-n:end]),
                    box_low=min(x[3] for x in rows[end-n:end]), contraction_ratio=contraction))
    # A pullback must follow a confirmed swing extreme, retain a higher low,
    # contain two counter-direction closes, and turn from its actual trough.
    recent = [x for x in hh if x[0] >= r[-config['pullback_lookback_bars']][0]]
    if not recent:
        return None
    peak_ms, peak = recent[-1]
    prior_lows = [x for x in ll if x[0] < peak_ms]
    if not prior_lows:
        return None
    peak_i = next(i for i, x in enumerate(r) if x[0] == peak_ms)
    after = r[peak_i+1:]
    if len(after) < 3:
        return None
    trough_i = peak_i+1+min(range(len(after)), key=lambda i: after[i][3])
    trough = r[trough_i]
    age = len(r)-1-trough_i
    declines = sum(r[i][4] < r[i-1][4] for i in range(peak_i+1, trough_i+1))
    base = true_range(r[max(0, peak_i-15):peak_i+1])
    if (not 1 <= age <= config['pullback_turn_max_bars'] or declines < 2 or base <= 0 or
            trough[3] <= prior_lows[-1][1] or
            peak-trough[3] < config['pullback_min_atr']*base or
            r[-1][4] <= r[-2][4] or r[-1][4] <= r[-1][1]):
        return None
    trigger = trough[2]
    # A prior closed break is an old signal, not a fresh trigger this candle.
    crossed = r[-1][4] > trigger and r[-2][4] <= trigger
    if r[-1][4] > trigger and not crossed:
        return None
    return dict(model='PULLBACK_CONTINUATION', trigger=sign*trigger,
        stop=sign*(trough[3]-config['stop_buffer_atr']*base),
        model_anchor_ms=int(trough[0]), model_ready=crossed,
        confirmation_ms=int(r[-1][6]) if crossed else None,
        pattern_evidence=dict(peak_ms=peak_ms, trough_ms=int(trough[0]),
            prior_structural_level=sign*prior_lows[-1][1],
            counter_direction_closes=declines, turn_age_bars=age))


def geometry(direction, entry, stop, obstacle, cost_pct):
    """Percentage of notional; fees/slippage are an assumption, not a fill."""
    if obstacle is None or not all(isinstance(x, (int, float)) and
            math.isfinite(x) and x > 0 for x in (entry, stop, obstacle)):
        return dict(valid=False, reason='FIRST_OBSTACLE_UNKNOWN')
    sign = 1 if direction == 'LONG' else -1
    gain = sign*(obstacle-entry)/entry*100
    risk = sign*(entry-stop)/entry*100
    net_gain, net_loss = gain-cost_pct, risk+cost_pct
    valid = gain > 0 and risk > 0 and net_gain > 0
    return dict(valid=valid, reason='OK' if valid else 'NO_NET_ROOM',
        reference_entry=entry, gross_room_pct=gain, invalidation_pct=risk,
        cost_assumption_pct=cost_pct, net_room_pct=net_gain,
        loss_with_cost_pct=net_loss,
        net_reward_risk=net_gain/net_loss if net_loss > 0 else None,
        break_even_win_rate_pct=100*net_loss/(gain+risk) if gain+risk > 0 else None)


def quality_limit(direction, stop, obstacle, cost_pct, min_net_r):
    sign = 1 if direction == 'LONG' else -1
    return (obstacle+min_net_r*stop)/((1+min_net_r)*(1+sign*cost_pct/100))
