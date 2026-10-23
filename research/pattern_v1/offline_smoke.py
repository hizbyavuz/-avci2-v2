"""Operational replay only; no network, optimization or profitability estimate."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from long_short_patterns import assess, HOUR, CONFIG_HASH, VERSION
from research.swing_v1.replay import archive


def aggregate_four_hours(rows):
    groups = {}
    for b in rows:
        groups.setdefault(b['open_ms']//(4*HOUR)*(4*HOUR), []).append(b)
    out = []
    for start, bars in sorted(groups.items()):
        if [b['open_ms'] for b in bars] != [start+i*HOUR for i in range(4)]:
            continue
        out.append(dict(open_ms=start, close_ms=start+4*HOUR-1, open=bars[0]['open'],
            high=max(b['high'] for b in bars), low=min(b['low'] for b in bars), close=bars[-1]['close'],
            quote_volume=sum(b['quote_volume'] for b in bars), taker_buy_quote=sum(b['taker_buy_quote'] for b in bars)))
    return out


def run(cache):
    if VERSION != 'PATTERN_STRUCTURE_RESEARCH_V1_2026_10_23':
        raise RuntimeError('V1_REPLAY_REQUIRES_COMMIT_53b8262; current strategy uses 15m/30m. Do not overwrite V1 evidence.')
    counts, shapes, reasons, direction, samples, inputs = Counter(), Counter(), Counter(), Counter(), [], []
    for symbol in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT', 'DOGEUSDT', 'XRPUSDT'):
        meta, rows = archive(Path(cache), symbol, '2026-08', '1h', offline=True)
        inputs.append(meta)
        if not rows:
            counts['missing_cache'] += 1; continue
        four = aggregate_four_hours(rows)
        for i in range(120, len(rows)):
            now = rows[i]['close_ms']+1
            context = [b for b in four if b['close_ms'] < now][-220:]
            try:
                plans = assess(symbol, '1h', rows[max(0, i-219):i+1], context, now)
                counts['evaluated_coin_hours'] += 1
            except ValueError as exc:
                reasons[str(exc)] += 1; continue
            for p in plans:
                counts[p['stage']] += 1
                shapes[p['model']] += 1
                direction[p['direction']+':'+p['stage']] += 1
                reasons.update(p['reasons'])
                if p['stage'] == 'READY' and len(samples) < 4:
                    samples.append(p)
    return dict(version=VERSION, config_hash=CONFIG_HASH,
                purpose='OPERATIONAL_REPLAY_NOT_PERFORMANCE_VALIDATION',
                historical_universe_bias='Eight named current large-cap coins only; no all-market accuracy claim.',
                no_network=True, counts=dict(counts), shapes=dict(shapes), directions=dict(direction),
                rejections=dict(reasons), inputs=inputs, samples=samples)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run(args.cache)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('purpose', 'counts', 'shapes', 'directions', 'rejections')}))
