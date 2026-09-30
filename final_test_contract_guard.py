#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed guard for the canonical 2026-10-07 final-test contract."""
from __future__ import annotations
import hashlib, json, subprocess, sys
from datetime import datetime, timezone

LOCK="final_test_contract_lock.json"
MANIFEST="genesis_freeze_manifest.json"
EXPECTED_CONTRACT_SHA256="93492e20a7c06988f0e65f03a953865f6b223fdf664c5d8721c797f7439af3c4"
EXPECTED_CONTRACT_COMMIT="dadada65ed6cb7a16c6ae7ca3d0c7665db55ae5a"

def git_bytes(ref,path):
    p=subprocess.run(["git","show",f"{ref}:{path}"],text=False,capture_output=True)
    return p.stdout if p.returncode==0 else None

def main():
    lock=json.load(open(LOCK,encoding="utf-8"))
    manifest=json.load(open(MANIFEST,encoding="utf-8"))
    path=lock["canonical_file"]
    data=open(path,"rb").read()
    actual=hashlib.sha256(data).hexdigest()
    lock_ok=(
        lock.get("canonical_sha256")==EXPECTED_CONTRACT_SHA256
        and lock.get("canonical_commit")==EXPECTED_CONTRACT_COMMIT
    )
    hash_ok=(actual==EXPECTED_CONTRACT_SHA256 and lock_ok)
    baseline=manifest["baseline_git_commit"]
    changed=[]
    missing=[]
    for p in manifest["contamination_sensitive_files"]:
        base=git_bytes(baseline,p)
        try: cur=open(p,"rb").read()
        except OSError:
            cur=None
        if base is None or cur is None:
            missing.append(p); continue
        if base!=cur: changed.append(p)
    now=datetime.now(timezone.utc)
    holdout=datetime.fromisoformat(lock["holdout_start_utc"].replace("Z","+00:00"))
    phase="PRE_HOLDOUT" if now<holdout else "HOLDOUT"
    status={
      "phase":phase,
      "canonical_file":path,
      "expected_sha256":EXPECTED_CONTRACT_SHA256,
      "lock_metadata_ok":lock_ok,
      "actual_sha256":actual,
      "contract_hash_ok":hash_ok,
      "baseline_git_commit":baseline,
      "changed_sensitive_files":changed,
      "missing_sensitive_files":missing,
      "status":"CLEAN" if hash_ok and not changed and not missing else "BLOCKED"
    }
    print(json.dumps(status,ensure_ascii=False,indent=2))
    # Pre-holdout drift is a hard failure too: reseal/revert before start.
    return 0 if status["status"]=="CLEAN" else 3

if __name__=="__main__":
    raise SystemExit(main())
