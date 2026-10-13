"""Repair history using the private relay of official Binance USD-M REST.

Independent of the live WebSocket; no orders, Telegram or signal-rule changes.
Only closed, fully validated bars are inserted via the existing idempotent writer.
"""
import json
import os
import signal
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

from long_short_futures_kline_shadow import (
    INTERVALS, PERIOD_MS, REST_SOURCE, initialize, log, parse_closed_kline,
    requested_symbols, upsert_rows,
)

ENDPOINT = 'https://fapi.binance.com/fapi/v1/klines'


def validated_rows(payload, symbol, interval, now_ms=None):
    now_ms = int(time.time()*1000) if now_ms is None else now_ms
    if (payload.get('source') != REST_SOURCE or payload.get('endpoint') != ENDPOINT
            or payload.get('symbol') != symbol or payload.get('interval') != interval
            or not isinstance(payload.get('bars'), list)):
        raise ValueError('unverified Futures history response')
    period = PERIOD_MS[interval]
    rows = []
    seen = set()
    for b in payload['bars']:
        if not isinstance(b, list) or len(b) < 11:
            raise ValueError('incomplete Futures candle')
        start, end = int(b[0]), int(b[6])
        if end >= now_ms-250:  # never import the forming candle
            continue
        if start % period or end != start+period-1 or start in seen:
            raise ValueError('invalid Futures candle timestamps')
        row = parse_closed_kline({'e':'kline','s':symbol,'k':{
            's':symbol,'i':interval,'x':True,'t':start,'T':end,
            'o':b[1],'h':b[2],'l':b[3],'c':b[4],'v':b[5],
            'q':b[7],'n':b[8],'Q':b[10]}})
        if row is None:
            raise ValueError('invalid Futures candle fields')
        rows.append((*row[:9],REST_SOURCE,now_ms,*row[11:]))
        seen.add(start)
    if rows != sorted(rows,key=lambda r:r[2]):
        raise ValueError('unordered Futures candles')
    return rows


def repair(db, base_url, symbol, interval):
    # A candle forming when the request starts may close while the response
    # travels; its fetched contents must still be rejected as forming.
    request_started_ms = int(time.time()*1000)
    query = urlencode({'symbol':symbol,'interval':interval})
    with urlopen(base_url+'/klines?'+query,timeout=18) as response:
        payload = json.loads(response.read(1000000))
    rows = validated_rows(payload,symbol,interval,now_ms=request_started_ms)
    with initialize(db) as conn:
        result = upsert_rows(conn,rows)
    conn.close()
    log('FUTURES_HISTORY_REPAIR',dict(symbol=symbol,interval=interval,closed_bars=len(rows),**result))
    return result


def main():
    base = os.getenv('LS_FUTURES_HISTORY_RELAY_URL','').rstrip('/')
    url = urlsplit(base)
    if (url.scheme != 'http' or not (url.hostname or '').endswith('.railway.internal')
            or url.username or url.password or url.path or url.query or url.fragment):
        raise ValueError('history relay must be an explicit private Railway URL')
    root = os.getenv('LS_STATE_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '.long-short-state'
    db = os.path.join(root,'binance_futures_kline_shadow.db')
    analyst = os.getenv('LS_ANALYST_DB') or os.path.join(root,'long_short_analyst.db')
    os.makedirs(root,exist_ok=True)
    stop = threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:stop.set())
    log('FUTURES_HISTORY_WORKER_STARTED',{'source':REST_SOURCE})
    while not stop.is_set():
        symbols = requested_symbols(analyst)[:90]
        failed = False
        # Fix the admission data first, then higher-timeframe context.
        for interval in INTERVALS:
            for symbol in symbols:
                if stop.is_set(): return 0
                try:
                    if os.getenv('LS_NATIVE_CHAIN_V2','0')=='1':
                        from long_short_data_health import interval_status
                        from long_short_futures_kline_shadow import connect
                        c=connect(db,timeout=5)
                        try:status=interval_status(c,symbol,interval,int(time.time()*1000),
                            required=None)
                        finally:c.close()
                        if status['ready']:continue
                    repair(db,base,symbol,interval)
                except Exception as exc:
                    failed = True
                    log('FUTURES_HISTORY_UNAVAILABLE',{'symbol':symbol,'interval':interval,
                        'error':type(exc).__name__,'detail':str(exc)[:100]})
                    # Stop the pass on any failed source, do not hammer it.
                    break
                stop.wait(0.4)
            else:
                continue
            break
        delay = 60 if failed else (15 if os.getenv('LS_NATIVE_CHAIN_V2','0')=='1' else 900)
        log('FUTURES_HISTORY_PASS_FINISHED',{'symbols':len(symbols),'retry_seconds':delay})
        stop.wait(delay)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
