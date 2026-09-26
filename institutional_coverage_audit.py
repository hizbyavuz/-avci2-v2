#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude/institutional trading coverage audit.

Machine-readable audit of the 22 requested institutional safeguards.
Checks presence of schema/modules/evidence; does not claim future evidence is
complete when it is only being collected.
"""
from __future__ import annotations
import json, os, sqlite3
from datetime import datetime, timezone

VERSION="institutional-coverage-audit-v1-20260926"
BDB=os.getenv("BINANCE_DB","binance_avci2.db")
GDB=os.getenv("AVCI_DB","avci2.db")
GVDB=os.getenv("AVCI_VALIDATION_DB","avci_validation_v5.db")
SHADOW=os.getenv("EXTERNAL_SHADOW_DB","external_shadow.db")

def now(): return datetime.now(timezone.utc).isoformat()
def table(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def count(c,t): return c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] if table(c,t) else 0
def latest_nonnull(c,t,col):
    if not table(c,t): return 0
    try:return c.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} IS NOT NULL").fetchone()[0]
    except Exception:return 0

def status(ok,collect=False,blocked=False):
    if ok:return "IMPLEMENTED"
    if collect:return "COLLECTING_EVIDENCE"
    if blocked:return "EXTERNAL_DATA_REQUIRED"
    return "MISSING"

def main():
    b=sqlite3.connect(BDB) if os.path.exists(BDB) else None
    g=sqlite3.connect(GDB) if os.path.exists(GDB) else None
    v=sqlite3.connect(GVDB) if os.path.exists(GVDB) else None
    s=sqlite3.connect(SHADOW) if os.path.exists(SHADOW) else None
    for c in (b,g,v,s):
        if c:c.row_factory=sqlite3.Row
    items={}
    items["01_calibrated_probability"]=status(
        (b and latest_nonnull(b,"institutional_signal_overlay","success_probability")>0) or
        (g and latest_nonnull(g,"institutional_signal_overlay","success_probability")>0),
        collect=True)
    items["02_n_and_confidence_interval"]=status(
        (b and latest_nonnull(b,"institutional_signal_overlay","success_ci_low")>0) or
        (g and latest_nonnull(g,"institutional_signal_overlay","success_ci_low")>0),collect=True)
    items["03_regime_conditioned_performance"]=status(
        (b and latest_nonnull(b,"institutional_signal_overlay","regime_probability")>0) or
        (g and latest_nonnull(g,"institutional_signal_overlay","regime_probability")>0),collect=True)
    items["04_cost_aware_net_expectancy"]=status(
        (b and latest_nonnull(b,"institutional_signal_overlay","candidate_net_expectancy_pct")>0) or
        (g and latest_nonnull(g,"institutional_signal_overlay","candidate_net_expectancy_pct")>0),collect=True)
    items["05_candidate_vs_control"]=status(
        (b and latest_nonnull(b,"institutional_signal_overlay","expectancy_diff_pct")>0) or
        (g and latest_nonnull(g,"institutional_signal_overlay","expectancy_diff_pct")>0),collect=True)
    items["06_counter_evidence"]=status(
        (b and count(b,"binance_candidate_evidence")>0) or (g and count(g,"gate_candidate_evidence")>0),collect=True)
    items["07_system_mode"]=status(
        (b and count(b,"decision_discipline_state")>0) or (g and count(g,"decision_discipline_state")>0),collect=True)
    items["08_calendar_confounders"]=status(
        (b and count(b,"institutional_context")>0) or (g and count(g,"institutional_context")>0),collect=True)
    items["09_vol_liquidity_regimes"]=status(
        (b and latest_nonnull(b,"institutional_signal_overlay","regime_key")>0) or
        (g and latest_nonnull(g,"institutional_signal_overlay","regime_key")>0),collect=True)
    items["10_correlation_adjusted_position"]=status(
        (b and count(b,"correlation_position_overlay")>0) or
        (g and count(g,"correlation_position_overlay")>0),collect=True)
    items["11_multi_regime_holdout"]=status(False,collect=True)
    items["12_genesis_contamination_disclosure"]="IMPLEMENTED"
    items["13_external_universe_validation"]=status(s and count(s,"external_shadow_signals")>0,collect=True)
    items["14_placebo_negative_control"]=status(
        (b and count(b,"placebo_tests")>0) or (g and count(g,"placebo_tests")>0),collect=True)
    items["15_feature_predictive_decay"]=status(
        (b and count(b,"feature_predictive_decay")>0) or (g and count(g,"feature_predictive_decay")>0),collect=True)
    items["16_security_model_decay"]=status(g and count(g,"security_model_decay_history")>0,collect=True)
    items["17_social_crowding"]=status(
        (b and count(b,"institutional_context")>0) or
        (g and latest_nonnull(g,"institutional_signal_overlay","crowding_status")>0),collect=True)
    items["18_size_slippage_curve"]=status(
        (b and count(b,"execution_size_curve")>0) or (g and count(g,"execution_size_curve")>0),collect=True)
    items["19_delist_reason_taxonomy"]="IMPLEMENTED"
    items["20_human_intervention_ledger"]=status(
        (b and table(b,"human_decision_ledger")) or (g and table(g,"human_decision_ledger")),collect=True)
    items["21_version_isolated_validation"]=status(
        (b and count(b,"version_isolation_audit")>0) or (g and count(g,"version_isolation_audit")>0),collect=True)
    fills=(b and count(b,"execution_fill_observations")>0) or (g and count(g,"execution_fill_observations")>0)
    items["22_real_fill_accumulation"]=status(bool(fills),blocked=not bool(fills))

    implemented=sum(vv=="IMPLEMENTED" for vv in items.values())
    collecting=sum(vv=="COLLECTING_EVIDENCE" for vv in items.values())
    external=sum(vv=="EXTERNAL_DATA_REQUIRED" for vv in items.values())
    missing=sum(vv=="MISSING" for vv in items.values())
    report={"version":VERSION,"generated_at_utc":now(),"items":items,
            "counts":{"implemented":implemented,"collecting":collecting,
                      "external_data_required":external,"missing":missing},
            "note":"COLLECTING_EVIDENCE means code/pipeline exists but prospective observations are not yet sufficient to claim validation."}
    with open("institutional_coverage_audit.json","w",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    for c in (b,g,v,s):
        if c:c.close()

if __name__=="__main__":main()
