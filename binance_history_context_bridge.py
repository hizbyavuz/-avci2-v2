#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auxiliary Binance historical-context bridge.

Combines:
- Winner Anatomy similarity vs matched controls,
- missed-mover prehistory vs matched controls,
- first-anomaly timing.

Observation-only. It never changes candidate membership, evidence_count,
Decision Quality families, signal labels, or Capital Trust.
"""
import json, math, os, sqlite3, statistics
from datetime import datetime, timezone

DB=os.getenv("BINANCE_DB","binance_avci2.db")
VERSION="binance-history-context-v1-20260927"
MIN_CLASS_EVENTS=5

FEATURES=(
  ("change_15m","change_15m",2.0),
  ("change_1h","change_1h",4.0),
  ("change_24h","change_24h",10.0),
  ("volume_mult_15m","volume_mult_15m",2.0),
  ("volume_mult_1h","volume_mult_1h",2.0),
  ("volume_z_15m","volume_z_15m",2.0),
  ("trade_z_15m","trade_z_15m",2.0),
  ("return_z_15m","return_z_15m",2.0),
  ("taker_buy_ratio_15m","taker_buy_ratio_15m",0.20),
  ("retention_proxy","retention_proxy",0.25),
  ("cross_sectional_rarity_pct","cross_sectional_rarity_pct",20.0),
  ("btc_relative_24h","btc_relative_24h",5.0),
)

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def f(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception:return None

def med(xs):
    ys=[f(x) for x in xs];ys=[x for x in ys if x is not None]
    return statistics.median(ys) if ys else None

def robust_scale(xs,fallback):
    ys=[f(x) for x in xs];ys=[x for x in ys if x is not None]
    if len(ys)<2:return fallback
    m=statistics.median(ys)
    mad=statistics.median([abs(x-m) for x in ys])
    if mad>1e-9:return max(1.4826*mad,fallback*0.25)
    sd=statistics.pstdev(ys)
    return max(sd,fallback*0.25) if sd>0 else fallback

def similarity(current,movers,controls):
    mover_events={(r["audit_scan_utc"],r["mover_symbol"]) for r in movers}
    ctrl_events={(r["audit_scan_utc"],r["mover_symbol"],r["subject_symbol"]) for r in controls}
    if len(mover_events)<MIN_CLASS_EVENTS or len(ctrl_events)<MIN_CLASS_EVENTS:
        return None
    dm=dc=0.0;w=0
    details=[]
    for live,hist,fallback in FEATURES:
        cv=f(current[live]) if live in current.keys() else None
        if cv is None:continue
        mv=[r[hist] for r in movers if hist in r.keys() and r[hist] is not None]
        ct=[r[hist] for r in controls if hist in r.keys() and r[hist] is not None]
        mm=med(mv);cm=med(ct)
        if mm is None or cm is None:continue
        sc=robust_scale(mv+ct,fallback)
        md=min(6.0,abs(cv-mm)/sc);cd=min(6.0,abs(cv-cm)/sc)
        dm+=md;dc+=cd;w+=1
        details.append({"feature":live,"current":cv,"mover_median":mm,"control_median":cm,
                        "mover_edge":cd-md})
    if w<4:return None
    md=dm/w;cd=dc/w
    sim=50+50*math.tanh((cd-md)/2.0)
    fit=100*math.exp(-min(md,cd)/3.0)
    strongest=sorted(details,key=lambda x:x["mover_edge"],reverse=True)[:4]
    return {"similarity_pct":max(0,min(100,sim)),"fit_pct":max(0,min(100,fit)),
            "mover_events":len(mover_events),"control_events":len(ctrl_events),
            "features":w,"strongest":strongest}

def ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS binance_history_context(
      scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,
      winner_classification TEXT,winner_similarity_pct REAL,winner_fit_pct REAL,
      winner_n INTEGER,winner_control_n INTEGER,
      missed_similarity_pct REAL,missed_fit_pct REAL,missed_mover_n INTEGER,
      missed_control_n INTEGER,missed_features INTEGER,
      first_anomaly_time_utc TEXT,first_anomaly_price REAL,gain_from_first_anomaly_pct REAL,
      context_status TEXT NOT NULL,details_json TEXT NOT NULL,
      version TEXT NOT NULL,created_at_utc TEXT NOT NULL,
      PRIMARY KEY(scan_time_utc,symbol,version)
    )""")

def current_subjects(c,ts,config):
    rows={}
    for r in c.execute("""SELECT * FROM features WHERE scan_time_utc=? AND config_version=?
      AND COALESCE(climax_risk,0)=0""",(ts,config)):
        if r["selection_class"]=="CANDIDATE":
            rows[r["symbol"]]=r
    if table(c,"opportunity_observations"):
        cols={x[1] for x in c.execute("PRAGMA table_info(opportunity_observations)")}
        if "early_watch" in cols:
            for r in c.execute("""SELECT f.* FROM features f JOIN opportunity_observations o
              ON o.scan_time_utc=f.scan_time_utc AND o.symbol=f.symbol
              WHERE f.scan_time_utc=? AND f.config_version=? AND o.early_watch=1
                AND COALESCE(f.climax_risk,0)=0""",(ts,config)):
                rows[r["symbol"]]=r
    return list(rows.values())

def main():
    if not os.path.exists(DB):print("Binance history context: DB yok");return
    with sqlite3.connect(DB,timeout=60) as c:
        c.row_factory=sqlite3.Row;ensure(c)
        scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID'
          ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:print("Binance history context: scan yok");return
        ts=scan["scan_time_utc"];subjects=current_subjects(c,ts,scan["config_version"])
        movers=controls=[]
        if table(c,"binance_missed_prehistory"):
            movers=c.execute("""SELECT * FROM binance_missed_prehistory
              WHERE subject_class='MOVER' AND offset_minutes BETWEEN -120 AND 0""").fetchall()
            controls=c.execute("""SELECT * FROM binance_missed_prehistory
              WHERE subject_class='MATCHED_CONTROL' AND offset_minutes BETWEEN -120 AND 0""").fetchall()
        out=0
        for cur in subjects:
            sym=cur["symbol"]
            wb=c.execute("""SELECT * FROM winner_bridge_scores WHERE scan_time_utc=? AND symbol=?
              ORDER BY id DESC LIMIT 1""",(ts,sym)).fetchone() if table(c,"winner_bridge_scores") else None
            ms=similarity(cur,movers,controls) if movers and controls else None
            fa=c.execute("""SELECT * FROM binance_first_anomaly_registry WHERE symbol=?""",(sym,)).fetchone()                if table(c,"binance_first_anomaly_registry") else None
            gain=None
            if fa and f(fa["first_price"]) and f(cur["price"]):
                gain=100*(float(cur["price"])/float(fa["first_price"])-1)
            winner_support=bool(wb and (wb["classification"]=="KAZANANA_BENZER")
                                and float(wb["reference_fit_pct"] or 0)>=35
                                and int(wb["winner_sample_count"] or 0)>=5
                                and int(wb["control_sample_count"] or 0)>=5)
            missed_support=bool(ms and ms["similarity_pct"]>=65 and ms["fit_pct"]>=35)
            if winner_support and missed_support:status="BOTH_SUPPORT"
            elif winner_support:status="WINNER_ANATOMY_SUPPORT"
            elif missed_support:status="MISSED_MOVER_SUPPORT"
            elif wb or ms:status="MIXED_OR_WEAK"
            else:status="INSUFFICIENT_HISTORY"
            details={"winner_bridge":dict(wb) if wb else None,"missed_mover":ms,
                     "note":"AUXILIARY_ONLY_NO_SCORING"}
            c.execute("""INSERT OR REPLACE INTO binance_history_context VALUES(
              ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
              ts,sym,wb["classification"] if wb else None,
              f(wb["winner_similarity_pct"]) if wb else None,
              f(wb["reference_fit_pct"]) if wb else None,
              int(wb["winner_sample_count"] or 0) if wb else 0,
              int(wb["control_sample_count"] or 0) if wb else 0,
              ms["similarity_pct"] if ms else None,ms["fit_pct"] if ms else None,
              ms["mover_events"] if ms else 0,ms["control_events"] if ms else 0,
              ms["features"] if ms else 0,
              fa["first_seen_utc"] if fa else None,f(fa["first_price"]) if fa else None,gain,
              status,json.dumps(details,ensure_ascii=False,default=str),VERSION,
              datetime.now(timezone.utc).isoformat()))
            out+=1
        c.commit();print(f"Binance history context: subjects={out}")

if __name__=="__main__":main()
