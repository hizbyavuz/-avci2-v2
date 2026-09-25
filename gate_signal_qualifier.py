#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence qualification gate for live Gate candidates.

This is an additive research layer. It never places orders and never changes
frozen V5 membership. It upgrades a current candidate to EVIDENCE_BACKED only
when it matches a validation-proven predeclared activation combo and passes
current safety/data-health checks.
"""
import json, os, sqlite3
from datetime import datetime, timezone
from gate_activation_math import feature_snapshot

OBS_DB=os.getenv("AVCI_DB","avci2.db")
VAL_DB=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
VERSION="gate-signal-qualifier-v1.1-20260926"
PRIMARY_TARGET=10
MIN_SELECTED_N=30
MIN_BASELINE_N=30
MAX_Q=0.05
MIN_LIFT=1.25
MIN_RATE=0.15

def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def validation_rows(c):
    if not table(c,"gate_activation_math_results"): return []
    return c.execute("""SELECT combo,combo_label,target_pct,selected_n,selected_rate,
        baseline_n,baseline_rate,lift,p_value,q_value
        FROM gate_activation_math_results
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

def main():
    if not os.path.exists(OBS_DB) or not os.path.exists(VAL_DB):
        print("Gate qualifier: DB eksik"); return
    with sqlite3.connect(OBS_DB,timeout=30) as obs, sqlite3.connect(VAL_DB,timeout=30) as val:
        obs.row_factory=val.row_factory=sqlite3.Row
        obs.execute("""CREATE TABLE IF NOT EXISTS gate_signal_qualifications(
            batch_id TEXT NOT NULL,validation_id INTEGER NOT NULL,network_id TEXT NOT NULL,
            token_contract TEXT NOT NULL,status TEXT NOT NULL,matched_combo TEXT,
            combo_label TEXT,target_pct INTEGER,selected_n INTEGER,selected_rate REAL,
            baseline_n INTEGER,baseline_rate REAL,lift REAL,q_value REAL,
            data_health TEXT,safety_status TEXT,reason TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,version TEXT NOT NULL,
            PRIMARY KEY(batch_id,validation_id,version))""")
        health=obs.execute("SELECT batch_id,status FROM gate_scan_health ORDER BY scan_ts DESC LIMIT 1").fetchone()
        if not health: print("Gate qualifier: scan yok"); return
        batch=health["batch_id"]; math_rows=validation_rows(obs)
        events=val.execute("""SELECT id,batch_id,network_id,token_contract,signal_ts
            FROM validation_events WHERE batch_id=? AND group_type IN ('CANDIDATE','EXPANDED_CANDIDATE')
            ORDER BY id""",(batch,)).fetchall()
        for e in events:
            feats=feature_snapshot(obs,e)
            matches=[r for r in math_rows if feats.get(r["combo"])]
            match=matches[0] if matches else None
            tier=evidence_tier(match)
            safety="UNKNOWN"
            hard_veto=None
            if table(obs,"gate_security_confidence_history"):
                sec=obs.execute("""SELECT label,hard_veto FROM gate_security_confidence_history
                    WHERE batch_id=? AND network_id=? AND token_contract=?
                    ORDER BY scan_ts DESC LIMIT 1""",(batch,e["network_id"],e["token_contract"])).fetchone()
                if sec:
                    safety=sec["label"] or "UNKNOWN"; hard_veto=sec["hard_veto"]
            if health["status"]!="VALID":
                status="WAIT"; reason="DATA_HEALTH_INVALID"
            elif hard_veto:
                status="REJECT"; reason="SAFETY_HARD_VETO"
            elif tier=="VALIDATED_EDGE":
                status="VALIDATED_EDGE"; reason="LIVE_MATCHES_VALIDATED_ACTIVATION"
            elif tier=="PROVISIONAL_EDGE":
                status="PROVISIONAL_EDGE"; reason="LIVE_MATCHES_PROMISING_NOT_YET_VALIDATED_ACTIVATION"
            else:
                status="RAW_CANDIDATE"; reason="TRACK_AND_LEARN"
            vals=(match if match else {})
            obs.execute("""INSERT OR REPLACE INTO gate_signal_qualifications VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                batch,e["id"],e["network_id"],e["token_contract"],status,
                vals["combo"] if match else None, vals["combo_label"] if match else None,
                vals["target_pct"] if match else None, vals["selected_n"] if match else None,
                vals["selected_rate"] if match else None, vals["baseline_n"] if match else None,
                vals["baseline_rate"] if match else None, vals["lift"] if match else None,
                vals["q_value"] if match else None,health["status"],safety,reason,
                datetime.now(timezone.utc).isoformat(),VERSION))
            print("Gate qualify",e["token_contract"][:10],status,reason,
                  vals["combo"] if match else "-")
        obs.commit()

if __name__=="__main__": main()
