#!/usr/bin/env python3
"""V4 shadow-only native Binance Futures streams, UTC append-only provenance.
No trade signals, Telegram, API keys, order privileges or V3 state mutation.
A scheduled short collector is NOT continuous coverage: gaps are explicitly logged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

VERSION="LS_V4_NATIVE_RESEARCH_2026_10_08"
SOURCE="BINANCE_UM_FUTURES_WEBSOCKET"
MARKET="wss://fstream.binance.com/market/stream"
PUBLIC="wss://fstream.binance.com/public/stream"
SYMBOL_RE=re.compile(r"^[A-Z0-9]{2,35}USDT$")
MAX_DEEP_SYMBOLS=30

def now_ms():
    return int(time.time()*1000)

def utc(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()

def choose_symbols(argument, analyst_db=None, limit=MAX_DEEP_SYMBOLS):
    """Use most recent frozen V3 scan as read-only symbol shortlist."""
    symbols=[s.strip().upper() for s in (argument or "").split(",") if s.strip()]
    if analyst_db and os.path.isfile(analyst_db):
        try:
            with sqlite3.connect("file:"+os.path.abspath(analyst_db)+"?mode=ro",uri=True) as c:
                latest=c.execute("SELECT MAX(scan_time_utc) FROM analyses").fetchone()[0]
                if latest:
                    symbols += [r[0] for r in c.execute(
                        "SELECT symbol FROM analyses WHERE scan_time_utc=? ORDER BY confidence DESC LIMIT ?",
                        (latest,limit))]
        except (sqlite3.Error,OSError):
            pass
    symbols=["BTCUSDT","ETHUSDT"]+symbols
    return list(dict.fromkeys(s for s in symbols if SYMBOL_RE.fullmatch(s)))[:limit]

def streams_for(symbols):
    market=["!ticker@arr","!markPrice@arr","!forceOrder@arr"]
    public=[]
    for s in symbols:
        x=s.lower()
        market.extend([x+"@kline_1m",x+"@kline_5m",x+"@aggTrade"])
        public.append(x+"@bookTicker")
    return market,public

def initialize(c):
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS collection_runs (
      id TEXT PRIMARY KEY, version TEXT NOT NULL, started_ms INTEGER NOT NULL,
      stopped_ms INTEGER, symbols_json TEXT NOT NULL, received INTEGER DEFAULT 0,
      accepted INTEGER DEFAULT 0, dropped INTEGER DEFAULT 0, state TEXT NOT NULL,
      market_connected INTEGER DEFAULT 0, public_connected INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS raw_events (
      event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, received_ms INTEGER NOT NULL,
      event_ms INTEGER, symbol TEXT NOT NULL, source TEXT NOT NULL,
      route TEXT NOT NULL, stream TEXT NOT NULL, payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS raw_symbol_time ON raw_events(symbol,event_ms);
    CREATE TABLE IF NOT EXISTS closed_klines(
      symbol TEXT NOT NULL, interval TEXT NOT NULL, open_ms INTEGER NOT NULL,
      close_ms INTEGER NOT NULL, source TEXT NOT NULL, received_ms INTEGER NOT NULL,
      open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
      volume REAL NOT NULL, quote_volume REAL, taker_buy_base REAL, taker_buy_quote REAL,
      trades INTEGER, event_id TEXT NOT NULL, PRIMARY KEY(symbol,interval,open_ms,source)
    );
    CREATE TABLE IF NOT EXISTS marks (
      event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, event_ms INTEGER NOT NULL,
      received_ms INTEGER NOT NULL, mark REAL NOT NULL, index_price REAL,
      funding_rate REAL, next_funding_ms INTEGER, source TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS liquidations(
      event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, event_ms INTEGER NOT NULL,
      received_ms INTEGER NOT NULL, side TEXT, price REAL, qty REAL, notional REAL,
      source TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS book_top(
      event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, event_ms INTEGER NOT NULL,
      received_ms INTEGER NOT NULL, bid REAL, ask REAL, bid_qty REAL, ask_qty REAL,
      spread_bps REAL, source TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS taker_trades(
      event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, event_ms INTEGER NOT NULL,
      received_ms INTEGER NOT NULL, price REAL NOT NULL, qty REAL NOT NULL,
      signed_quote REAL NOT NULL, source TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS data_issues(
      id INTEGER PRIMARY KEY AUTOINCREMENT, observed_ms INTEGER NOT NULL,
      run_id TEXT, symbol TEXT, code TEXT NOT NULL, details TEXT
    );
    """)
    c.commit()

def issue(c,run_id,code,symbol=None,details=None):
    c.execute("INSERT INTO data_issues(observed_ms,run_id,symbol,code,details) VALUES(?,?,?,?,?)",
              (now_ms(),run_id,symbol,code,str(details or "")[:500]))

def finite_price(val):
    try:
        v=float(val)
        return v if 0<v<1e20 else None
    except (TypeError,ValueError,OverflowError):
        return None

def ingest(c, run_id, route, stream, obj, received_ms):
    """Strictly interpret only known Binance public market event formats."""
    if not isinstance(obj,dict):
        return 0
    if obj.get("st") not in (None,1,"1"):
        return 0
    symbol=str(obj.get("s") or (obj.get("k") or {}).get("s") or
               (obj.get("o") or {}).get("s") or "").upper()
    if not SYMBOL_RE.fullmatch(symbol):
        return 0
    event_ms=int(obj.get("E") or obj.get("T") or received_ms)
    raw=json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    eid=hashlib.sha256((route+"|"+stream+"|"+raw).encode()).hexdigest()
    row=c.execute("""INSERT OR IGNORE INTO raw_events
      (event_id,run_id,received_ms,event_ms,symbol,source,route,stream,payload_json)
      VALUES(?,?,?,?,?,?,?,?,?)""",
      (eid,run_id,received_ms,event_ms,symbol,SOURCE,route,stream,raw))
    if row.rowcount == 0:
        return 0
    if obj.get("e")=="kline":
        k=obj.get("k") or {}
        if k.get("x") is True and k.get("i") in ("1m","5m"):
            op,hi,lo,cl=(finite_price(k.get(key)) for key in ("o","h","l","c"))
            if None in (op,hi,lo,cl) or lo>min(op,cl) or hi<max(op,cl):
                issue(c,run_id,"BAD_KLINE",symbol,eid);return 1
            c.execute("""INSERT OR IGNORE INTO closed_klines
            (symbol,interval,open_ms,close_ms,source,received_ms,open,high,low,close,
             volume,quote_volume,taker_buy_base,taker_buy_quote,trades,event_id)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (symbol,k["i"],int(k["t"]),int(k["T"]),SOURCE,received_ms,op,hi,lo,cl,
              float(k.get("v") or 0),float(k.get("q") or 0),
              float(k.get("V") or 0),float(k.get("Q") or 0),int(k.get("n") or 0),eid))
    elif obj.get("e")=="markPriceUpdate":
        mark=finite_price(obj.get("p"))
        if mark is not None:
            c.execute("""INSERT OR IGNORE INTO marks VALUES(?,?,?,?,?,?,?,?,?)""",
                (eid,symbol,event_ms,received_ms,mark,finite_price(obj.get("i")),
                 float(obj["r"]) if obj.get("r") is not None else None,
                 int(obj["T"]) if obj.get("T") is not None else None,SOURCE))
    elif obj.get("e")=="forceOrder":
        o=obj.get("o") or {}
        price=finite_price(o.get("ap")) or finite_price(o.get("p"))
        qty=finite_price(o.get("z")) or finite_price(o.get("q"))
        c.execute("""INSERT OR IGNORE INTO liquidations VALUES(?,?,?,?,?,?,?,?,?)""",
                  (eid,symbol,event_ms,received_ms,o.get("S"),price,qty,
                   price*qty if price and qty else None,SOURCE))
    elif obj.get("e")=="aggTrade":
        price,qty=finite_price(obj.get("p")),finite_price(obj.get("q"))
        if price and qty:
            buy=not bool(obj.get("m"))
            c.execute("INSERT OR IGNORE INTO taker_trades VALUES(?,?,?,?,?,?,?,?)",
                      (eid,symbol,int(obj.get("T") or event_ms),received_ms,
                       price,qty,(1 if buy else -1)*price*qty,SOURCE))
    elif "bookTicker" in stream or obj.get("e")=="bookTicker":
        bid,ask=finite_price(obj.get("b")),finite_price(obj.get("a"))
        if bid and ask and ask>=bid:
            mid=(bid+ask)/2
            c.execute("INSERT OR IGNORE INTO book_top VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (eid,symbol,event_ms,received_ms,bid,ask,
                       float(obj.get("B") or 0),float(obj.get("A") or 0),
                       (ask-bid)/mid*10000,SOURCE))
    return 1

def pump(name,url,streams,out,stop,health):
    import websocket  # only required for actual collection; tests are offline
    ws=None
    try:
        ws=websocket.create_connection(url,timeout=8,enable_multithread=True)
        health[name]=True
        ws.send(json.dumps({"method":"SUBSCRIBE","params":streams,"id":1 if name=="market" else 2}))
        ws.settimeout(2)
        while not stop.is_set():
            try:
                raw=ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            received=now_ms()
            try:
                msg=json.loads(raw)
                data=msg.get("data",msg) if isinstance(msg,dict) else msg
                stream=msg.get("stream","") if isinstance(msg,dict) else ""
                items=data if isinstance(data,list) else [data]
                for item in items:
                    if isinstance(item,dict) and item.get("result") is None and "id" in item:
                        continue
                    try:out.put_nowait((name,stream,item,received))
                    except queue.Full:health["dropped"]+=1
            except (ValueError,TypeError) as exc:
                health[name+"_decode_errors"]+=1
    except Exception as exc:
        health[name+"_error"]=type(exc).__name__+":"+str(exc)[:160]
    finally:
        if ws:
            try:ws.close()
            except Exception:pass

def observe(db, symbols, seconds):
    if not 2 <= seconds <= 3600:
        raise ValueError("seconds outside safe 2..3600 interval")
    os.makedirs(os.path.dirname(os.path.abspath(db)),exist_ok=True)
    with sqlite3.connect(db) as c:
        initialize(c)
        run=uuid.uuid4().hex
        started=now_ms()
        c.execute("INSERT INTO collection_runs(id,version,started_ms,symbols_json,state) VALUES(?,?,?,?,?)",
                  (run,VERSION,started,json.dumps(symbols),"RUNNING"))
        c.commit()
        market,public=streams_for(symbols)
        q=queue.Queue(maxsize=50000)
        stop=threading.Event()
        health={"market":False,"public":False,"dropped":0,
                "market_decode_errors":0,"public_decode_errors":0}
        threads=[
            threading.Thread(target=pump,args=("market",MARKET,market,q,stop,health),daemon=True),
            threading.Thread(target=pump,args=("public",PUBLIC,public,q,stop,health),daemon=True),
        ]
        for th in threads:th.start()
        received=accepted=0
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline or not q.empty():
            # Shut down producers at the deadline; otherwise a busy market
            # could keep the queue perpetually non-empty.
            if time.monotonic()>=deadline and not stop.is_set():
                stop.set()
            try:
                route,stream,obj,ts=q.get(timeout=0.3)
            except queue.Empty:
                continue
            received+=1
            try:
                accepted+=ingest(c,run,route,stream,obj,ts)
            except (ValueError,KeyError,TypeError,sqlite3.Error) as exc:
                issue(c,run,"BAD_EVENT",details=type(exc).__name__+":"+str(exc)[:200])
            if received%200==0:c.commit()
        stop.set()
        for th in threads:th.join(timeout=3)
        if not health["market"]:issue(c,run,"NATIVE_MARKET_DISCONNECTED",details=health.get("market_error"))
        if not health["public"]:issue(c,run,"NATIVE_BOOK_DISCONNECTED",details=health.get("public_error"))
        if health["dropped"]:issue(c,run,"QUEUE_OVERFLOW",details=health["dropped"])
        # Do not silently treat scheduled 90s windows as 5m continuous capture.
        previous=c.execute("""SELECT stopped_ms FROM collection_runs
                              WHERE id<>? AND stopped_ms IS NOT NULL
                              ORDER BY stopped_ms DESC LIMIT 1""",(run,)).fetchone()
        if previous and started-int(previous[0])>90000:
            issue(c,run,"COLLECTION_GAP",details=f"{started-int(previous[0])}ms")
        state="OBSERVED" if accepted and health["market"] else "DATA_FAILURE"
        c.execute("""UPDATE collection_runs SET stopped_ms=?,received=?,accepted=?,dropped=?,
          state=?,market_connected=?,public_connected=? WHERE id=?""",
          (now_ms(),received,accepted,health["dropped"],state,
           int(health["market"]),int(health["public"]),run))
        c.commit()
        return {"version":VERSION,"run_id":run,"state":state,"received":received,
                "accepted":accepted,"symbols":len(symbols),"health":health,
                "continuous_coverage":False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="long_short_v4_native.db")
    p.add_argument("--analyst-db",default="")
    p.add_argument("--symbols",default=os.getenv("LS_V4_SYMBOLS","BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"))
    p.add_argument("--seconds",type=int,default=90)
    p.add_argument("--report",default="long_short_v4_collection.json")
    a=p.parse_args()
    symbols=choose_symbols(a.symbols,a.analyst_db)
    result=observe(a.db,symbols,a.seconds)
    with open(a.report,"w",encoding="utf-8") as f:json.dump(result,f,indent=2,ensure_ascii=False)
    print(json.dumps(result,ensure_ascii=False))
    if result["state"]!="OBSERVED":raise SystemExit(2)

if __name__=="__main__": main()
