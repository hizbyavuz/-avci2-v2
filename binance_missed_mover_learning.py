#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append-only Binance missed-mover learning dataset.

Research-only. Captures:
1) first observable anomaly per symbol,
2) the prior ~6h feature path for audited movers,
3) matched same-scan controls that did not become audited movers.

Never changes frozen scanner thresholds or candidate selection.
"""
import json, os, sqlite3
from datetime import datetime, timezone, timedelta

DB=os.getenv("BINANCE_DB","binance_avci2.db")
VERSION="binance-missed-learning-v1-20260927"
LOOKBACK_HOURS=6
CONTROLS_PER_MOVER=4

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def iso_now(): return datetime.now(timezone.utc).isoformat()

def parse_dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None

def raw(row):
    try:return json.loads(row["raw_json"] or "{}")
    except Exception:return {}

def ensure(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS binance_first_anomaly_registry(
      symbol TEXT PRIMARY KEY,
      first_seen_utc TEXT NOT NULL,
      first_price REAL,
      first_change_24h REAL,
      stage TEXT,
      reasons_json TEXT NOT NULL,
      version TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS binance_missed_prehistory(
      audit_scan_utc TEXT NOT NULL,
      mover_symbol TEXT NOT NULL,
      subject_symbol TEXT NOT NULL,
      subject_class TEXT NOT NULL,
      offset_minutes INTEGER NOT NULL,
      feature_time_utc TEXT NOT NULL,
      price REAL,change_15m REAL,change_1h REAL,change_24h REAL,
      volume_mult_15m REAL,volume_mult_1h REAL,volume_z_15m REAL,trade_z_15m REAL,
      return_z_15m REAL,taker_buy_ratio_15m REAL,retention_proxy REAL,
      persistence INTEGER,reignition INTEGER,wakeup INTEGER,trigger INTEGER,
      cross_sectional_rarity_pct REAL,btc_relative_24h REAL,oi_change_1h_pct REAL,
      funding_rate REAL,spread_bps REAL,stage TEXT,selection_class TEXT,
      version TEXT NOT NULL,
      PRIMARY KEY(audit_scan_utc,mover_symbol,subject_symbol,feature_time_utc,version)
    );
    CREATE TABLE IF NOT EXISTS binance_missed_control_map(
      audit_scan_utc TEXT NOT NULL,
      mover_symbol TEXT NOT NULL,
      control_symbol TEXT NOT NULL,
      distance REAL NOT NULL,
      version TEXT NOT NULL,
      PRIMARY KEY(audit_scan_utc,mover_symbol,control_symbol,version)
    );
    """)

def first_anomalies(c,ts):
    if not table(c,"features"):return 0
    rows=c.execute("""SELECT f.*,o.early_watch,o.early_watch_reason_json
      FROM features f
      LEFT JOIN opportunity_observations o
        ON o.scan_time_utc=f.scan_time_utc AND o.symbol=f.symbol
      WHERE f.scan_time_utc=?""",(ts,)).fetchall() if table(c,"opportunity_observations") else       c.execute("SELECT f.*,0 early_watch,'[]' early_watch_reason_json FROM features f WHERE scan_time_utc=?",(ts,)).fetchall()
    n=0
    for r in rows:
        reasons=[]
        if int(r["wakeup"] or 0):reasons.append("WAKE_UP")
        if int(r["persistence"] or 0):reasons.append("PERSISTENCE")
        if int(r["reignition"] or 0):reasons.append("REIGNITION")
        if int(r["early_watch"] or 0):
            try: reasons.extend(json.loads(r["early_watch_reason_json"] or "[]"))
            except Exception: reasons.append("EARLY_WATCH")
        if not reasons:continue
        before=c.execute("SELECT 1 FROM binance_first_anomaly_registry WHERE symbol=?",(r["symbol"],)).fetchone()
        if before:continue
        c.execute("""INSERT INTO binance_first_anomaly_registry VALUES(?,?,?,?,?,?,?)""",
          (r["symbol"],r["scan_time_utc"],r["price"],r["change_24h"],r["stage"],
           json.dumps(sorted(set(reasons))),VERSION))
        n+=1
    return n

def feature_distance(a,b):
    # Matching deliberately uses broad pre-outcome context, not future performance.
    d=0.0
    for key,scale in (("change_24h",10.0),("volume_mult_15m",2.0),
                      ("taker_buy_ratio_15m",0.25),("cross_sectional_rarity_pct",25.0)):
        try:
            av=float(a[key] or 0); bv=float(b[key] or 0); d+=abs(av-bv)/scale
        except Exception: d+=1.0
    if (a["btc_regime"] or "UNKNOWN")!=(b["btc_regime"] or "UNKNOWN"):d+=2.0
    return d

def save_path(c,audit_ts,mover,subject,klass,start,end):
    rows=c.execute("""SELECT * FROM features WHERE symbol=? AND scan_time_utc>=? AND scan_time_utc<=?
      ORDER BY scan_time_utc""",(subject,start,end)).fetchall()
    end_dt=parse_dt(end)
    for r in rows:
        ft=parse_dt(r["scan_time_utc"])
        off=int((ft-end_dt).total_seconds()/60) if ft and end_dt else 0
        vals=[r[k] if k in r.keys() else None for k in (
          "price","change_15m","change_1h","change_24h","volume_mult_15m","volume_mult_1h",
          "volume_z_15m","trade_z_15m","return_z_15m","taker_buy_ratio_15m","retention_proxy",
          "persistence","reignition","wakeup","trigger","cross_sectional_rarity_pct",
          "btc_relative_24h","oi_change_1h_pct","funding_rate","spread_bps","stage","selection_class")]
        c.execute("""INSERT OR IGNORE INTO binance_missed_prehistory VALUES(
          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (audit_ts,mover,subject,klass,off,r["scan_time_utc"],*vals,VERSION))

def main():
    if not os.path.exists(DB):print("Binance missed learning: DB yok");return
    with sqlite3.connect(DB,timeout=60) as c:
        c.row_factory=sqlite3.Row; ensure(c)
        scan=c.execute("""SELECT scan_time_utc FROM scans WHERE health_status!='INVALID'
          ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:print("Binance missed learning: scan yok");return
        ts=scan["scan_time_utc"]
        new_first=first_anomalies(c,ts)
        if not table(c,"top_mover_audit"):
            c.commit();print(f"Binance missed learning: first_anomaly={new_first}; audit yok");return
        movers=c.execute("""SELECT * FROM top_mover_audit WHERE scan_time_utc=?
          AND audit_status IN ('MISSED','LATE_CAUGHT') ORDER BY current_change_24h DESC""",(ts,)).fetchall()
        snap={r["symbol"]:r for r in c.execute("SELECT * FROM features WHERE scan_time_utc=?",(ts,)).fetchall()}
        mover_set={r["symbol"] for r in movers}
        end=parse_dt(ts); start=(end-timedelta(hours=LOOKBACK_HOURS)).isoformat() if end else ts
        controls_written=0; paths=0
        for m in movers:
            sym=m["symbol"]
            save_path(c,ts,sym,sym,"MOVER",start,ts); paths+=1
            anchor=snap.get(sym)
            if not anchor:continue
            pool=[r for s,r in snap.items() if s not in mover_set and s!=sym
                  and float(r["change_24h"] or 0)<10 and not int(r["is_signal"] or 0)]
            ranked=sorted(((feature_distance(anchor,r),r) for r in pool),key=lambda x:x[0])[:CONTROLS_PER_MOVER]
            for dist,r in ranked:
                cs=r["symbol"]
                c.execute("""INSERT OR IGNORE INTO binance_missed_control_map VALUES(?,?,?,?,?)""",
                          (ts,sym,cs,dist,VERSION))
                save_path(c,ts,sym,cs,"MATCHED_CONTROL",start,ts)
                controls_written+=1
        c.commit()
        print(f"Binance missed learning: movers={len(movers)} paths={paths} controls={controls_written} first_anomaly_new={new_first}")

if __name__=="__main__":main()
