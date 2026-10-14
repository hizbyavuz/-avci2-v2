"""Causal movement context, observational only; never grants or vetoes entry.

Windows are fixed measurement horizons, not fitted signal thresholds. Historical
replay uses retained candles, which may have been backfilled after the event.
"""
import math
import sqlite3
from pathlib import Path

VERSION = 'MOVE_CONTEXT_V1_2026_10_14'
WINDOWS = {'15m': (900000, 96), '1h': (3600000, 48), '4h': (14400000, 42)}
SOURCES = {'BINANCE_UM_FUTURES_WEBSOCKET', 'BINANCE_UM_FUTURES_REST',
           'BINANCE_UM_FUTURES_ARCHIVE'}


def _ema(values, period=20):
    value = values[0]
    for x in values[1:]:
        value += 2 / (period + 1) * (x - value)
    return value


def measure(rows, price, direction):
    sign = 1 if direction == 'LONG' else -1
    highs, lows, closes = ([r[k] for r in rows] for k in ('high', 'low', 'close'))
    high, low = max(highs), min(lows)
    tr = [max(b['high']-b['low'], abs(b['high']-a['close']),
              abs(b['low']-a['close'])) for a, b in zip(rows, rows[1:])]
    atr = sum(tr[-14:]) / len(tr[-14:])
    ema = _ema(closes)
    recent, previous = rows[-4:], rows[-8:-4]
    rq, pq = sum(r['quote_volume'] for r in recent), sum(r['quote_volume'] for r in previous)
    buy = sum(r['taker_buy_quote'] for r in recent) / rq if rq else None
    prev_buy = sum(r['taker_buy_quote'] for r in previous) / pq if pq else None
    supports, resistances = [], []
    for i in range(2, len(rows)-2):
        if lows[i] == min(lows[i-2:i+3]) and lows[i] < price:
            supports.append(lows[i])
        if highs[i] == max(highs[i-2:i+3]) and highs[i] > price:
            resistances.append(highs[i])
    support = max(supports) if supports else None
    resistance = min(resistances) if resistances else None
    obstacle = resistance if sign == 1 else support
    return {
        'status': 'OK', 'bars': len(rows), 'last_close_ms': rows[-1]['close_ms'],
        'range_low': low, 'range_high': high,
        # May exceed [0,1] when the live price has broken the historical range.
        'range_position': (price-low)/(high-low) if high > low else None,
        'return_before_event_pct': (price/rows[0]['open']-1)*100,
        'directional_move_before_event_pct': sign*(price/rows[0]['open']-1)*100,
        'ema20_slope_pct': (ema/_ema(closes[:-1])-1)*100,
        'directional_ema_distance_atr': sign*(price-ema)/atr if atr else None,
        'quote_volume_recent4_over_previous4': rq/pq if pq else None,
        'taker_buy_share_recent4': buy,
        'directional_flow_change': sign*(buy-prev_buy) if buy is not None and prev_buy is not None else None,
        'nearest_confirmed_pivot_support': support,
        'nearest_confirmed_pivot_resistance': resistance,
        'directional_pivot_room_pct': sign*(obstacle/price-1)*100 if obstacle else None,
        'obstacle_status': 'KNOWN_PIVOT' if obstacle else 'UNKNOWN',
    }


def snapshot(candle_db, symbol, direction, price, event_ms, mode='historical_reconstruction'):
    result = {'version': VERSION, 'observation_only': True, 'mode': mode,
              'event_ms': event_ms, 'timeframes': {}}
    if direction not in ('LONG', 'SHORT') or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        result['status'] = 'INVALID_REFERENCE'
        return result
    if not Path(candle_db).is_file():
        result['status'] = 'MISSING_CANDLE_DB'
        return result
    try:
        with sqlite3.connect(Path(candle_db).resolve().as_uri()+'?mode=ro', uri=True, timeout=2) as c:
            c.row_factory = sqlite3.Row
            for interval, (step, count) in WINDOWS.items():
                rows = list(reversed(c.execute('''SELECT open_ms,close_ms,open,high,low,close,
                    quote_volume,taker_buy_quote,source FROM futures_closed_klines
                    WHERE symbol=? AND interval=? AND close_ms<? ORDER BY open_ms DESC LIMIT ?''',
                    (symbol, interval, event_ms, count)).fetchall()))
                reason = None
                if len(rows) != count:
                    reason = 'MISSING_HISTORY'
                elif not 0 < event_ms-rows[-1]['close_ms'] <= step:
                    reason = 'STALE_HISTORY'
                elif any(b['open_ms']-a['open_ms'] != step for a,b in zip(rows, rows[1:])):
                    reason = 'GAPPED_HISTORY'
                elif any(r['source'] not in SOURCES or r['open_ms'] % step or
                         r['close_ms'] != r['open_ms']+step-1 or
                         any(r[k] is None or not math.isfinite(r[k]) for k in
                             ('open','high','low','close','quote_volume','taker_buy_quote')) or
                         not 0 < r['low'] <= min(r['open'],r['close']) <= max(r['open'],r['close']) <= r['high'] or
                         not 0 <= r['taker_buy_quote'] <= r['quote_volume'] for r in rows):
                    reason = 'UNVERIFIED_HISTORY'
                result['timeframes'][interval] = {'status': reason} if reason else measure(rows, price, direction)
        result['status'] = 'OK' if all(x['status']=='OK' for x in result['timeframes'].values()) else 'INCOMPLETE'
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        result['status'] = 'DATA_ERROR'
        result['error_type'] = type(exc).__name__
    return result
