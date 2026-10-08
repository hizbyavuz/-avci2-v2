#!/usr/bin/env python3
"""V4 system-wide research/health audit. No predictive claims without closed events."""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime,timezone
from long_short_v4_research import CONFIG_HASH,VERSION as FEAT_VERSION,setup

def audit(db):
    report={"timestamp_utc":datetime.now(timezone.utc).isoformat(),
            "version":"LS_V4_AUDIT_2026_10_08","feature_config_hash":CONFIG_HASH,
            "frozen_v31_unchanged":True,"orders_enabled":False,
            "telegram_signals_from_v4":False,"raw_events":0,
            "collection_runs":0,"native_market_successes":0,
            "native_market_failures":0,"collection_gaps":0,
            "symbols_with_1m_bars":0,"symbols_with_5m_bars":0,
            "funding_observations":0,"liquidation_snapshots":0,
            "taker_trade_events":0,"book_top_events":0,
            "external_oi_references":0,"feature_snapshots":0,
            "feature_healthy":0,"health_reason_counts":{},
            "regrade_labels":0,"regrade_data_failures":0,
            "regrade_by_direction":{},"release_decision":"NOT_ELIGIBLE",
            "remaining_blockers":[]}
    if not os.path.isfile(db):
        report["remaining_blockers"].append("V4_DATABASE_NOT_YET_CREATED")
        return report
    with sqlite3.connect(db) as c:
        c.row_factory=sqlite3.Row
        setup(c)
        queries={
            "raw_events":"SELECT COUNT(*) FROM raw_events",
            "collection_runs":"SELECT COUNT(*) FROM collection_runs",
            "native_market_successes":"SELECT COUNT(*) FROM collection_runs WHERE state='OBSERVED' AND market_connected=1",
            "native_market_failures":"SELECT COUNT(*) FROM collection_runs WHERE state='DATA_FAILURE' OR market_connected=0",
            "collection_gaps":"SELECT COUNT(*) FROM data_issues WHERE code='COLLECTION_GAP'",
            "symbols_with_1m_bars":"SELECT COUNT(DISTINCT symbol) FROM closed_klines WHERE interval='1m'",
            "symbols_with_5m_bars":"SELECT COUNT(DISTINCT symbol) FROM closed_klines WHERE interval='5m'",
            "funding_observations":"SELECT COUNT(*) FROM marks WHERE funding_rate IS NOT NULL",
            "liquidation_snapshots":"SELECT COUNT(*) FROM liquidations",
            "taker_trade_events":"SELECT COUNT(*) FROM taker_trades",
            "book_top_events":"SELECT COUNT(*) FROM book_top",
            "external_oi_references":"SELECT COUNT(*) FROM external_derivatives",
            "feature_snapshots":"SELECT COUNT(*) FROM feature_snapshots",
            "feature_healthy":"SELECT COUNT(*) FROM feature_snapshots WHERE health='OBSERVATION_ONLY'",
            "regrade_labels":"SELECT COUNT(*) FROM paper_regrades WHERE status='LABELED'",
            "regrade_data_failures":"SELECT COUNT(*) FROM paper_regrades WHERE status='DATA_FAILURE'",
        }
        for key,sql in queries.items():
            report[key]=c.execute(sql).fetchone()[0]
        reasons=Counter()
        for row in c.execute("SELECT errors_json FROM feature_snapshots WHERE version=?",(FEAT_VERSION,)):
            for reason in json.loads(row[0] or "[]"):
                reasons[reason]+=1
        report["health_reason_counts"]=dict(reasons)
        for row in c.execute("""SELECT direction,COUNT(*) n,
              SUM(CASE WHEN status='LABELED' THEN 1 ELSE 0 END) labeled
              FROM paper_regrades GROUP BY direction"""):
            report["regrade_by_direction"][row[0]]={"total":row[1],"labeled":row[2]}
        # Labels contain separate 15m/60m/180m observations; DO NOT count them
        # as independent coin setups, and DO NOT infer PnL net of unknown funding.
        episodes=c.execute("""SELECT COUNT(DISTINCT event_key) FROM paper_regrades
          WHERE status='LABELED' AND horizon_min=60""").fetchone()[0]
        report["unique_60m_events"]=episodes
        report["minimum_independent_episodes_required"]=150
        if episodes<150:report["remaining_blockers"].append("INSUFFICIENT_INDEPENDENT_EVENTS")
        report["remaining_blockers"].append("NO_BINANCE_NATIVE_OI_WEBSOCKET")
        report["remaining_blockers"].append("SCHEDULED_GITHUB_RUNNER_NOT_CONTINUOUS")
        report["remaining_blockers"].append("FUNDING_AND_REAL_EXECUTION_NOT_FULLY_VALIDATED")
        report["remaining_blockers"].append("MODEL_EDGE_UNPROVEN_ON_HOLDOUT")
        report["release_decision"]="RESEARCH_ONLY"
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="long_short_v4_native.db")
    p.add_argument("--out",default="long_short_v4_audit.json")
    args=p.parse_args()
    result=audit(args.db)
    with open(args.out,"w",encoding="utf-8") as f:json.dump(result,f,indent=2,ensure_ascii=False)
    print(json.dumps({k:result[k] for k in ("raw_events","collection_runs",
            "feature_snapshots","regrade_labels","release_decision")},ensure_ascii=False))

if __name__=="__main__":main()
