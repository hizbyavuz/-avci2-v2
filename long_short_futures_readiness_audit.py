#!/usr/bin/env python3
"""Read-only readiness audit for the opt-in native Binance Futures signal path.

No Telegram, no orders, no environment mutations, no Spot fallback.
Run inside the Railway volume environment before any controlled activation.
"""
import argparse
import json
import os
import sqlite3
import time

INTERVALS={"1m":(30,180),"5m":(30,600),"1h":(25,7200)}

def inspect(db,symbol,now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
    out={"symbol":symbol,"ready":False,"intervals":{}}
    if not os.path.isfile(db):
        out["reason"]="FUTURES_DB_MISSING"
        return out
    try:
        with sqlite3.connect("file:"+os.path.abspath(db)+"?mode=ro",uri=True,timeout=3) as con:
            for interval,(count,max_age) in INTERVALS.items():
                rows=con.execute("""SELECT open_ms,close_ms,source FROM futures_closed_klines
                    WHERE symbol=? AND interval=? ORDER BY open_ms DESC LIMIT ?""",
                    (symbol,interval,count)).fetchall()
                rows.reverse()
                age=(now_ms-int(rows[-1][1]))/1000 if rows else None
                step={"1m":60000,"5m":300000,"1h":3600000}[interval]
                gaps=sum(int(b[0])-int(a[0])!=step for a,b in zip(rows,rows[1:]))
                sources=sorted({str(x[2]) for x in rows})
                valid_sources={"BINANCE_UM_FUTURES_WEBSOCKET",
                    "BINANCE_UM_FUTURES_ARCHIVE","BINANCE_UM_FUTURES_REST"}
                ok=(len(rows)==count and age is not None and 0<=age<=max_age
                    and gaps==0 and set(sources)<=valid_sources)
                out["intervals"][interval]={"ready":ok,"bars":len(rows),
                    "required_bars":count,"last_close_age_seconds":age,
                    "gaps":gaps,"sources":sources}
    except (OSError,sqlite3.Error,ValueError,TypeError) as exc:
        out["reason"]="FUTURES_DB_READ_ERROR"
        out["error_type"]=type(exc).__name__
        return out
    out["ready"]=all(v["ready"] for v in out["intervals"].values())
    out["reason"]="OK" if out["ready"] else "FUTURES_CANDLE_COVERAGE_INCOMPLETE"
    return out

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--symbol",default="BTCUSDT")
    parser.add_argument("--db",default=os.getenv("LS_FUTURES_KLINE_DB") or
        os.path.join(os.getenv("LS_STATE_DIR") or
        os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state",
        "binance_futures_kline_shadow.db"))
    args=parser.parse_args()
    result=inspect(args.db,args.symbol.upper())
    print(json.dumps(result,sort_keys=True))
    return 0 if result["ready"] else 2

if __name__=="__main__":
    raise SystemExit(main())
