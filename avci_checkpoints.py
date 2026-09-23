"""Scheduled post-signal checkpoints for Avci research.
Stores 15m/1h/4h/24h/72h observations. It never changes signal selection.
"""
import sqlite3
from datetime import datetime, timezone
from telegram_readable import binance_price, gecko_token, pct

HORIZONS=(("15m",15*60),("1h",3600),("4h",4*3600),("24h",24*3600),("72h",72*3600))

def parse_iso(value):
    return int(datetime.fromisoformat(str(value).replace("Z","+00:00")).timestamp())

def due(age,target,tolerance=12*60):
    return age>=target and age<=target+tolerance

def binance(path="binance_avci2.db"):
    con=sqlite3.connect(path,timeout=60); con.row_factory=sqlite3.Row
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS signal_checkpoints(
            event_id TEXT NOT NULL,horizon TEXT NOT NULL,observed_at_utc TEXT NOT NULL,
            price REAL,return_pct REAL,status TEXT NOT NULL,
            PRIMARY KEY(event_id,horizon))""")
        now=int(datetime.now(timezone.utc).timestamp())
        rows=con.execute("""SELECT event_id,symbol,signal_time_utc,signal_price
            FROM signal_events WHERE event_class='CANDIDATE'
            ORDER BY signal_time_utc DESC LIMIT 500""").fetchall()
        written=0
        for r in rows:
            age=now-parse_iso(r["signal_time_utc"])
            for label,seconds in HORIZONS:
                if not due(age,seconds): continue
                exists=con.execute("SELECT 1 FROM signal_checkpoints WHERE event_id=? AND horizon=?",
                                   (r["event_id"],label)).fetchone()
                if exists: continue
                cur=binance_price(r["symbol"])
                ret=pct(cur,r["signal_price"])
                con.execute("INSERT INTO signal_checkpoints VALUES(?,?,?,?,?,?)",
                            (r["event_id"],label,datetime.now(timezone.utc).isoformat(),
                             cur,ret,"MEASURED" if cur is not None else "PRICE_MISSING"))
                written+=1
        con.commit(); print(f"Binance checkpoints: {written}")
    finally: con.close()

def gate(obs_path="avci2.db",val_path="avci_validation_v5.db"):
    obs=sqlite3.connect(obs_path,timeout=60); val=sqlite3.connect(val_path,timeout=60)
    val.row_factory=sqlite3.Row
    try:
        obs.execute("""CREATE TABLE IF NOT EXISTS gate_signal_checkpoints(
            validation_id INTEGER NOT NULL,horizon TEXT NOT NULL,observed_at_utc TEXT NOT NULL,
            price REAL,return_pct REAL,status TEXT NOT NULL,
            PRIMARY KEY(validation_id,horizon))""")
        now=int(datetime.now(timezone.utc).timestamp())
        rows=val.execute("""SELECT id,network_id,token_contract,signal_ts,signal_price
            FROM validation_events WHERE group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')
            ORDER BY signal_ts DESC LIMIT 500""").fetchall()
        written=0
        for r in rows:
            age=now-int(r["signal_ts"])
            for label,seconds in HORIZONS:
                if not due(age,seconds): continue
                if obs.execute("SELECT 1 FROM gate_signal_checkpoints WHERE validation_id=? AND horizon=?",
                               (r["id"],label)).fetchone(): continue
                cur=gecko_token(r["network_id"],r["token_contract"]).get("price")
                ret=pct(cur,r["signal_price"])
                obs.execute("INSERT INTO gate_signal_checkpoints VALUES(?,?,?,?,?,?)",
                            (r["id"],label,datetime.now(timezone.utc).isoformat(),
                             cur,ret,"MEASURED" if cur is not None else "PRICE_MISSING"))
                written+=1
        obs.commit(); print(f"Gate checkpoints: {written}")
    finally:
        val.close(); obs.close()

if __name__=="__main__":
    import sys
    {"binance":binance,"gate":gate}[sys.argv[1]]()
