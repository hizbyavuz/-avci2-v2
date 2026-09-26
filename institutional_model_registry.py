#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Institutional model inventory / registry.

Explicitly records intended use, validation state, limitations and dependencies
for every material Avci model or rule engine. Research-only.
"""
from __future__ import annotations
import json, os, hashlib
from datetime import datetime, timezone

VERSION="model-registry-v1-20260926"
MODELS=[
 {"id":"BINANCE_V1_FROZEN","kind":"signal_rules","intended_use":"paper candidate selection",
  "production_use":"RESEARCH_ONLY","frozen":True,
  "validation":"PROSPECTIVE_GENESIS_HOLDOUT_PENDING",
  "limitations":["genesis contamination cannot be removed retroactively","futures may degrade to SPOT_ONLY"],
  "dependencies":["binance_scanner.py","binance_live_pool.py","research_validation_spec.json"]},
 {"id":"GATE_V5_FROZEN","kind":"signal_rules","intended_use":"paper on-chain candidate selection",
  "production_use":"RESEARCH_ONLY","frozen":True,
  "validation":"PROSPECTIVE_GENESIS_HOLDOUT_PENDING",
  "limitations":["security/execution coverage varies by chain/provider","historical universe remains partially reconstructable"],
  "dependencies":["scanner.py","gate_evidence_research.py","research_validation_spec.json"]},
 {"id":"INSTITUTIONAL_SIGNAL_OVERLAY","kind":"calibration_reporting",
  "intended_use":"probability/CI/regime/expectancy reporting only","production_use":"REPORTING_ONLY","frozen":False,
  "validation":"OBSERVATIONAL","limitations":["probability unavailable when N is small","not a live-entry model"],
  "dependencies":["institutional_signal_overlay.py"]},
 {"id":"TRADE_READINESS","kind":"risk_gate","intended_use":"paper readiness classification",
  "production_use":"PAPER_ONLY","frozen":False,"validation":"OBSERVATIONAL",
  "limitations":["does not replace portfolio/global governance"],"dependencies":["trade_readiness_layer.py"]},
 {"id":"GLOBAL_GOVERNANCE","kind":"portfolio_risk","intended_use":"combined Binance/Gate paper portfolio risk",
  "production_use":"PAPER_ONLY","frozen":False,"validation":"OBSERVATIONAL",
  "limitations":["sector taxonomy incomplete","beta is backward-looking"],"dependencies":["avci_global_governance.py"]},
 {"id":"SECURITY_REDTEAM","kind":"security_validation","intended_use":"measure security recall/FPR",
  "production_use":"VALIDATION_ONLY","frozen":False,"validation":"EXTERNAL_CORPUS_REPLAY",
  "limitations":["EVM labels stronger than Solana ground truth","adversarial techniques evolve"],"dependencies":["security_redteam.py","external_security_replay.py"]},
 {"id":"EXTERNAL_SHADOW","kind":"external_validation","intended_use":"independent venue shadow validation",
  "production_use":"VALIDATION_ONLY","frozen":True,"validation":"COLLECTING",
  "limitations":["wake-up core only","does not recreate full Binance/Gate stack"],"dependencies":["external_shadow_universe.py"]},
]

def digest(path):
    try:
        with open(path,"rb") as f:return hashlib.sha256(f.read()).hexdigest()
    except Exception:return None

def main():
    stamp=datetime.now(timezone.utc).isoformat()
    rows=[]
    for m in MODELS:
        dep=[{"path":p,"sha256":digest(p)} for p in m["dependencies"]]
        rows.append({**m,"dependency_state":dep,"registry_version":VERSION,"recorded_at_utc":stamp})
    report={"version":VERSION,"generated_at_utc":stamp,"models":rows,
            "policy":{"new_signal_logic_requires_new_model_id":True,
                      "validation_results_do_not_transfer_across_model_ids":True,
                      "production_use_must_match_intended_use":True,
                      "limitations_must_be_visible":True}}
    with open("institutional_model_registry.json","w",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
