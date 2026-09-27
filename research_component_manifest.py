#!/usr/bin/env python3
"""Append-only manifest of active research components and their policy class."""
import hashlib, os, re, sqlite3, subprocess, sys
from datetime import datetime, timezone, timedelta
from research_windows import DISCOVERY_END_UTC, CALIBRATION_END_UTC, PURGE_HOURS, EMBARGO_HOURS

VERSION="component-manifest-v1-20260927"

COMPONENTS={
 "binance":[
  ("binance_scanner.py","FROZEN_CORE"),
  ("binance_opportunity_observer.py","OBSERVATIONAL_MUTABLE"),
  ("binance_missed_mover_learning.py","OBSERVATIONAL_MUTABLE"),
  ("binance_winner_bridge.py","OBSERVATIONAL_MUTABLE"),
  ("binance_history_context_bridge.py","OBSERVATIONAL_MUTABLE"),
  ("binance_evidence_research.py","DERIVED_RESEARCH"),
  ("trade_readiness_layer.py","DERIVED_RESEARCH"),
  ("decision_quality_layer.py","DERIVED_RESEARCH"),
  ("research_validation_layer.py","VALIDATION_GOVERNANCE"),
  ("capital_trust_gate.py","CAPITAL_GOVERNANCE"),
 ],
 "gate":[
  ("scanner.py","FROZEN_CORE"),
  ("gate_spot_watch.py","OBSERVATIONAL_MUTABLE"),
  ("gate_opportunity_observer.py","OBSERVATIONAL_MUTABLE"),
  ("gate_missed_mover_learning.py","OBSERVATIONAL_MUTABLE"),
  ("gate_history_bridge.py","OBSERVATIONAL_MUTABLE"),
  ("gate_evidence_research.py","DERIVED_RESEARCH"),
  ("gate_security_calibration.py","DERIVED_RESEARCH"),
  ("trade_readiness_layer.py","DERIVED_RESEARCH"),
  ("decision_quality_layer.py","DERIVED_RESEARCH"),
  ("research_validation_layer.py","VALIDATION_GOVERNANCE"),
  ("capital_trust_gate.py","CAPITAL_GOVERNANCE"),
 ],
}

def dt(x): return datetime.fromisoformat(x.replace("Z","+00:00"))
def phase(t):
    d=dt(DISCOVERY_END_UTC); k=dt(CALIBRATION_END_UTC)
    if t < d-timedelta(hours=PURGE_HOURS): return "DISCOVERY"
    if t < d+timedelta(hours=EMBARGO_HOURS): return "PURGED_EMBARGO"
    if t < k-timedelta(hours=PURGE_HOURS): return "CALIBRATION"
    if t < k+timedelta(hours=EMBARGO_HOURS): return "PURGED_EMBARGO"
    return "FINAL_TEST"

def declared_version(text):
    for name in ("CONFIG_VERSION","VERSION","BRIDGE_VERSION","RESEARCH_VERSION"):
        m=re.search(r'^'+name+r'\s*=\s*["\']([^"\']+)["\']',text,re.M)
        if m:return m.group(1)
    return None

def git_sha():
    try:return subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    except Exception:return os.getenv("GITHUB_SHA","")

def main():
    source=(sys.argv[1] if len(sys.argv)>1 else "").lower()
    if source not in COMPONENTS: raise SystemExit("usage: research_component_manifest.py binance|gate")
    db=os.getenv("BINANCE_DB","binance_avci2.db") if source=="binance" else os.getenv("AVCI_DB","avci2.db")
    if not os.path.exists(db): return
    now=datetime.now(timezone.utc)
    with sqlite3.connect(db,timeout=60) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS research_component_manifest(
          source TEXT NOT NULL, observed_at_utc TEXT NOT NULL, phase TEXT NOT NULL,
          component TEXT NOT NULL, policy_class TEXT NOT NULL,
          file_sha256 TEXT NOT NULL, declared_version TEXT, git_sha TEXT,
          manifest_version TEXT NOT NULL,
          PRIMARY KEY(source,observed_at_utc,component)
        )""")
        for path,policy in COMPONENTS[source]:
            try:
                raw=open(path,"rb").read()
            except OSError:
                continue
            text=raw.decode("utf-8","replace")
            c.execute("""INSERT OR IGNORE INTO research_component_manifest VALUES(?,?,?,?,?,?,?,?,?)""",
              (source.upper(),now.isoformat(),phase(now),path,policy,
               hashlib.sha256(raw).hexdigest(),declared_version(text),git_sha(),VERSION))
        c.commit()
    print(f"component manifest | {source} | phase={phase(now)} | components={len(COMPONENTS[source])}")

if __name__=="__main__":main()
