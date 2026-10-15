"""Observe the entire episode price path, never revive a crossed locked level.

The first minute can include pre-episode extrema: ambiguous touches expire the
idea without asserting a stop/target result. Forming bars are risk observations,
never entry confirmations. A missing path blocks a new READY notification.
"""
import math
import sqlite3

VALID={'BINANCE_UM_FUTURES_WEBSOCKET','BINANCE_UM_FUTURES_ARCHIVE','BINANCE_UM_FUTURES_REST'}


def path_guard(con, p, now_ms):
    created=int(p['created_ms']);start=created//60000*60000;end=now_ms//60000*60000
    try:
        rows=con.execute('''SELECT open_ms,close_ms,open,high,low,close,source
            FROM futures_closed_klines WHERE symbol=? AND interval='1m'
            AND open_ms>=? AND open_ms<? ORDER BY open_ms''',(p['symbol'],start,end)).fetchall()
        current=con.execute('''SELECT open_ms,close_ms,open,high,low,close,source,event_ms,received_ms
            FROM futures_current_1m WHERE symbol=?''',(p['symbol'],)).fetchone()
    except sqlite3.Error:
        return dict(status='MISSING',reason='PATH_STORAGE_UNAVAILABLE')
    mapping={int(r[0]):r for r in rows}
    if (current and int(current[0])==end and
            current[6]=='BINANCE_UM_FUTURES_WEBSOCKET' and
            all(0<=now_ms-int(t)<=5000 for t in current[7:9])):
        mapping[end]=current[:7]
    missing=False
    for t in range(start,end+1,60000):
        r=mapping.get(t)
        if not r:
            missing=True;continue
        _,close_ms,op,hi,lo,cl,source=r
        if (source not in VALID or close_ms!=t+59999 or
                not all(isinstance(v,(float,int)) and math.isfinite(v) and v>0 for v in (op,hi,lo,cl)) or
                not lo<=min(op,cl)<=max(op,cl)<=hi):
            missing=True;continue
        up=p['direction']=='LONG';stop=p['stop'];target=p.get('obstacle')
        stop_hit=lo<=stop if up else hi>=stop
        target_hit=target is not None and (hi>=target if up else lo<=target)
        if stop_hit or target_hit:
            ambiguous=(t<created or missing or (stop_hit and target_hit))
            return dict(status='AMBIGUOUS' if ambiguous else 'BROKEN' if stop_hit else 'OBSTACLE_REACHED',
                        minute_open_ms=t,stop_touched=stop_hit,obstacle_touched=target_hit,
                        reason='ORDER_OR_BOUNDARY_UNKNOWN' if ambiguous else 'LOCKED_LEVEL_TOUCHED')
    return dict(status='MISSING' if missing else 'CLEAR',checked_through_ms=now_ms)
