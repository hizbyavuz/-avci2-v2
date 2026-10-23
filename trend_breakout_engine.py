"""Trend Kırılım Motoru v1. Pure, causal signals; no network, orders or legacy imports.

All timestamps are integer UTC milliseconds. close_ms is the *exclusive* close
boundary (Binance's inclusive close_time + 1). A bar is known when close_ms <= t.
EMA50 starts at the mean of its first 50 closes. ATR14 uses Wilder smoothing.
"""
from dataclasses import dataclass, asdict
import bisect
import hashlib
import json
import math
from collections import deque

HOUR = 3_600_000
DAY = 24 * HOUR
MINUTE = 60_000
RULES = dict(version=1, ema=50, slope=5, channel=120, volume=20,
             volume_multiple=1.5, atr=14, stop_atr=1., max_risk_atr=2.5,
             target_r=2., obstacle=480, timeout=168, cost_pct=.30,
             fallback_funding_pct_8h=.01, total_limit=5, direction_limit=3,
             universe_size=50, universe_days=30, minimum_age_days=60)
RULES_HASH = hashlib.sha256(json.dumps(RULES, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Candle:
    open_ms: int
    close_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float


def _ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    current = sum(values[:period]) / period
    out[period-1] = current
    for i in range(period, len(values)):
        current += (values[i] - current) * 2 / (period+1)
        out[i] = current
    return out


def _atr(bars, period=14):
    out = [None] * len(bars)
    tr = [max(b.high-b.low, abs(b.high-bars[i-1].close),
              abs(b.low-bars[i-1].close)) if i else b.high-b.low
          for i, b in enumerate(bars)]
    if len(bars) >= period:
        current = sum(tr[:period]) / period
        out[period-1] = current
        for i in range(period, len(bars)):
            current = (current*(period-1) + tr[i])/period
            out[i] = current
    return out


def _rolling_prior(values, window, maximum):
    """O(n) extrema of preceding window, excluding current row."""
    out, q = [], deque()
    for i, value in enumerate(values):
        while q and q[0] < i-window:
            q.popleft()
        out.append(values[q[0]] if i >= window and q else None)
        while q and ((values[q[-1]] <= value) if maximum else (values[q[-1]] >= value)):
            q.pop()
        q.append(i)
    return out


class Series:
    """Causal precomputation; appending future rows cannot change earlier values."""
    def __init__(self, bars, interval):
        self.bars = tuple(bars)
        self.interval = interval
        previous = -1
        for b in self.bars:
            values = (b.open, b.high, b.low, b.close, b.volume, b.quote_volume)
            if (b.open_ms <= previous or b.close_ms-b.open_ms != interval or
                not all(math.isfinite(x) for x in values) or
                min(b.open, b.high, b.low, b.close) <= 0 or min(b.volume, b.quote_volume) < 0 or
                b.low > min(b.open, b.close) or b.high < max(b.open, b.close)):
                raise ValueError('INVALID_CANDLE')
            previous = b.open_ms
        self.closes = tuple(b.close_ms for b in self.bars)
        self.opens = tuple(b.open_ms for b in self.bars)
        self.ema = _ema([b.close for b in self.bars], 50)
        self.atr = _atr(self.bars)
        self.level_high = _rolling_prior([b.high for b in self.bars], 120, True)
        self.level_low = _rolling_prior([b.low for b in self.bars], 120, False)
        self.obstacle_high = _rolling_prior([b.high for b in self.bars], 480, True)
        self.obstacle_low = _rolling_prior([b.low for b in self.bars], 480, False)
        self.volsum, self.quotesum, self.gaps = [0.], [0.], [0]
        for i, b in enumerate(self.bars):
            self.volsum.append(self.volsum[-1] + b.volume)
            self.quotesum.append(self.quotesum[-1] + b.quote_volume)
            self.gaps.append(self.gaps[-1] + int(i > 0 and b.open_ms != self.bars[i-1].close_ms))

    def index(self, t):
        return bisect.bisect_right(self.closes, t)-1

    def contiguous(self, start, end):
        return start >= 0 and self.gaps[end+1] == self.gaps[start+1]


def evaluate(symbol, mumlar_1h, mumlar_4h, zaman):
    """Signal only. Entry must subsequently be supplied to make_plan().

    Backtest uses next open; live uses a fresh quote. No next candle is read here.
    Prepared Series and raw Candle sequences have identical semantics.
    """
    h = mumlar_1h if isinstance(mumlar_1h, Series) else Series(mumlar_1h, HOUR)
    q = mumlar_4h if isinstance(mumlar_4h, Series) else Series(mumlar_4h, 4*HOUR)
    if h.interval != HOUR or q.interval != 4*HOUR:
        raise ValueError('WRONG_TIMEFRAME')
    i, j = h.index(zaman), q.index(zaman)
    result = dict(symbol=symbol, time=zaman, status='NO_SIGNAL', rules_hash=RULES_HASH)
    if i < 480 or j < 54:
        return dict(result, reason='INSUFFICIENT_HISTORY')
    b = h.bars[i]
    if b.close_ms != zaman or q.bars[j].close_ms != (zaman // (4*HOUR))*(4*HOUR):
        return dict(result, reason='STALE_OR_UNCLOSED_DATA')
    # All indicator history must be gap-free; do not smooth across missing bars.
    if not h.contiguous(0, i) or not q.contiguous(0, j):
        return dict(result, reason='DATA_GAP')
    ema, prior = q.ema[j], q.ema[j-5]
    close4 = q.bars[j].close
    side = 'LONG' if close4 > ema > prior else 'SHORT' if close4 < ema < prior else None
    if side is None:
        return dict(result, reason='NO_TREND')
    sign = 1 if side == 'LONG' else -1
    level = h.level_high[i] if sign == 1 else h.level_low[i]
    mean = (h.volsum[i]-h.volsum[i-20])/20
    if sign*(b.close-level) <= 0:
        return dict(result, reason='NO_BREAKOUT')
    if mean <= 0 or b.volume <= 1.5*mean:
        return dict(result, reason='LOW_VOLUME')
    atr = h.atr[i]
    if not atr or atr <= 0:
        return dict(result, reason='INVALID_ATR')
    return dict(result, status='SIGNAL', reason='TREND_CHANNEL_VOLUME', side=side,
                signal_close=b.close, level=level, atr=atr, stop=level-sign*atr,
                band_low=h.level_low[i], band_high=h.level_high[i],
                obstacle=h.obstacle_high[i] if sign == 1 else h.obstacle_low[i],
                volume_ratio=b.volume/mean, quote_volume_24h=h.quotesum[i+1]-h.quotesum[i-23],
                trend_close=close4, trend_ema=ema, trend_ema_prior=prior,
                trend_close_ms=q.bars[j].close_ms, signal_open_ms=b.open_ms)


def make_plan(signal, entry, entry_ms, data_source='futures'):
    if signal.get('status') != 'SIGNAL':
        return dict(signal, status='REJECTED', reason='NO_SIGNAL')
    sign = 1 if signal['side'] == 'LONG' else -1
    risk = sign*(entry-signal['stop'])
    p = dict(signal, entry=float(entry), entry_ms=int(entry_ms), data_source=data_source)
    if not math.isfinite(entry) or entry <= 0 or risk <= 0:
        return dict(p, status='REJECTED', reason='ENTRY_BEYOND_STOP')
    p.update(risk=risk, risk_pct=100*risk/entry, target=entry+sign*2*risk)
    if risk > 2.5*signal['atr']:
        return dict(p, status='REJECTED', reason='LATE_BREAKOUT')
    if p['target'] <= 0:
        return dict(p, status='REJECTED', reason='INVALID_TARGET')
    if 0 < sign*(signal['obstacle']-entry) < 2*risk:
        return dict(p, status='REJECTED', reason='OBSTACLE_BEFORE_TARGET')
    return dict(p, status='PLAN', reason='RULES_MET')


def choose_plans(candidates, active, limited):
    """Causal priority: 24h quote volume then symbol. Active contains current positions only."""
    accepted, rejected = [], []
    current = list(active)
    for p in sorted(candidates, key=lambda x: (-x['quote_volume_24h'], x['symbol'])):
        if any(x['symbol'] == p['symbol'] for x in current):
            reason = 'SAME_SYMBOL_OPEN'
        elif limited and (len(current) >= 5 or sum(x['side'] == p['side'] for x in current) >= 3):
            reason = 'LİMİT NEDENİYLE ATLANDI'
        else:
            accepted.append(p); current.append(p); continue
        rejected.append(dict(p, status='SKIPPED', reason=reason))
    return accepted, rejected
