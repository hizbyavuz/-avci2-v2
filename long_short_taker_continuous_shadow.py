"""Low-storage continuous Binance Futures taker collector (research-only).

Stores one minute of aggregated taker flow per tracked symbol, not raw trades.
No Telegram, trading, signal gate, or historical DB mutation.
"""
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict

SOURCE="BINANCE_UM_FUTURES_WEBSOCKET"
URL="wss://fstream.binance.com/market/stream"

def initialize(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS taker_minute_shadow (
      symbol TEXT NOT NULL, minute_ms INTEGER NOT NULL,
      buy_quote REAL NOT NULL, sell_quote REAL NOT NULL,
      trades INTEGER NOT NULL, first_received_ms INTEGER NOT NULL,
      last_received_ms INTEGER NOT NULL, source TEXT NOT NULL,
      PRIMARY KEY(symbol,minute_ms)
    );
    CREATE TABLE IF NOT EXISTS taker_collector_health (
      observed_ms INTEGER NOT NULL, state TEXT NOT NULL,
      symbols_json TEXT NOT NULL, dropped INTEGER NOT NULL,
      details TEXT
    );
    """)
    conn.commit()

def record_minute(conn,symbol,minute,buy,sell,count,first,last):
    conn.execute("""INSERT INTO taker_minute_shadow
      VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(symbol,minute_ms) DO UPDATE SET
      buy_quote=buy_quote+excluded.buy_quote,
      sell_quote=sell_quote+excluded.sell_quote,
      trades=trades+excluded.trades,
      first_received_ms=MIN(first_received_ms,excluded.first_received_ms),
      last_received_ms=MAX(last_received_ms,excluded.last_received_ms)""",
      (symbol,minute,buy,sell,count,first,last,SOURCE))

def collect(db,symbols,stop,rotation_seconds=300):
    import websocket
    symbols=sorted({x.upper() for x in symbols if x.upper().endswith("USDT")})[:30]
    if not symbols:
        return {"state":"NO_SYMBOLS"}
    os.makedirs(os.path.dirname(os.path.abspath(db)),exist_ok=True)
    conn=sqlite3.connect(db,timeout=5)
    ws=None
    totals=defaultdict(lambda:[0.0,0.0,0,0,0])
    dropped=0
    state="DISCONNECTED"
    connected_at=None
    events_received=0
    last_heartbeat=time.monotonic()
    last_event=time.monotonic()
    try:
        initialize(conn)
        ws=websocket.create_connection(URL,timeout=8,enable_multithread=True)
        ws.send(json.dumps({"method":"SUBSCRIBE","params":[x.lower()+"@aggTrade" for x in symbols],"id":9}))
        ws.settimeout(1)
        state="CONNECTED"
        connected_at=int(time.time()*1000)
        end=time.monotonic()+rotation_seconds
        while not stop.is_set() and time.monotonic()<end:
            try:
                raw=ws.recv()
            except websocket.WebSocketTimeoutException:
                raw=None
            if raw:
                try:
                    packet=json.loads(raw)
                    event=packet.get("data",packet)
                    if event.get("e")=="aggTrade" and event.get("s") in symbols:
                        sym=event["s"]
                        event_ms=int(event["T"])
                        minute=(event_ms//60000)*60000
                        price=float(event["p"])
                        qty=float(event["q"])
                        if price>0 and qty>0:
                            now=int(time.time()*1000)
                            row=totals[(sym,minute)]
                            row[1 if event.get("m") else 0]+=price*qty
                            row[2]+=1
                            events_received+=1
                            last_event=time.monotonic()
                            row[3]=min(row[3] or now,now)
                            row[4]=max(row[4],now)
                except (ValueError,TypeError,KeyError,AttributeError):
                    dropped+=1
            # A silent socket can remain open without delivering market events.
            # Force reconnect instead of claiming continuous coverage.
            if time.monotonic()-last_event>120:
                state="ERROR_STALE_FLOW"
                break
            if time.monotonic()-last_heartbeat>=15:
                conn.execute("INSERT INTO taker_collector_health VALUES(?,?,?,?,?)",
                    (int(time.time()*1000),"CONNECTED",json.dumps(symbols),dropped,
                     json.dumps({"connected_at_ms":connected_at,
                                 "events_received":events_received})))
                conn.commit()
                last_heartbeat=time.monotonic()
            if events_received and events_received%10000==0:
                print("BINANCE_TAKER_SHADOW_HEARTBEAT "+json.dumps({
                    "state":state,"symbols":len(symbols),
                    "events_received":events_received,"dropped":dropped,
                    "seconds_since_last_event":round(time.monotonic()-last_event,1),
                    "connected_at_ms":connected_at}),flush=True)
            current=int(time.time()*1000)//60000*60000
            old=[key for key in totals if key[1]<current]
            if old:
                for sym,minute in old:
                    buy,sell,n,first,last=totals.pop((sym,minute))
                    record_minute(conn,sym,minute,buy,sell,n,first,last)
                conn.commit()
        if state=="CONNECTED":
            state="STOPPED" if stop.is_set() else "ROTATE"
    except Exception as exc:
        state="ERROR_"+type(exc).__name__
    finally:
        # Record an explicit boundary so a reconnect/rotation cannot be mistaken
        # for uninterrupted market-data coverage.
        if ws:
            try: ws.close()
            except Exception: pass
        if totals:
            for (sym,minute),(buy,sell,n,first,last) in totals.items():
                record_minute(conn,sym,minute,buy,sell,n,first,last)
        conn.execute("INSERT INTO taker_collector_health VALUES(?,?,?,?,?)",
                     (int(time.time()*1000),state,json.dumps(symbols),dropped,
                      json.dumps({"connected_at_ms":connected_at})))
        conn.commit()
        conn.close()
    return {"state":state,"symbols":len(symbols),"dropped":dropped,"events_received":events_received}

def window(db,symbol,now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else now_ms
    last_closed=(now_ms//60000)*60000-60000
    start=last_closed-4*60000
    with sqlite3.connect("file:"+os.path.abspath(db)+"?mode=ro",uri=True,timeout=5) as c:
        rows=c.execute("""SELECT minute_ms,buy_quote,sell_quote,trades
           FROM taker_minute_shadow WHERE symbol=? AND minute_ms BETWEEN ? AND ?
           ORDER BY minute_ms""",(symbol,start,last_closed)).fetchall()
        health=c.execute("""SELECT observed_ms,state,dropped,symbols_json,details
           FROM taker_collector_health WHERE observed_ms>=? ORDER BY observed_ms DESC LIMIT 30""",
           (start-300000,)).fetchall()
    covered={x[0] for x in rows}
    gaps=[m for m in range(start,last_closed+1,60000) if m not in covered]
    # A minute containing trades is NOT proof of uninterrupted websocket coverage.
    # Fail closed until one uninterrupted collection session spans the full window.
    # Require the latest session to span the complete closed-minute window.
    # A reconnect cannot borrow coverage from an older session.
    latest=health[0] if health else None
    session_ok=False
    if latest:
        try:
            details=json.loads(latest[4] or "{}")
            session_ok=(
                latest[1] in ("CONNECTED","ROTATE","STOPPED")
                and latest[2]==0
                and symbol in json.loads(latest[3])
                and isinstance(details.get("connected_at_ms"),int)
                and details["connected_at_ms"]<=start
                and latest[0]>=last_closed+60000
                and (latest[1]!="CONNECTED" or now_ms-latest[0]<=30000)
            )
        except (ValueError,TypeError,KeyError):
            session_ok=False
    interruptions=not session_ok or any(
        start<=h[0]<=now_ms and (h[1].startswith("ERROR") or h[2]>0)
        for h in health
    )
    buy=sum(x[1] for x in rows)
    sell=sum(x[2] for x in rows)
    ready=not gaps and not interruptions and buy>0 and sell>0
    return {"ready":ready,"source":SOURCE,"symbol":symbol,
            "taker_ratio":buy/sell if ready else None,
            "coverage_minutes":len(covered),"missing_minutes":len(gaps),
            "interruptions_detected":interruptions}
