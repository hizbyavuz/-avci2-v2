#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append-only Gate missed-mover learning dataset.

Captures first on-chain/early-watch anomaly, prior ~6h Spot/on-chain path,
and matched same-batch controls for audited Gate movers.
Research-only; never changes security or signal thresholds.
"""
import json, os, sqlite3, time
from datetime import datetime, timezone, timedelta

DB=os.getenv("AVCI_DB","avci2.db")
VERSION="gate-missed-learning-v1-20260927"
LOOKBACK_HOURS=6
CONTROLS_PER_MOVER=4

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def ensure(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS gate_first_anomaly_registry(
      network_id TEXT NOT NULL,token_contract TEXT NOT NULL,
      first_seen_ts INTEGER NOT NULL,first_price REAL,first_change_24h REAL,
      reasons_json TEXT NOT NULL,version TEXT NOT NULL,
      PRIMARY KEY(network_id,token_contract)
    );
    CREATE TABLE IF NOT EXISTS gate_missed_prehistory(
      spot_batch_id TEXT NOT NULL,mover_pair TEXT NOT NULL,subject_pair TEXT NOT NULL,
      subject_class TEXT NOT NULL,sample_ts INTEGER NOT NULL,
      offset_minutes INTEGER NOT NULL,price REAL,volume_24h REAL,change_24h REAL,
      network_id TEXT,token_contract TEXT,onchain_price REAL,liquidity REAL,
      volume_5m REAL,volume_1h REAL,buys_5m REAL,sells_5m REAL,
      own_volume_ratio REAL,observed_anomaly INTEGER,
      version TEXT NOT NULL,
      PRIMARY KEY(spot_batch_id,mover_pair,subject_pair,sample_ts,version)
    );
    CREATE TABLE IF NOT EXISTS gate_missed_control_map(
      spot_batch_id TEXT NOT NULL,mover_pair TEXT NOT NULL,control_pair TEXT NOT NULL,
      distance REAL NOT NULL,version TEXT NOT NULL,
      PRIMARY KEY(spot_batch_id,mover_pair,control_pair,version)
    );
    """)

def contracts(c,pair):
    return c.execute("SELECT network_id,token_contract FROM gate_spot_contracts WHERE pair=?",(pair,)).fetchall() if table(c,"gate_spot_contracts") else []

def first_anomalies(c):
    if not table(c,"gate_early_observations"):return 0
    n=0
    rows=c.execute("""SELECT * FROM gate_early_observations WHERE observed_anomaly=1
      ORDER BY scan_ts ASC""").fetchall()
    for r in rows:
        if c.execute("""SELECT 1 FROM gate_first_anomaly_registry WHERE network_id=? AND token_contract=?""",
                     (r["network_id"],r["token_contract"])).fetchone():continue
        c.execute("""INSERT INTO gate_first_anomaly_registry VALUES(?,?,?,?,?,?,?)""",
          (r["network_id"],r["token_contract"],r["scan_ts"],r["price"],r["change_24h"],
           json.dumps(["ONCHAIN_ANOMALY"]),VERSION));n+=1
    if table(c,"gate_opportunity_observations"):
        cols={x[1] for x in c.execute("PRAGMA table_info(gate_opportunity_observations)")}
        if "early_watch" in cols:
            rows=c.execute("""SELECT o.pair,o.early_watch_reason_json,h.scan_ts
              FROM gate_opportunity_observations o JOIN gate_spot_health h ON h.batch_id=o.batch_id
              WHERE o.early_watch=1 ORDER BY h.scan_ts""").fetchall()
            for r in rows:
                for x in contracts(c,r["pair"]):
                    if c.execute("""SELECT 1 FROM gate_first_anomaly_registry WHERE network_id=? AND token_contract=?""",
                                 (x["network_id"],x["token_contract"])).fetchone():continue
                    c.execute("""INSERT INTO gate_first_anomaly_registry VALUES(?,?,?,?,?,?,?)""",
                      (x["network_id"],x["token_contract"],r["scan_ts"],None,None,
                       r["early_watch_reason_json"] or "[]",VERSION));n+=1
    return n

def current_row(c,batch,pair):
    return c.execute("""SELECT * FROM gate_spot_history WHERE batch_id=? AND pair=?""",(batch,pair)).fetchone()

def distance(a,b):
    d=abs(float(a["change_24h"] or 0)-float(b["change_24h"] or 0))/10.0
    av=max(float(a["volume_24h"] or 0),1.0); bv=max(float(b["volume_24h"] or 0),1.0)
    import math
    d+=abs(math.log10(av)-math.log10(bv))
    return d

def save_path(c,batch,mover,subject,klass,end_ts):
    start_ts=end_ts-LOOKBACK_HOURS*3600
    rows=c.execute("""SELECT h.batch_id,h.scan_ts,s.last,s.volume_24h,s.change_24h
      FROM gate_spot_health h JOIN gate_spot_history s ON s.batch_id=h.batch_id
      WHERE s.pair=? AND h.status='VALID' AND h.scan_ts BETWEEN ? AND ?
      ORDER BY h.scan_ts""",(subject,start_ts,end_ts)).fetchall()
    mapped=contracts(c,subject)
    network=mapped[0]["network_id"] if mapped else None
    contract=mapped[0]["token_contract"] if mapped else None
    for r in rows:
        oc=None
        if network and contract and table(c,"gate_early_observations"):
            oc=c.execute("""SELECT * FROM gate_early_observations
              WHERE network_id=? AND token_contract=? AND scan_ts<=?
              ORDER BY scan_ts DESC LIMIT 1""",(network,contract,r["scan_ts"])).fetchone()
        vals=(oc["price"],oc["liquidity"],oc["volume_5m"],oc["volume_1h"],oc["buys_5m"],
              oc["sells_5m"],oc["own_volume_ratio"],oc["observed_anomaly"]) if oc else (None,)*8
        c.execute("""INSERT OR IGNORE INTO gate_missed_prehistory VALUES(
          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (batch,mover,subject,klass,r["scan_ts"],int((r["scan_ts"]-end_ts)/60),
           r["last"],r["volume_24h"],r["change_24h"],network,contract,*vals,VERSION))

def main():
    if not os.path.exists(DB):print("Gate missed learning: DB yok");return
    with sqlite3.connect(DB,timeout=60) as c:
        c.row_factory=sqlite3.Row;ensure(c)
        new_first=first_anomalies(c)
        if not table(c,"gate_top_mover_audit"):
            c.commit();print(f"Gate missed learning: first_anomaly={new_first}; audit yok");return
        latest=c.execute("""SELECT spot_batch_id,MAX(created_scan_ts) ts FROM gate_top_mover_audit""").fetchone()
        if not latest or not latest["spot_batch_id"]:
            c.commit();print("Gate missed learning: mover audit yok");return
        batch=latest["spot_batch_id"];end_ts=int(latest["ts"] or time.time())
        movers=c.execute("""SELECT * FROM gate_top_mover_audit WHERE spot_batch_id=?
          AND audit_status IN ('MISSED','LATE_CAUGHT','NO_ONCHAIN_HISTORY')
          ORDER BY current_change_24h DESC""",(batch,)).fetchall()
        all_rows=c.execute("SELECT * FROM gate_spot_history WHERE batch_id=?",(batch,)).fetchall()
        mover_pairs={m["pair"] for m in movers}
        controls=0
        for m in movers:
            pair=m["pair"]; anchor=current_row(c,batch,pair)
            if not anchor:continue
            save_path(c,batch,pair,pair,"MOVER",end_ts)
            pool=[r for r in all_rows if r["pair"] not in mover_pairs and r["pair"]!=pair
                  and float(r["change_24h"] or 0)<10 and float(r["volume_24h"] or 0)>=30000]
            for dist,r in sorted(((distance(anchor,r),r) for r in pool),key=lambda x:x[0])[:CONTROLS_PER_MOVER]:
                cp=r["pair"]
                c.execute("""INSERT OR IGNORE INTO gate_missed_control_map VALUES(?,?,?,?,?)""",
                          (batch,pair,cp,dist,VERSION))
                save_path(c,batch,pair,cp,"MATCHED_CONTROL",end_ts);controls+=1
        c.commit()
        print(f"Gate missed learning: movers={len(movers)} controls={controls} first_anomaly_new={new_first}")

if __name__=="__main__":main()
