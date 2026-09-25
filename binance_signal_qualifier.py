#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence qualification gate for live Binance candidates.

Research-only. Never sends orders and never changes scanner thresholds.
A live candidate becomes EVIDENCE_BACKED only if it matches a predeclared
activation combo that passed validation and current execution/data checks.
"""
import json, os, sqlite3
from datetime import datetime, timezone

DB=os.getenv("BINANCE_DB","binance_avci2.db")
VERSION="binance-signal-qualifier-v1.1-20260926"
PRIMARY_TARGET=10
MIN_SELECTED_N=30
MIN_BASELINE_N=30
MAX_Q=0.05
MIN_LIFT=1.25
MIN_RATE=0.15

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def validation_rows(c):
    if not table(c,"binance_activation_math_results"): return []
    return c.execute("""SELECT combo,combo_label,target_pct,selected_n,selected_rate,
        baseline_n,baseline_rate,lift,p_value,q_value
        FROM binance_activation_math_results
        WHERE split='VALIDATION' AND target_pct=?
        ORDER BY CASE WHEN q_value IS NULL THEN 1 ELSE 0 END,q_value ASC,lift DESC""",
        (PRIMARY_TARGET,)).fetchall()

def evidence_tier(row):
    if not row:
        return "RAW_CANDIDATE"
    if (row["selected_n"] >= MIN_SELECTED_N and row["baseline_n"] >= MIN_BASELINE_N
            and row["q_value"] is not None and row["q_value"] <= MAX_Q
            and row["lift"] is not None and row["lift"] >= MIN_LIFT
            and row["selected_rate"] is not None and row["selected_rate"] >= MIN_RATE):
        return "VALIDATED_EDGE"
    if (row["selected_n"] >= 8 and row["baseline_n"] >= 8
            and row["lift"] is not None and row["lift"] >= 1.15
            and row["selected_rate"] is not None and row["baseline_rate"] is not None
            and row["selected_rate"] > row["baseline_rate"]):
        return "PROVISIONAL_EDGE"
    return "RAW_CANDIDATE"

def current_flags(c,feat,flow,opp):
    book=None
    if flow and flow["book_imbalance"] is not None:
        book=float(flow["book_imbalance"])
    taker=float(feat["taker_buy_ratio_15m"] or 0)
    rarity=float(feat["cross_sectional_rarity_pct"] or 0)
    return {
        "B1_WAKE_RET":bool(feat["wakeup"]) and bool(feat["retention"]),
        "B2_WAKE_RET_TAKER":bool(feat["wakeup"]) and bool(feat["retention"]) and taker>=0.55,
        "B3_REIGNITION_RARITY":bool(feat["reignition"]) and rarity>=75,
        "B4_TRIGGER_FLOW":bool(feat["trigger"]) and taker>=0.55 and book is not None and book>=0.05,
        "B5_SILENT_ACCUM":bool(opp and opp["silent_accumulation"]),
    }

def main():
    if not os.path.exists(DB):
        print("Binance qualifier: DB yok"); return
    with sqlite3.connect(DB,timeout=30) as c:
        c.row_factory=sqlite3.Row
        c.execute("""CREATE TABLE IF NOT EXISTS binance_signal_qualifications(
            scan_time_utc TEXT NOT NULL,symbol TEXT NOT NULL,status TEXT NOT NULL,
            matched_combo TEXT,combo_label TEXT,target_pct INTEGER,selected_n INTEGER,
            selected_rate REAL,baseline_n INTEGER,baseline_rate REAL,lift REAL,q_value REAL,
            data_mode TEXT,health_status TEXT,execution_ok INTEGER,climax_risk INTEGER,
            reason TEXT NOT NULL,created_at_utc TEXT NOT NULL,version TEXT NOT NULL,
            PRIMARY KEY(scan_time_utc,symbol,version))""")
        scan=c.execute("""SELECT * FROM scans WHERE health_status!='INVALID'
            ORDER BY scan_time_utc DESC LIMIT 1""").fetchone()
        if not scan:
            print("Binance qualifier: valid scan yok"); return
        rows=c.execute("""SELECT * FROM features WHERE scan_time_utc=?
            AND selection_class='CANDIDATE' ORDER BY score DESC""",(scan["scan_time_utc"],)).fetchall()
        math_rows=validation_rows(c)
        for feat in rows:
            flow=c.execute("""SELECT * FROM flow_observations WHERE scan_time_utc=? AND symbol=?
                ORDER BY rowid DESC LIMIT 1""",(scan["scan_time_utc"],feat["symbol"])).fetchone() if table(c,"flow_observations") else None
            opp=c.execute("""SELECT * FROM opportunity_observations WHERE scan_time_utc=? AND symbol=?
                ORDER BY version DESC LIMIT 1""",(scan["scan_time_utc"],feat["symbol"])).fetchone() if table(c,"opportunity_observations") else None
            flags=current_flags(c,feat,flow,opp)
            matches=[r for r in math_rows if flags.get(r["combo"])]
            match=matches[0] if matches else None
            tier=evidence_tier(match)
            # Candidate selection already includes spread/impact filters. Re-check latest event snapshot.
            ev=c.execute("""SELECT spread_bps,buy_impact_1k_bps,buy_impact_5k_bps
                FROM signal_events WHERE symbol=? AND event_class='CANDIDATE'
                AND signal_time_utc<=? ORDER BY signal_time_utc DESC LIMIT 1""",
                (feat["symbol"],scan["scan_time_utc"])).fetchone()
            execution_ok=1
            if ev:
                if ev["spread_bps"] is not None and float(ev["spread_bps"])>30: execution_ok=0
                if ev["buy_impact_1k_bps"] is not None and float(ev["buy_impact_1k_bps"])>35: execution_ok=0
                if ev["buy_impact_5k_bps"] is not None and float(ev["buy_impact_5k_bps"])>100: execution_ok=0
            if scan["health_status"]=="INVALID":
                status="WAIT"; reason="DATA_HEALTH_INVALID"
            elif int(feat["climax_risk"] or 0):
                status="REJECT"; reason="CLIMAX_RISK"
            elif not execution_ok:
                status="REJECT"; reason="EXECUTION_TOO_EXPENSIVE"
            elif tier=="VALIDATED_EDGE":
                status="VALIDATED_EDGE"; reason="LIVE_MATCHES_VALIDATED_ACTIVATION"
            elif tier=="PROVISIONAL_EDGE":
                status="PROVISIONAL_EDGE"; reason="LIVE_MATCHES_PROMISING_NOT_YET_VALIDATED_ACTIVATION"
            else:
                status="RAW_CANDIDATE"; reason="TRACK_AND_LEARN"
            vals=match if match else {}
            c.execute("""INSERT OR REPLACE INTO binance_signal_qualifications VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                scan["scan_time_utc"],feat["symbol"],status,
                vals["combo"] if match else None,vals["combo_label"] if match else None,
                vals["target_pct"] if match else None,vals["selected_n"] if match else None,
                vals["selected_rate"] if match else None,vals["baseline_n"] if match else None,
                vals["baseline_rate"] if match else None,vals["lift"] if match else None,
                vals["q_value"] if match else None,feat["data_mode"],scan["health_status"],
                execution_ok,int(feat["climax_risk"] or 0),reason,
                datetime.now(timezone.utc).isoformat(),VERSION))
            print("Binance qualify",feat["symbol"],status,reason,vals["combo"] if match else "-")
        c.commit()

if __name__=="__main__": main()
