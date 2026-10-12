"""Read-only health report for the existing Binance native taker collector.

Do not confuse a stored historical trade with fresh continuous coverage.
"""
import os
import sqlite3
import time

SOURCE = "BINANCE_UM_FUTURES_WEBSOCKET"

def check(db_path, symbol, now_ms=None, max_age_ms=120000):
    now_ms = int(time.time()*1000) if now_ms is None else int(now_ms)
    result = {"symbol":symbol.upper(),"source":SOURCE,"ready":False,
              "reason":"COLLECTOR_DB_MISSING","trade_count_2m":0,
              "buy_quote_2m":None,"sell_quote_2m":None,"taker_buy_sell_ratio":None}
    if not os.path.isfile(db_path):
        return result
    try:
        with sqlite3.connect("file:"+os.path.abspath(db_path)+"?mode=ro",uri=True) as conn:
            row=conn.execute("""SELECT COUNT(*),MAX(event_ms),
                    SUM(CASE WHEN signed_quote>0 THEN signed_quote ELSE 0 END),
                    SUM(CASE WHEN signed_quote<0 THEN -signed_quote ELSE 0 END)
                    FROM taker_trades
                    WHERE symbol=? AND source=? AND event_ms>? AND event_ms<=?""",
                    (symbol.upper(),SOURCE,now_ms-max_age_ms,now_ms)).fetchone()
            count, latest, buy, sell = row
            result["trade_count_2m"]=int(count or 0)
            if not count or latest is None:
                result["reason"]="NO_RECENT_TRADES"
            elif not (0<=now_ms-int(latest)<=max_age_ms):
                result["reason"]="STALE_TRADES"
            elif not buy or not sell or buy<=0 or sell<=0:
                result["reason"]="ONE_SIDED_OR_INSUFFICIENT_FLOW"
            else:
                result.update(ready=True,reason=None,buy_quote_2m=float(buy),
                              sell_quote_2m=float(sell),
                              taker_buy_sell_ratio=float(buy)/float(sell))
    except (sqlite3.Error,OSError):
        result["reason"]="DB_READ_ERROR"
    return result
