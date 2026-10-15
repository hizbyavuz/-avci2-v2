"""Read-only 15/30 minute movement audit of the original chart V1 deliveries.

No strategy imports, database writes, network calls or Telegram publishing.
Minute closes at/before the horizon approximate the requested second; extrema
exclude the partially elapsed delivery minute. These are not executed returns.
"""
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

VERSION = 'CHART_FIRST_MULTIFRAME_V1_2026_10_15'
START = int(datetime(2026, 10, 14, 21, tzinfo=timezone.utc).timestamp()*1000)
END = START + 86400000
VALID = {'BINANCE_UM_FUTURES_WEBSOCKET', 'BINANCE_UM_FUTURES_REST', 'BINANCE_UM_FUTURES_ARCHIVE'}


def measure(price, stamp, side, rows, minutes):
    if not isinstance(price, (float, int)) or not math.isfinite(price) or price <= 0:
        return {'state': 'MISSING_ACK_PRICE'}
    target = stamp + minutes*60000
    start = (stamp//60000+1)*60000
    # Last fully closed minute no later than the requested second.
    end = ((target+1)//60000-1)*60000
    times = list(range(start, end+1, 60000))
    mapping = {int(r[0]): r for r in rows}
    missing = [t for t in times if t not in mapping]
    if missing:
        return {'state': 'MISSING_NATIVE_PATH', 'missing_minutes': len(missing)}
    path = [mapping[t] for t in times]
    if not path:
        return {'state': 'MISSING_NATIVE_PATH'}
    for t, close_ms, high, low, close, source in path:
        if (source not in VALID or close_ms != t+59999 or
                not all(isinstance(v, (float, int)) and math.isfinite(v) and v > 0
                        for v in (high, low, close)) or not low <= close <= high):
            return {'state': 'INVALID_NATIVE_PATH'}
    sign = 1 if side == 'LONG' else -1
    change = (path[-1][4]/price-1)*100
    up = max(0, (max(r[2] for r in path)/price-1)*100)
    down = max(0, (1-min(r[3] for r in path)/price)*100)
    return dict(state='MEASURED', endpoint_ms=path[-1][1],
                effective_minutes=round((path[-1][1]-stamp)/60000, 4),
                endpoint_price=path[-1][4], price_change_pct=change,
                directional_change_pct=sign*change,
                favorable_excursion_pct=up if sign == 1 else down,
                adverse_excursion_pct=down if sign == 1 else up)


def build(root):
    def readonly(name):
        return sqlite3.connect((Path(root)/name).resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    a, k = readonly('chart_first_v1.db'), readonly('binance_futures_kline_shadow.db')
    results = []
    try:
        records = a.execute('''SELECT id, observed_ms, symbol, direction, stage, price, payload
            FROM assessments WHERE observed_ms>=? AND observed_ms<?
            AND reason='TELEGRAM_ACK' AND stage IN ('DELIVERED_WATCH','DELIVERED_READY')
            ORDER BY observed_ms,id''', (START, END))
        for aid, stamp, symbol, side, stage, price, payload in records:
            p = json.loads(payload)
            if p.get('version') != VERSION:
                continue
            rows = k.execute('''SELECT open_ms,close_ms,high,low,close,source
                FROM futures_closed_klines WHERE symbol=? AND interval='1m'
                AND open_ms>=? AND open_ms<=? ORDER BY open_ms''',
                (symbol, stamp//60000*60000, stamp+30*60000)).fetchall()
            results.append(dict(assessment_id=aid, episode_id=p.get('episode_id'),
                strategy_id=p.get('strategy_id'), symbol=symbol, direction=side,
                stage=stage, observed_ms=stamp, price=price, scenario=p.get('scenario'),
                trigger=p.get('trigger'), stop=p.get('stop'), obstacle=p.get('obstacle'),
                results={str(h): measure(price,stamp,side,rows,h) for h in (15,30)}))
    finally:
        a.close()
        k.close()
    groups = []
    grouped = defaultdict(list)
    for r in results:
        for horizon, m in r['results'].items():
            grouped[(r['stage'],horizon)].append(m)
    for (stage, horizon), items in sorted(grouped.items()):
        measured = [x for x in items if x['state']=='MEASURED']
        groups.append(dict(stage=stage, minutes=int(horizon), total=len(items),
            measured=len(measured), states=dict(Counter(x['state'] for x in items)),
            direction_positive=sum(x['directional_change_pct']>0 for x in measured),
            direction_negative=sum(x['directional_change_pct']<0 for x in measured),
            unchanged=sum(x['directional_change_pct']==0 for x in measured),
            above_assumed_030_cost=sum(x['directional_change_pct']>.30 for x in measured),
            mean_directional_change_pct=(sum(x['directional_change_pct'] for x in measured)/len(measured)
                                         if measured else None)))
    return dict(version=VERSION, start_ms=START, end_ms=END,
                scope='ACK_PRICE_FORWARD_MOVEMENT_NOT_EXECUTED_PNL',
                timing='LAST_MINUTE_CLOSE_AT_OR_BEFORE_HORIZON',
                extrema='PARTIAL_DELIVERY_MINUTE_EXCLUDED',
                observations=len(results), unique_symbols=len({r['symbol'] for r in results}),
                groups=groups, rows=results)


if __name__ == '__main__':
    root = os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state'
    try:
        report = build(root)
        for row in report.pop('rows'):
            print('CHART_V1_DELIVERY_AUDIT_ROW '+json.dumps(row), flush=True)
        print('CHART_V1_DELIVERY_AUDIT_SUMMARY '+json.dumps(report), flush=True)
    except Exception as exc:
        print('CHART_V1_DELIVERY_AUDIT_UNAVAILABLE '+type(exc).__name__, flush=True)
