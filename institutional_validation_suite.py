#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Institutional validation suite for Avci.

Research-only diagnostics:
- random-time placebo on Binance full-universe scans
- rolling feature predictive-power decay
- version-isolation audit
- security model decay history
No frozen signal thresholds are changed.
"""
from __future__ import annotations
import json, math, os, random, sqlite3, statistics, sys
from datetime import datetime, timezone, timedelta

VERSION="institutional-validation-v1-20260926"

def now():return datetime.now(timezone.utc).isoformat()
def table(c,t):return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def cols(c,t):return {r[1] for r in c.execute(f"PRAGMA table_info({t})")} if table(c,t) else set()
def dt(x):
    try:return datetime.fromisoformat(str(x).replace("Z","+00:00"))
    except Exception:return None
def mean(xs):return sum(xs)/len(xs) if xs else None
def corr(x,y):
    if len(x)<8:return None
    mx=mean(x);my=mean(y);vx=sum((a-mx)**2 for a in x);vy=sum((b-my)**2 for b in y)
    if vx<=0 or vy<=0:return None
    return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(vx*vy)

def init(c):
    c.executescript("""CREATE TABLE IF NOT EXISTS placebo_tests(
      source TEXT,version TEXT,run_utc TEXT,test_name TEXT,n INTEGER,
      candidate_expectancy REAL,placebo_expectancy REAL,diff REAL,status TEXT,details_json TEXT,
      PRIMARY KEY(source,version,test_name));
    CREATE TABLE IF NOT EXISTS feature_predictive_decay(
      source TEXT,version TEXT,run_date TEXT,feature TEXT,window_days INTEGER,n INTEGER,
      predictive_corr REAL,success_rate REAL,status TEXT,
      PRIMARY KEY(source,version,run_date,feature,window_days));
    CREATE TABLE IF NOT EXISTS version_isolation_audit(
      source TEXT,version TEXT,run_utc TEXT,config_versions INTEGER,
      mixed_version_holdout_rows INTEGER,status TEXT,details_json TEXT,
      PRIMARY KEY(source,version));
    CREATE TABLE IF NOT EXISTS security_model_decay_history(
      source TEXT,version TEXT,run_date TEXT,bad_matched INTEGER,good_matched INTEGER,
      recall REAL,false_positive_rate REAL,status TEXT,
      PRIMARY KEY(source,version,run_date));
    """)

def binance(c):
    init(c)
    # Version isolation: each config must be assessed separately; flag mixed prospective rows.
    versions=[r[0] for r in c.execute("SELECT DISTINCT config_version FROM signal_events WHERE config_version IS NOT NULL")]
    hold="2026-10-07T00:00:00+00:00"
    mixed=c.execute("""SELECT COUNT(*) FROM signal_events WHERE signal_time_utc>=?
      AND config_version IS NOT NULL AND config_version<>(SELECT config_version FROM signal_events
      WHERE signal_time_utc>=? AND config_version IS NOT NULL ORDER BY signal_time_utc LIMIT 1)""",(hold,hold)).fetchone()[0] if table(c,"signal_events") else 0
    status="PASS" if mixed==0 else "FAIL_MIXED_VERSION_HOLDOUT"
    c.execute("INSERT OR REPLACE INTO version_isolation_audit VALUES(?,?,?,?,?,?,?)",
              ("BINANCE",VERSION,now(),len(versions),mixed,status,json.dumps({"versions":versions})))

    # Feature predictive-power decay using closed candidate outcomes and signal-time feature snapshot.
    if table(c,"outcome_labels") and table(c,"features"):
        features=["volume_z_15m","trade_z_15m","return_z_15m","retention_proxy",
                  "taker_buy_ratio_15m","btc_relative_24h","cross_sectional_rarity_pct"]
        today=datetime.now(timezone.utc)
        for w in (30,60,90):
            cutoff=(today-timedelta(days=w)).isoformat()
            rows=c.execute("""SELECT f.*,o.net_return_pct FROM signal_events s
              JOIN outcome_labels o ON o.event_id=s.event_id
              JOIN features f ON f.scan_time_utc=s.signal_time_utc AND f.symbol=s.symbol
              WHERE s.event_class='CANDIDATE' AND o.label_status='CLOSED'
                AND s.signal_time_utc>=? AND o.net_return_pct IS NOT NULL""",(cutoff,)).fetchall()
            y=[1.0 if float(r["net_return_pct"])>0 else 0.0 for r in rows]
            for name in features:
                xy=[(float(r[name]),yy) for r,yy in zip(rows,y) if r[name] is not None]
                x=[a for a,b in xy]; yy=[b for a,b in xy]
                pc=corr(x,yy) if len(x)>=8 else None
                c.execute("INSERT OR REPLACE INTO feature_predictive_decay VALUES(?,?,?,?,?,?,?,?,?)",
                  ("BINANCE",VERSION,today.date().isoformat(),name,w,len(x),pc,mean(yy) if yy else None,
                   "OK" if pc is not None else "INSUFFICIENT"))

    # Scan-time random placebo: random-control events are predeclared controls;
    # also compare to random NONE rows with 72h nearest-scan return where possible.
    cand=c.execute("""SELECT o.net_return_pct FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
      WHERE s.event_class='CANDIDATE' AND o.label_status='CLOSED' AND o.net_return_pct IS NOT NULL""").fetchall() if table(c,"outcome_labels") else []
    ctrl=c.execute("""SELECT o.net_return_pct FROM signal_events s JOIN outcome_labels o ON o.event_id=s.event_id
      WHERE s.event_class='RANDOM_CONTROL' AND o.label_status='CLOSED' AND o.net_return_pct IS NOT NULL""").fetchall() if table(c,"outcome_labels") else []
    cv=[float(r[0]) for r in cand];pv=[float(r[0]) for r in ctrl]
    c.execute("INSERT OR REPLACE INTO placebo_tests VALUES(?,?,?,?,?,?,?,?,?,?)",
      ("BINANCE",VERSION,now(),"PREDECLARED_RANDOM_CONTROL",len(pv),mean(cv),mean(pv),
       (mean(cv)-mean(pv)) if cv and pv else None,
       "OK" if len(pv)>=8 else "INSUFFICIENT",
       json.dumps({"note":"Random controls are selected contemporaneously at scan time; this is the primary placebo/control test."})))
    c.commit()

def gate(c,v):
    init(c)
    versions=[r[0] for r in v.execute("SELECT DISTINCT config_version FROM validation_events WHERE config_version IS NOT NULL")]
    hold_ts=int(datetime.fromisoformat("2026-10-07T00:00:00+00:00").timestamp())
    first=v.execute("SELECT config_version FROM validation_events WHERE signal_ts>=? ORDER BY signal_ts LIMIT 1",(hold_ts,)).fetchone()
    mixed=0
    if first:
        mixed=v.execute("SELECT COUNT(*) FROM validation_events WHERE signal_ts>=? AND config_version<>?",(hold_ts,first[0])).fetchone()[0]
    c.execute("INSERT OR REPLACE INTO version_isolation_audit VALUES(?,?,?,?,?,?,?)",
      ("GATE",VERSION,now(),len(versions),mixed,"PASS" if mixed==0 else "FAIL_MIXED_VERSION_HOLDOUT",json.dumps({"versions":versions})))

    # Security decay history from latest red-team replay tables.
    if table(c,"external_security_replay"):
        rows=c.execute("SELECT label,matched,detected FROM external_security_replay").fetchall()
        bad=[r for r in rows if str(r["label"]).upper()=="BAD" and int(r["matched"] or 0)]
        good=[r for r in rows if str(r["label"]).upper()=="GOOD" and int(r["matched"] or 0)]
        recall=(sum(int(r["detected"] or 0) for r in bad)/len(bad)) if bad else None
        fpr=(sum(int(r["detected"] or 0) for r in good)/len(good)) if good else None
        c.execute("INSERT OR REPLACE INTO security_model_decay_history VALUES(?,?,?,?,?,?,?,?)",
          ("GATE",VERSION,datetime.now(timezone.utc).date().isoformat(),len(bad),len(good),recall,fpr,
           "OK" if len(bad)>=30 and len(good)>=30 else "REPLAY_INSUFFICIENT"))

    cand=v.execute("""SELECT net_final_pct FROM validation_events WHERE status='CLOSED_72H'
      AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE') AND net_final_pct IS NOT NULL""").fetchall()
    ctrl=v.execute("""SELECT net_final_pct FROM validation_events WHERE status='CLOSED_72H'
      AND group_type='RANDOM_CONTROL' AND net_final_pct IS NOT NULL""").fetchall()
    cv=[float(r[0]) for r in cand];pv=[float(r[0]) for r in ctrl]
    c.execute("INSERT OR REPLACE INTO placebo_tests VALUES(?,?,?,?,?,?,?,?,?,?)",
      ("GATE",VERSION,now(),"PREDECLARED_RANDOM_CONTROL",len(pv),mean(cv),mean(pv),
       (mean(cv)-mean(pv)) if cv and pv else None,"OK" if len(pv)>=8 else "INSUFFICIENT",
       json.dumps({"note":"Matched contemporaneous random controls; arbitrary-time placebo remains a separate future diagnostic."})))
    c.commit()

def main():
    m=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if m=="binance":
        db=os.getenv("BINANCE_DB","binance_avci2.db")
        if os.path.exists(db):
            with sqlite3.connect(db,timeout=60) as c:c.row_factory=sqlite3.Row;binance(c)
    elif m=="gate":
        db=os.getenv("AVCI_DB","avci2.db");vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
        if os.path.exists(db) and os.path.exists(vdb):
            with sqlite3.connect(db,timeout=60) as c,sqlite3.connect(vdb,timeout=60) as v:
                c.row_factory=v.row_factory=sqlite3.Row;gate(c,v)
    else:raise SystemExit("usage: institutional_validation_suite.py binance|gate")
if __name__=="__main__":main()
