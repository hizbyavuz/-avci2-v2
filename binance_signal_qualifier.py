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
VERSION="binance-signal-qualifier-v1-20260926"
PRIMARY_TARGET=10
MIN_SELECTED_N=30
MIN_BASELINE_N=30
MAX_Q=0.05
MIN_LIFT=1.25
MIN_RATE=0.15

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def proven(c):
    if not table(c,"binance_activation_math_results"): return []
    return c.execute("""SELECT combo,combo_label,target_pct,selected_n,selected_rate,
        baseline_n,baseline_rate,lift,q_value
        FROM binance_activation_math_results
        WHERE split='VALIDATION' AND target_pct=?
          AND selected_n>=? AND baseline_n>=?
          AND q_value IS NOT NULL AND q_value<=?
          AND lift IS NOT NULL AND lift>=?
          AND selected_rate IS NOT NULL AND selected_rate>=?
        ORDER BY q_value ASC,lift DESC""",
        (PRIMARY_TARGET,MIN_SELECTED_N,MIN_BASELINE_N,MAX_Q,MIN_LIFT,MIN_RATE)).fetchall()

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
        good=proven(c)
        for feat in rows:
            flow=c.execute("""SELECT * FROM flow_observations WHERE scan_time_utc=? AND symbol=?
                ORDER BY rowid DESC LIMIT 1""",(scan["scan_time_utc"],feat["symbol"])).fetchone() if table(c,"flow_observations") else None
            opp=c.execute("""SELECT * FROM opportunity_observations WHERE scan_time_utc=? AND symbol=?
                ORDER BY version DESC LIMIT 1""",(scan["scan_time_utc"],feat["symbol"])).fetchone() if table(c,"opportunity_observations") else None
            flags=current_flags(c,feat,flow,opp)
            match=next((r for r in good if flags.get(r["combo"])),None)
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
            elif not good:
                status="RESEARCH"; reason="NO_VALIDATION_PROVEN_COMBO_YET"
            elif not match:
                status="RESEARCH"; reason="CURRENT_CANDIDATE_DOES_NOT_MATCH_PROVEN_COMBO"
            else:
                status="EVIDENCE_BACKED"; reason="LIVE_MATCHES_VALIDATION_PROVEN_ACTIVATION"
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
