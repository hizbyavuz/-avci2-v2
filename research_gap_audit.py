#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operational gap audit for Avci research governance.

Research-only. Never changes scanner thresholds, candidate membership, scores,
or execution logic. It converts known methodological/data limitations into
machine-readable statuses so unavailable evidence cannot be silently forgotten.
"""
from __future__ import annotations
import csv, json, os, sqlite3, sys
from datetime import datetime, timezone

VERSION="research-gap-audit-v1-20260926"
HOLDOUT_START="2026-10-07T00:00:00+00:00"

def now(): return datetime.now(timezone.utc)
def table(c,t):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def scalar(c,sql,args=()):
    try:
        r=c.execute(sql,args).fetchone()
        return r[0] if r else None
    except sqlite3.Error:
        return None

def labelled_security_rows(path):
    if not path or not os.path.exists(path): return (0,0)
    bad=good=0
    try:
        with open(path,newline="",encoding="utf-8") as f:
            for r in csv.DictReader(f):
                x=(r.get("label") or "").upper()
                if x=="BAD": bad+=1
                elif x=="GOOD": good+=1
    except Exception:
        pass
    return bad,good

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS research_gap_audit(
      source TEXT NOT NULL, version TEXT NOT NULL, audited_at_utc TEXT NOT NULL,
      overall_status TEXT NOT NULL, gaps_json TEXT NOT NULL,
      PRIMARY KEY(source,version)
    )""")
    c.commit()

def common(c,source):
    gaps={}
    holdout_started=now()>=datetime.fromisoformat(HOLDOUT_START)
    gaps["prospective_genesis_holdout"]={
      "status":"COLLECTING" if holdout_started else "WAITING_TIME",
      "start_utc":HOLDOUT_START,
      "retroactive_reconstruction":False
    }
    latency=scalar(c,"SELECT COUNT(*) FROM research_source_latency") if table(c,"research_source_latency") else 0
    gaps["latency_and_data_age"]={
      "status":"FORWARD_COLLECTION_ACTIVE" if latency else "WAITING_FORWARD_OBSERVATIONS",
      "rows":int(latency or 0),
      "historical_backfill":"IMPOSSIBLE_RETROACTIVELY"
    }
    fills=scalar(c,"SELECT COUNT(*) FROM execution_fill_observations WHERE source=?",(source,)) if table(c,"execution_fill_observations") else 0
    gaps["real_fill_execution"]={
      "status":"OBSERVED" if fills else "WAITING_INDEPENDENT_READ_ONLY_FILLS",
      "rows":int(fills or 0),
      "synthetic_realized_cost_allowed":False
    }
    gaps["power_and_optional_stopping"]={
      "status":"PREDECLARED_DECISION_CONTRACT_FROZEN",
      "minimum_economic_candidate_control_expectancy_diff_pct":0.50,
      "alpha_two_sided":0.05,
      "target_power":0.80,
      "sequential_checkpoints_effective_n":[50,100,200],
      "rule":"Do not change the MDE/checkpoints after genesis holdout begins and do not resize from observed uplift."
    }
    return gaps

def binance():
    db=os.getenv("BINANCE_DB","binance_avci2.db")
    if not os.path.exists(db): return
    with sqlite3.connect(db,timeout=30) as c:
        init(c); gaps=common(c,"BINANCE")
        unresolved=scalar(c,"SELECT COUNT(*) FROM data_issues") if table(c,"data_issues") else 0
        gaps["survivorship_and_data_failures"]={
          "status":"EXPLICITLY_RETAINED" if table(c,"data_issues") else "TABLE_NOT_AVAILABLE",
          "recorded_issues":int(unresolved or 0)
        }
        overall="PAPER_ONLY_WAITING_EVIDENCE"
        payload={"source":"BINANCE","version":VERSION,"audited_at_utc":now().isoformat(),
                 "overall_status":overall,"gaps":gaps}
        c.execute("INSERT OR REPLACE INTO research_gap_audit VALUES(?,?,?,?,?)",
                  ("BINANCE",VERSION,payload["audited_at_utc"],overall,json.dumps(gaps,sort_keys=True)))
        c.commit()
    open("research_gap_status_binance.json","w",encoding="utf-8").write(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(payload,ensure_ascii=False,indent=2))

def gate():
    db=os.getenv("AVCI_DB","avci2.db")
    vdb=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
    hdb=os.getenv("HISTORY_DB",".history-state/history_miner.db")
    corpus=os.getenv("SECURITY_CORPUS","security_adversarial_corpus.csv")
    if not os.path.exists(db): return
    with sqlite3.connect(db,timeout=30) as c:
        init(c); gaps=common(c,"GATE")
        unresolved=None
        if os.path.exists(vdb):
            with sqlite3.connect(vdb) as v:
                if table(v,"validation_events"):
                    unresolved=scalar(v,"SELECT COUNT(*) FROM validation_events WHERE status<>'CLOSED_72H'")
        gaps["validation_outcomes"]={
          "status":"EXPLICIT" if unresolved is not None else "VALIDATION_DB_UNAVAILABLE",
          "unresolved":None if unresolved is None else int(unresolved)
        }
        if os.path.exists(hdb):
            with sqlite3.connect(hdb) as h:
                if table(h,"historical_pair_registry"):
                    total=scalar(h,"SELECT COUNT(*) FROM historical_pair_registry") or 0
                    pending=scalar(h,"SELECT COUNT(*) FROM historical_pair_registry WHERE coverage_status IN ('QUEUED','RETRY','NO_ARCHIVE','SHORT_HISTORY')") or 0
                    gaps["historical_survivorship_coverage"]={
                      "status":"PARTIAL_EXTERNAL_COVERAGE" if pending else "KNOWN_REGISTRY_COVERED",
                      "known_pairs":int(total),"unresolved_or_uncovered":int(pending),
                      "full_historical_universe_guaranteed":False
                    }
                else:
                    gaps["historical_survivorship_coverage"]={"status":"REGISTRY_NOT_AVAILABLE"}
        else:
            gaps["historical_survivorship_coverage"]={"status":"EXTERNAL_HISTORY_DB_NOT_MOUNTED"}
        bad,good=labelled_security_rows(corpus)
        replay_status=None; bad_matched=good_matched=0
        if os.path.exists("security_redteam_report.json"):
            try:
                rr=json.load(open("security_redteam_report.json",encoding="utf-8"))
                replay_status=rr.get("status")
                bad_matched=int(rr.get("bad_matched") or 0)
                good_matched=int(rr.get("good_matched") or 0)
            except Exception:
                pass
        gaps["security_ground_truth"]={
          "status":("READY_REPLAY_VALIDATED" if bad_matched>=30 and good_matched>=30
                    else "CORPUS_READY_REPLAY_PENDING" if bad>=30 and good>=30
                    else "WAITING_INDEPENDENT_GROUND_TRUTH"),
          "bad_labels":bad,"good_controls":good,"target_each":30,
          "bad_matched":bad_matched,"good_matched":good_matched,
          "redteam_status":replay_status
        }
        overall="PAPER_ONLY_WAITING_EVIDENCE"
        payload={"source":"GATE","version":VERSION,"audited_at_utc":now().isoformat(),
                 "overall_status":overall,"gaps":gaps}
        c.execute("INSERT OR REPLACE INTO research_gap_audit VALUES(?,?,?,?,?)",
                  ("GATE",VERSION,payload["audited_at_utc"],overall,json.dumps(gaps,sort_keys=True)))
        c.commit()
    open("research_gap_status_gate.json","w",encoding="utf-8").write(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(payload,ensure_ascii=False,indent=2))

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if mode=="binance": binance()
    elif mode=="gate": gate()
    else: raise SystemExit("usage: research_gap_audit.py binance|gate")

if __name__=="__main__": main()
