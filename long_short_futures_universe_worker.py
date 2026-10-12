"""Persistent Binance USD-M Futures 24h ticker snapshot via WebSocket only."""
import json
import os
import sqlite3
import time

URL="wss://fstream.binance.com/market/stream"
SOURCE="BINANCE_UM_FUTURES_WEBSOCKET"

def parse_ticker(packet, received_ms=None):
    data=packet.get("data",packet)
    if not isinstance(data,list):
        return []
    now=int(time.time()*1000) if received_ms is None else int(received_ms)
    out=[]
    for x in data:
        if not isinstance(x,dict) or x.get("e")!="24hrTicker":
            continue
        symbol=str(x.get("s","")).upper()
        if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            continue
        try:
            event_ms=int(x["E"])
            qv=float(x["q"])
            price=float(x["c"])
            change=float(x["P"])
        except (KeyError,ValueError,TypeError,OverflowError):
            continue
        if not (0<=now-event_ms<=120000 and 0<price<1e15 and 0<=qv<1e20 and abs(change)<1e7):
            continue
        out.append((symbol,qv,price,change,now,SOURCE))
    return out

def initialize(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""CREATE TABLE IF NOT EXISTS futures_tickers(
        symbol TEXT PRIMARY KEY, quote_volume REAL NOT NULL,
        last_price REAL NOT NULL, change_pct REAL NOT NULL,
        received_ms INTEGER NOT NULL, source TEXT NOT NULL)""")
    conn.commit()

def run():
    import websocket
    root=os.getenv("LS_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".long-short-state"
    os.makedirs(root,exist_ok=True)
    db=os.path.join(root,"binance_futures_universe.db")
    while True:
        try:
            with sqlite3.connect(db,timeout=8) as conn:
                initialize(conn)
                ws=websocket.create_connection(URL,timeout=12,enable_multithread=True)
                last_report=0
                try:
                    ws.send(json.dumps({"method":"SUBSCRIBE","params":["!ticker@arr"],"id":81}))
                    ws.settimeout(15)
                    while True:
                        packet=json.loads(ws.recv())
                        rows=parse_ticker(packet)
                        if rows:
                            conn.executemany("""INSERT INTO futures_tickers
                                (symbol,quote_volume,last_price,change_pct,received_ms,source)
                                VALUES(?,?,?,?,?,?)
                                ON CONFLICT(symbol) DO UPDATE SET
                                quote_volume=excluded.quote_volume,
                                last_price=excluded.last_price,
                                change_pct=excluded.change_pct,
                                received_ms=excluded.received_ms,
                                source=excluded.source""",rows)
                            conn.commit()
                            if time.monotonic()-last_report>=60:
                                count,fresh=conn.execute(
                                    "SELECT COUNT(*),SUM(CASE WHEN received_ms>=? THEN 1 ELSE 0 END) FROM futures_tickers WHERE source=?",
                                    (int(time.time()*1000)-120000,SOURCE)).fetchone()
                                print("FUTURES_UNIVERSE_HEALTH "+json.dumps(
                                    {"state":"CONNECTED","symbols_total":count,
                                     "symbols_fresh":fresh or 0,"last_update_ms":rows[-1][4]}),flush=True)
                                last_report=time.monotonic()
                finally:
                    ws.close()
        except Exception as exc:
            print("FUTURES_UNIVERSE_FEED_ERROR",type(exc).__name__,str(exc)[:160],flush=True)
            time.sleep(5)

if __name__=="__main__":
    run()
